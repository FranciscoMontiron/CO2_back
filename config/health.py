"""Endpoint de salud del backend.

Se usa como healthcheck de los contenedores `api` y `ws`, y como diagnostico
rapido durante el despliegue. No expone informacion sensible ni requiere
autenticacion, porque Docker lo consulta sin credenciales.

Es una vista de Django plana, no de DRF: asi no la alcanza la politica global
``IsAuthenticated`` de ``REST_FRAMEWORK``.
"""

import logging

import redis
from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse

logger = logging.getLogger(__name__)


def _revisar_base_de_datos() -> tuple[bool, str]:
    """Verifica que MySQL responda a una consulta trivial.

    Returns:
        Una tupla ``(ok, detalle)``.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - el healthcheck reporta, no propaga
        logger.warning("Healthcheck: MySQL no responde: %s", exc)
        return False, str(exc)
    return True, "ok"


def _revisar_redis() -> tuple[bool, str]:
    """Verifica que Redis responda al PING.

    Returns:
        Una tupla ``(ok, detalle)``.
    """
    try:
        cliente = redis.Redis.from_url(
            f"{settings.REDIS_URL}/{settings.REDIS_DB_HEARTBEAT}",
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        cliente.ping()
    except Exception as exc:  # noqa: BLE001 - el healthcheck reporta, no propaga
        logger.warning("Healthcheck: Redis no responde: %s", exc)
        return False, str(exc)
    return True, "ok"


def _revisar_controlador() -> tuple[bool, str]:
    """Consulta el heartbeat del proceso controlador (ADR-0002).

    El controlador escribe una clave con TTL corto. Si vencio, el controlador
    esta caido o colgado y el front debe mostrar SIN CONEXION AL CONTROLADOR
    en lugar de telemetria congelada.

    Returns:
        Una tupla ``(ok, detalle)``. No participa del estado general del
        healthcheck: la API sigue siendo util aunque el controlador este caido.
    """
    try:
        cliente = redis.Redis.from_url(
            f"{settings.REDIS_URL}/{settings.REDIS_DB_HEARTBEAT}",
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        vivo = cliente.exists(settings.CONTROLLER_HEARTBEAT_KEY) == 1
    except Exception as exc:  # noqa: BLE001 - el healthcheck reporta, no propaga
        return False, str(exc)
    return vivo, "ok" if vivo else "heartbeat vencido o ausente"


def healthz(request: HttpRequest) -> JsonResponse:
    """Devuelve el estado de las dependencias del backend.

    El codigo HTTP refleja unicamente las dependencias sin las que la API no
    puede funcionar (MySQL y Redis). El estado del controlador se informa pero
    no tumba el healthcheck, porque la API sigue sirviendo consultas
    historicas y administracion aunque el hardware no este disponible.

    Args:
        request: La peticion HTTP entrante. No se usa.

    Returns:
        ``200`` si MySQL y Redis responden, ``503`` en caso contrario.
    """
    bd_ok, bd_detalle = _revisar_base_de_datos()
    redis_ok, redis_detalle = _revisar_redis()
    ctrl_ok, ctrl_detalle = _revisar_controlador()

    saludable = bd_ok and redis_ok

    cuerpo = {
        "estado": "ok" if saludable else "degradado",
        "dependencias": {
            "mysql": {"ok": bd_ok, "detalle": bd_detalle},
            "redis": {"ok": redis_ok, "detalle": redis_detalle},
            "controlador": {"ok": ctrl_ok, "detalle": ctrl_detalle},
        },
    }
    return JsonResponse(cuerpo, status=200 if saludable else 503)
