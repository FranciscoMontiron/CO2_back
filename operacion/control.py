"""Puente entre Django y el proceso controlador, a traves de Redis.

Django no toca el hardware (ADR-0002): manda comandos al stream que lee el
controlador, y lee el estado de la ultima telemetria que el controlador deja en
Redis. Los nombres de canales y comandos salen de ``controller/protocolo.py``,
la misma fuente que usa el controlador.
"""

from __future__ import annotations

import json
import time

import redis
from django.conf import settings

from comun.enums import AccionAuditoria
from controller import protocolo as p
from usuarios.models import RegistroAuditoria


class ControladorNoDisponibleError(Exception):
    """El controlador no responde: su heartbeat vencio o Redis esta caido."""


def bus(timeout: float = 2) -> redis.Redis:
    """Conexion a la base de pub/sub y streams de Redis (ADR-0004).

    Args:
        timeout: Timeout del socket, en segundos. Quien haga lecturas
            bloqueantes tiene que pasar uno mayor que el tiempo de bloqueo, o el
            socket corta antes de que Redis responda.
    """
    return redis.Redis.from_url(
        f"{settings.REDIS_URL}/{settings.REDIS_DB_PUBSUB}", socket_timeout=timeout
    )


def controlador_vivo() -> bool:
    """Indica si el heartbeat del controlador esta vigente."""
    try:
        latido = redis.Redis.from_url(
            f"{settings.REDIS_URL}/{settings.REDIS_DB_HEARTBEAT}", socket_timeout=2
        )
        return latido.exists(settings.CONTROLLER_HEARTBEAT_KEY) == 1
    except redis.RedisError:
        return False


def ultima_telemetria() -> dict | None:
    """Devuelve la ultima telemetria publicada, o ``None`` si vencio.

    La clave tiene TTL de unos segundos: si el controlador murio, devuelve
    ``None`` en lugar de un estado viejo que pareceria actual.
    """
    try:
        crudo = bus().get(p.CLAVE_TELEMETRIA)
    except redis.RedisError:
        return None
    return json.loads(crudo) if crudo else None


def enviar(accion: str, datos: dict | None = None, usuario=None, ip: str | None = None) -> None:
    """Manda un comando al controlador y lo deja auditado (RN010).

    Args:
        accion: Un comando de ``controller.protocolo``.
        datos: Parametros del comando.
        usuario: Quien lo ordena.
        ip: Desde donde.

    Raises:
        ControladorNoDisponibleError: Si el controlador no responde. Un comando
            mandado a un controlador caido quedaria en el stream y se perderia:
            el que llamo tiene que saberlo en el momento.
    """
    if not controlador_vivo():
        raise ControladorNoDisponibleError("Sin conexion al controlador.")
    comando = {
        "accion": accion,
        **(datos or {}),
        # Se mide RNF001 desde aca: incluye API, Redis y controlador.
        "t_envio": int(time.time() * 1000),
        "usuario": getattr(usuario, "username", None),
    }
    try:
        bus().xadd(
            p.STREAM_COMANDOS,
            {"datos": json.dumps(comando)},
            maxlen=p.MAXLEN_STREAM,
            approximate=True,
        )
    except redis.RedisError as exc:
        raise ControladorNoDisponibleError(f"No se pudo hablar con el controlador: {exc}") from exc
    RegistroAuditoria.objects.create(
        usuario=usuario if getattr(usuario, "is_authenticated", False) else None,
        accion=AccionAuditoria.COMANDAR,
        entidad="controlador",
        codigo_entidad=accion,
        valor_nuevo=json.dumps(datos or {}, ensure_ascii=False),
        critica=accion in p.COMANDOS_CRITICOS,
        direccion_ip=ip,
    )


def umbrales_vigentes() -> list[dict]:
    """Umbrales de la configuracion activa, en el formato que espera el controlador."""
    from .models import Configuracion

    vigente = Configuracion.objects.filter(activa=True).first()
    if vigente is None:
        return []
    return [
        {
            "variable": u.variable,
            "valor_min": u.valor_min,
            "valor_max": u.valor_max,
            "accion": u.accion,
            "severidad": u.severidad,
            "activo": u.activo,
        }
        for u in vigente.umbrales.all()
    ]


def procedimiento_del_arreglo():
    """Receta de medicion del interrogador optico integrado al arreglo.

    Es con la que se caracteriza una red apenas termina de grabarse. Se crea la
    primera vez que se necesita.

    Returns:
        El :class:`~fabricacion.models.Procedimiento`.
    """
    from fabricacion.models import Procedimiento

    procedimiento, _ = Procedimiento.objects.get_or_create(
        titulo="Interrogador optico del arreglo",
        version="sim",
        defaults={
            "span": 500.0,
            "resolucion": 2.0,
            "sensibilidad": "SIMULADO",
            "escala_vertical": 3.0,
            "corriente_sld": 150.0,
        },
    )
    return procedimiento


def publicar_configuracion() -> int:
    """Deja los umbrales vigentes donde el controlador los lee.

    Returns:
        Cuantos umbrales se publicaron.
    """
    umbrales = umbrales_vigentes()
    bus().set(p.CLAVE_CONFIGURACION, json.dumps(umbrales))
    return len(umbrales)
