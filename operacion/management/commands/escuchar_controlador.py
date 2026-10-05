"""Servicio de eventos: persiste lo que el controlador informa (ADR-0009).

El controlador no toca MySQL (ADR-0002). Lo que pasa en el arreglo y tiene que
quedar registrado —marcas grabadas, checkpoints, alertas, emergencias, el fin
del programa con su espectro— lo emite al stream ``co2:eventos``, y este proceso
lo lee y lo guarda con el ORM.

Lee como parte de un **grupo de consumo**: si el proceso se cae a mitad de un
ensayo, al volver retoma los eventos que no llego a confirmar. La telemetria,
en cambio, no pasa por aca: es efimera (ADR-0007).

Ademas publica en Redis los umbrales de la configuracion vigente, que es de
donde los lee el controlador.

    docker compose exec api python manage.py escuchar_controlador

En el compose corre como su propio servicio (``eventos``).
"""

from __future__ import annotations

import json
import logging
import signal
import statistics
import time
from pathlib import Path

import redis
from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction

from comun.enums import EstadoEjecucion, EstadoRed, Severidad, nombre_de_variable
from controller import protocolo as p
from fabricacion.models import Marca
from operacion import control
from operacion.models import Alerta, Configuracion, EventoEmergencia
from trazabilidad.models import RegistroDeFabricacion

logger = logging.getLogger("eventos")

ARCHIVO_VIVO = Path("/tmp/eventos.alive")
CONSUMIDOR = "eventos-1"
INTERVALO_CONFIGURACION_S = 5

# Una red se da por viable si su resonancia principal cae al menos esto por
# debajo de la linea de base. Criterio de partida, a validar con el CIOp.
PROFUNDIDAD_VIABLE_DB = 5.0


def _registro(codigo: str | None) -> RegistroDeFabricacion | None:
    if not codigo:
        return None
    registro = (
        RegistroDeFabricacion.objects.select_related("red", "procedimiento", "programa")
        .filter(codigo=codigo)
        .first()
    )
    if registro is None:
        logger.warning("Evento para un registro inexistente: %s", codigo)
    return registro


# ── Manejadores ──────────────────────────────────────────────────────────────


def al_grabar_marca(datos: dict) -> None:
    """Asienta una marca grabada. Es idempotente ante una entrega repetida."""
    registro = _registro(datos.get("registro"))
    if registro is None or registro.red is None:
        return
    Marca.objects.get_or_create(
        red=registro.red,
        numero=datos["numero"],
        defaults={
            "posicion": datos["posicion_um"],
            "cant_pulsos": datos["cant_pulsos"],
            "ciclo_trabajo": datos["ciclo_trabajo"],
            "tiempo_de_pulso": datos["tiempo_de_pulso"],
        },
    )


def al_registrar_checkpoint(datos: dict) -> None:
    """Asienta un checkpoint con las lecturas de ese instante (ADR-0007)."""
    registro = _registro(datos.get("registro"))
    if registro is None:
        return
    registro.registrar_checkpoint(
        tipo=datos["tipo"],
        estado_sistema=datos["estado_sistema"],
        paso=registro.programa.pasos.first(),
        valores=datos.get("valores", {}),
    )


def al_exceder_umbral(datos: dict) -> None:
    """Genera la alerta del umbral excedido."""
    variable, valor = datos["variable"], datos["valor"]
    vigente = Configuracion.objects.filter(activa=True).first()
    umbral = vigente.umbrales.filter(variable=variable).first() if vigente else None
    if umbral is not None:
        umbral.disparar(valor)
        return
    # El controlador uso sus umbrales por defecto: la alerta se asienta igual.
    Alerta.objects.create(
        descripcion=f"{nombre_de_variable(variable)} = {valor} fuera de rango.",
        severidad=Severidad.CRITICA,
        valor_medido=valor,
    )


def al_parar_de_emergencia(datos: dict) -> None:
    """Asienta la parada e interrumpe el ensayo que estuviera en curso."""
    registro = _registro(datos.get("registro"))
    EventoEmergencia.objects.create(
        origen=datos["origen"],
        descripcion=datos.get("descripcion", ""),
        tiempo_respuesta_ms=datos["tiempo_respuesta_ms"],
        registro=registro,
    )
    if registro is not None and registro.estado == EstadoEjecucion.EN_CURSO:
        registro.interrumpir(datos.get("descripcion", "Parada de emergencia"))
        if registro.red is not None:
            registro.red.estado = EstadoRed.INVIABLE
            registro.red.save(update_fields=["estado"])


