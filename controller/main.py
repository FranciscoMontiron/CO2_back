"""Proceso controlador: lazo de control, E-Stop de software y telemetria.

Este proceso es el dueno exclusivo del hardware (ADR-0002). Django no accede al
hardware: ni para escribir, ni para leer. Se hablan por Redis (ADR-0004), con el
contrato de ``controller/protocolo.py``:

* **Comandos** — los lee del stream ``co2:comandos`` y se los pasa a la maquina.
* **Telemetria** — la publica a ``CONTROLLER_TELEMETRY_HZ`` (1 Hz, RF009) por
  pub/sub, y ademas fuera de turno cada vez que cambia el estado. Es efimera:
  no se persiste ninguna muestra (ADR-0007).
* **Eventos** — marcas, checkpoints, alertas, emergencias y fin de programa van
  al stream ``co2:eventos``. Los persiste el servicio ``eventos`` (Django): este
  proceso nunca toca MySQL.
* **Configuracion** — lee de Redis los umbrales vigentes que publica Django.

Con ``CONTROLLER_HAL=simulado`` el hardware es el modelo fisico de
``simulador.py``. El driver de GPIO para la Pi 5 va detras de la misma
interfaz (#22).

Lo que NO va a vivir aca: nada que necesite Django, el ORM o la base de datos.
Cada dependencia que se agregue a este proceso es latencia y jitter que el
presupuesto de RNF001 no puede pagar.
"""

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path

import redis.asyncio as redis

from controller import protocolo as p
from controller.maquina import Maquina
from controller.simulador import HalSimulado

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("controller")

# Archivo que lee el healthcheck del contenedor. Es intencionalmente distinto
# del heartbeat de Redis: este dice "el proceso arranco", el de Redis dice "el
# lazo sigue girando". Un proceso vivo con el lazo colgado es justo el modo de
# falla que hay que poder distinguir.
ARCHIVO_VIVO = Path("/tmp/controller.alive")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
REDIS_DB_PUBSUB = int(os.environ.get("REDIS_DB_PUBSUB", "0"))
REDIS_DB_HEARTBEAT = int(os.environ.get("REDIS_DB_HEARTBEAT", "2"))
HEARTBEAT_TTL = int(os.environ.get("CONTROLLER_HEARTBEAT_TTL", "3"))
HAL = os.environ.get("CONTROLLER_HAL", "simulado")
LOOP_HZ = int(os.environ.get("CONTROLLER_LOOP_HZ", "100"))
TELEMETRY_HZ = float(os.environ.get("CONTROLLER_TELEMETRY_HZ", "1"))
# Factor de aceleracion del tiempo simulado. 1 = tiempo real, como en el
# laboratorio. Subirlo sirve para demos largas; no tiene efecto con hardware real.
VELOCIDAD = float(os.environ.get("CONTROLLER_SIM_VELOCIDAD", "1"))

CLAVE_HEARTBEAT = "controller:heartbeat"


async def publicar_heartbeat(cliente: redis.Redis, parar: asyncio.Event) -> None:
    """Renueva la clave de heartbeat en Redis mientras el proceso viva.

    Se renueva a un tercio del TTL para tolerar un ciclo perdido sin que el
    front declare al controlador caido por un hipo momentaneo.

    Args:
        cliente: Conexion a la base de heartbeat de Redis.
        parar: Evento que corta el bucle en un apagado ordenado.
    """
    intervalo = max(HEARTBEAT_TTL / 3, 0.5)
    while not parar.is_set():
        try:
            await cliente.setex(CLAVE_HEARTBEAT, HEARTBEAT_TTL, "1")
            ARCHIVO_VIVO.touch()
        except Exception as exc:  # noqa: BLE001 - reintentar es preferible a morir
            logger.warning("No se pudo publicar el heartbeat: %s", exc)
        try:
            await asyncio.wait_for(parar.wait(), timeout=intervalo)
        except TimeoutError:
            continue


async def lazo(maquina: Maquina, parar: asyncio.Event) -> None:
    """Corre el lazo de control a ``LOOP_HZ``.

    El paso de la fisica es el tiempo real transcurrido, escalado por la
    velocidad de la simulacion: si un ciclo se atrasa, el siguiente lo compensa
    en lugar de que la simulacion se desfase del reloj.

    Args:
        maquina: La maquina de estados.
        parar: Evento de apagado.
    """
    periodo = 1 / LOOP_HZ
    anterior = time.monotonic()
    while not parar.is_set():
        ahora = time.monotonic()
        maquina.tick((ahora - anterior) * maquina.velocidad)
        anterior = ahora
        await asyncio.sleep(periodo)


async def publicar_telemetria(cliente: redis.Redis, maquina: Maquina, parar: asyncio.Event) -> None:
    """Publica la telemetria a ``TELEMETRY_HZ``, y fuera de turno si algo cambio.

    Ademas deja la ultima en una clave con TTL corto: la API la consulta para
    validar comandos, y si el controlador muere la clave vence sola.

    Args:
        cliente: Conexion a la base de pub/sub.
        maquina: La maquina de estados.
        parar: Evento de apagado.
    """
    intervalo = 1 / TELEMETRY_HZ
    ultimo = 0.0
    while not parar.is_set():
        ahora = time.monotonic()
        if maquina.hubo_cambio or ahora - ultimo >= intervalo:
            maquina.hubo_cambio = False
            ultimo = ahora
            mensaje = json.dumps(maquina.telemetria(TELEMETRY_HZ))
            try:
                await cliente.publish(p.CANAL_TELEMETRIA, mensaje)
                await cliente.setex(p.CLAVE_TELEMETRIA, p.TTL_TELEMETRIA, mensaje)
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudo publicar la telemetria: %s", exc)
        await asyncio.sleep(0.05)


