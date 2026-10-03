"""Proceso controlador — ESQUELETO, sin logica de control todavia.

Este proceso es el dueño exclusivo del GPIO y el responsable del lazo de
control y del E-Stop de software (ADR-0002). Django no accede al hardware:
ni para escribir, ni para leer.

Estado actual: solo publica el heartbeat que el resto del sistema usa para
saber si el controlador esta vivo. La logica de control se implementa cuando
se cierre el Diagrama de Clases v4.

Lo que va a vivir aca:

* HAL con dos implementaciones (``simulado`` y ``gpio``) detras de la misma
  interfaz, seleccionadas por ``CONTROLLER_HAL``.
* Lazo de control a ``CONTROLLER_LOOP_HZ`` (100 Hz): lee sensores, evalua
  umbrales, actua.
* Cadena ``Umbral.evaluar() -> AccionUmbral.PARADA_EMERGENCIA ->
  Componente.cambiarEstado()`` del diagrama de clases, asentando el
  ``EventoEmergencia`` con su ``tiempoRespuestaMs`` (la evidencia de RNF001).
  El apagado seguro tiene orden -- shutter antes que la fuente HV -- y esa
  secuencia se resuelve aca, no en el modelo.
* Publicacion de telemetria a ``CONTROLLER_TELEMETRY_HZ`` (1 Hz, RF009) sobre
  el pub/sub de Redis (ADR-0004). Es **efimera**: no se persiste ninguna
  muestra, el front la consume en vivo y se descarta (ADR-0007).
* Suscripcion al canal de comandos que publica la API REST.

Lo que NO va a vivir aca: nada que necesite Django, el ORM o la base de datos.
Cada dependencia que se agregue a este proceso es latencia y jitter que el
presupuesto de RNF001 no puede pagar.
"""

import asyncio
import logging
import os
import signal
from pathlib import Path

import redis.asyncio as redis

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
REDIS_DB_HEARTBEAT = int(os.environ.get("REDIS_DB_HEARTBEAT", "2"))
HEARTBEAT_TTL = int(os.environ.get("CONTROLLER_HEARTBEAT_TTL", "3"))
HAL = os.environ.get("CONTROLLER_HAL", "simulado")
LOOP_HZ = int(os.environ.get("CONTROLLER_LOOP_HZ", "100"))
TELEMETRY_HZ = int(os.environ.get("CONTROLLER_TELEMETRY_HZ", "1"))

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


async def main() -> None:
    """Arranca el proceso controlador y espera la señal de apagado."""
    logger.info("Controlador iniciando")
    logger.info("  HAL ................ %s", HAL)
    logger.info("  lazo de control .... %d Hz", LOOP_HZ)
    logger.info("  telemetria ......... %d Hz", TELEMETRY_HZ)
    logger.info("  heartbeat TTL ...... %d s", HEARTBEAT_TTL)

    if HAL != "simulado":
        logger.warning(
            "CONTROLLER_HAL=%s pero el driver de GPIO todavia no esta "
            "implementado. Corriendo sin acceso a hardware.",
            HAL,
        )

    logger.warning(
        "ESQUELETO: el lazo de control y el E-Stop todavia no estan "
        "implementados. Este proceso solo publica el heartbeat."
    )

    cliente = redis.Redis.from_url(f"{REDIS_URL}/{REDIS_DB_HEARTBEAT}")
    parar = asyncio.Event()

    bucle = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        bucle.add_signal_handler(sig, parar.set)

    tarea = asyncio.create_task(publicar_heartbeat(cliente, parar))
    await parar.wait()

    logger.info("Señal de apagado recibida, cerrando")
    tarea.cancel()
    # El apagado ordenado del hardware (shutter -> laser -> HV, manteniendo la
    # refrigeracion) se implementa junto con el HAL.
    await cliente.delete(CLAVE_HEARTBEAT)
    await cliente.aclose()
    ARCHIVO_VIVO.unlink(missing_ok=True)
    logger.info("Controlador detenido")


if __name__ == "__main__":
    asyncio.run(main())