def al_terminar(datos: dict) -> None:
    """Cierra el ensayo y caracteriza la red con el espectro medido.

    La viabilidad se decide por la profundidad de la resonancia principal
    respecto de la linea de base (mediana del espectro).
    """
    registro = _registro(datos.get("registro"))
    if registro is None or registro.estado != EstadoEjecucion.EN_CURSO:
        return
    registro.finalizar()
    espectro = datos.get("espectro")
    if registro.red is None or not espectro:
        return
    filas = zip(espectro["longitudes_onda"], espectro["transmitancias"], strict=True)
    csv = "\n".join(f"{nm},{db}" for nm, db in filas)
    # Un registro sin procedimiento asignado no puede dejar la red sin
    # caracterizar: el evento se confirma igual y no se volveria a procesar.
    procedimiento = registro.procedimiento or control.procedimiento_del_arreglo()
    procedimiento.importar(registro.red, csv).detectar_resonancias()
    principal = registro.red.resonancia_principal()
    base = statistics.median(espectro["transmitancias"])
    profundidad = base - principal.transmitancia if principal else 0.0
    registro.red.estado = (
        EstadoRed.VIABLE if profundidad >= PROFUNDIDAD_VIABLE_DB else EstadoRed.INVIABLE
    )
    registro.red.save(update_fields=["estado"])
    if principal is None:
        registro.observaciones = (
            f"{registro.observaciones}\nSin resonancia dentro de la ventana del OSA.".strip()
        )
        registro.save(update_fields=["observaciones"])


def al_abortar(datos: dict) -> None:
    """Cierra el ensayo como abortado."""
    registro = _registro(datos.get("registro"))
    if registro is None or registro.estado != EstadoEjecucion.EN_CURSO:
        return
    registro.abortar(datos.get("motivo", "Abortado."))
    if registro.red is not None and datos.get("marcas"):
        registro.red.estado = EstadoRed.INVIABLE
        registro.red.save(update_fields=["estado"])


MANEJADORES = {
    p.EV_MARCA: al_grabar_marca,
    p.EV_CHECKPOINT: al_registrar_checkpoint,
    p.EV_ALERTA: al_exceder_umbral,
    p.EV_EMERGENCIA: al_parar_de_emergencia,
    p.EV_FIN: al_terminar,
    p.EV_ABORTADO: al_abortar,
}


def procesar(tipo: str, datos: dict) -> None:
    """Despacha un evento a su manejador, en una transaccion.

    Args:
        tipo: Tipo de evento (``controller.protocolo.EV_*``).
        datos: Su contenido.
    """
    manejador = MANEJADORES.get(tipo)
    if manejador is None:
        logger.warning("Evento desconocido: %s", tipo)
        return
    with transaction.atomic():
        manejador(datos)


# ── Comando ──────────────────────────────────────────────────────────────────


class Command(BaseCommand):
    """Escucha los eventos del controlador y los persiste."""

    help = "Persiste los eventos del controlador y le publica la configuracion vigente."

    def handle(self, *args, **opciones) -> None:
        """Corre hasta recibir SIGTERM o SIGINT."""
        self._parar = False
        signal.signal(signal.SIGTERM, self._detener)
        signal.signal(signal.SIGINT, self._detener)

        # El XREADGROUP bloquea hasta 2 s: el socket necesita mas margen que eso.
        cliente = control.bus(timeout=10)
        try:
            cliente.xgroup_create(p.STREAM_EVENTOS, p.GRUPO_EVENTOS, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        self.stdout.write("Escuchando eventos del controlador...")
        # Primero los pendientes de una corrida anterior ("0"), despues los nuevos (">").
        desde = "0"
        ultima_config = 0.0
        while not self._parar:
            ARCHIVO_VIVO.touch()
            close_old_connections()
            if time.monotonic() - ultima_config >= INTERVALO_CONFIGURACION_S:
                try:
                    control.publicar_configuracion()
                except redis.RedisError as exc:
                    logger.warning("No se pudo publicar la configuracion: %s", exc)
                ultima_config = time.monotonic()
            try:
                respuesta = cliente.xreadgroup(
                    p.GRUPO_EVENTOS, CONSUMIDOR, {p.STREAM_EVENTOS: desde}, count=50, block=2000
                )
            except redis.RedisError as exc:
                logger.warning("No se pudo leer el stream de eventos: %s", exc)
                time.sleep(1)
                continue
            mensajes = respuesta[0][1] if respuesta else []
            if desde == "0" and not mensajes:
                desde = ">"
                continue
            for id_mensaje, campos in mensajes:
                tipo = campos.get(b"tipo", b"").decode()
                try:
                    procesar(tipo, json.loads(campos.get(b"datos", b"{}")))
                except Exception:
                    # Un evento que no se puede procesar se registra y se confirma
                    # igual: reintentarlo para siempre bloquearia los siguientes.
                    logger.exception("Fallo al procesar el evento %s %s", tipo, id_mensaje)
                cliente.xack(p.STREAM_EVENTOS, p.GRUPO_EVENTOS, id_mensaje)
        self.stdout.write("Servicio de eventos detenido.")

    def _detener(self, *_args) -> None:
        self._parar = True