async def escuchar_comandos(cliente: redis.Redis, maquina: Maquina, parar: asyncio.Event) -> None:
    """Lee comandos del stream y se los pasa a la maquina.

    Arranca desde ``$``: solo comandos que lleguen **despues** de que el proceso
    arranco. Un controlador que se reinicia no puede ejecutar ordenes viejas que
    quedaron en el stream, como encender un laser que alguien pidio hace una hora.

    Args:
        cliente: Conexion a la base de pub/sub.
        maquina: La maquina de estados.
        parar: Evento de apagado.
    """
    ultimo_id = "$"
    while not parar.is_set():
        try:
            respuesta = await cliente.xread({p.STREAM_COMANDOS: ultimo_id}, block=1000, count=10)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo leer el stream de comandos: %s", exc)
            await asyncio.sleep(1)
            continue
        for _stream, mensajes in respuesta or []:
            for id_mensaje, campos in mensajes:
                ultimo_id = id_mensaje
                try:
                    comando = json.loads(campos[b"datos"])
                except (KeyError, json.JSONDecodeError):
                    logger.warning("Comando mal formado: %r", campos)
                    continue
                logger.info("comando: %s", comando.get("accion"))
                await maquina.manejar(comando)


async def despachar_eventos(
    cliente: redis.Redis, cola: asyncio.Queue, parar: asyncio.Event
) -> None:
    """Escribe en el stream de eventos lo que emite la maquina.

    Args:
        cliente: Conexion a la base de pub/sub.
        cola: Eventos pendientes ``(tipo, datos)``.
        parar: Evento de apagado.
    """
    while not parar.is_set() or not cola.empty():
        try:
            tipo, datos = await asyncio.wait_for(cola.get(), timeout=0.5)
        except TimeoutError:
            continue
        try:
            await cliente.xadd(
                p.STREAM_EVENTOS,
                {"tipo": tipo, "datos": json.dumps(datos)},
                maxlen=p.MAXLEN_STREAM,
                approximate=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Se perdio un evento %s: %s", tipo, exc)


async def leer_configuracion(cliente: redis.Redis, maquina: Maquina, parar: asyncio.Event) -> None:
    """Actualiza los umbrales con la configuracion vigente que publica Django.

    Si la clave no existe todavia, la maquina sigue con sus umbrales por
    defecto: nunca corre sin proteccion.

    Args:
        cliente: Conexion a la base de pub/sub.
        maquina: La maquina de estados.
        parar: Evento de apagado.
    """
    while not parar.is_set():
        try:
            crudo = await cliente.get(p.CLAVE_CONFIGURACION)
            if crudo:
                umbrales = [u for u in json.loads(crudo) if u.get("activo", True)]
                if umbrales:
                    maquina.umbrales = umbrales
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo leer la configuracion: %s", exc)
        try:
            await asyncio.wait_for(parar.wait(), timeout=2)
        except TimeoutError:
            continue


async def main() -> None:
    """Arranca el proceso controlador y espera la senal de apagado."""
    logger.info("Controlador iniciando")
    logger.info("  HAL ................ %s", HAL)
    logger.info("  lazo de control .... %d Hz", LOOP_HZ)
    logger.info("  telemetria ......... %s Hz", TELEMETRY_HZ)
    logger.info("  velocidad sim ...... x%s", VELOCIDAD)
    logger.info("  heartbeat TTL ...... %d s", HEARTBEAT_TTL)

    if HAL != "simulado":
        logger.warning(
            "CONTROLLER_HAL=%s pero el driver de GPIO todavia no esta implementado (#22). "
            "Se usa el HAL simulado.",
            HAL,
        )

    cola: asyncio.Queue = asyncio.Queue()
    maquina = Maquina(
        HalSimulado(), emitir=lambda t, d: cola.put_nowait((t, d)), velocidad=VELOCIDAD
    )

    latido = redis.Redis.from_url(f"{REDIS_URL}/{REDIS_DB_HEARTBEAT}")
    bus = redis.Redis.from_url(f"{REDIS_URL}/{REDIS_DB_PUBSUB}")
    parar = asyncio.Event()

    bucle = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        bucle.add_signal_handler(sig, parar.set)

    tareas = [
        asyncio.create_task(publicar_heartbeat(latido, parar)),
        asyncio.create_task(lazo(maquina, parar)),
        asyncio.create_task(publicar_telemetria(bus, maquina, parar)),
        asyncio.create_task(escuchar_comandos(bus, maquina, parar)),
        asyncio.create_task(despachar_eventos(bus, cola, parar)),
        asyncio.create_task(leer_configuracion(bus, maquina, parar)),
    ]
    await parar.wait()

    logger.info("Senal de apagado recibida, cerrando")
    # Apagado ordenado del hardware: shutter, laser y alta tension, manteniendo
    # la refrigeracion (el tubo sigue caliente).
    maquina.hal.shutter_abierto = False
    maquina.hal.laser = False
    maquina.hal.alta_tension = False
    await asyncio.wait(tareas, timeout=3)
    for tarea in tareas:
        tarea.cancel()
    await latido.delete(CLAVE_HEARTBEAT)
    await latido.aclose()
    await bus.aclose()
    ARCHIVO_VIVO.unlink(missing_ok=True)
    logger.info("Controlador detenido")


if __name__ == "__main__":
    asyncio.run(main())
