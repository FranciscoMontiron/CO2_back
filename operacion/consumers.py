"""Canal WebSocket de telemetria (ADR-0003).

Es la unica superficie asincrona del sistema: reenvia al navegador lo que el
controlador publica por pub/sub. No persiste nada (ADR-0007) y no recibe
comandos: los comandos van por la API REST, que los audita.

**Autenticacion.** El navegador no permite mandar headers en el handshake de
WebSocket, asi que el JWT de acceso viaja en la query string
(``/ws/telemetria/?token=...``). nginx registra solo la ruta y no los
parametros, para que el token no quede en los logs.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs

import redis.asyncio as aioredis
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from controller import protocolo as p

logger = logging.getLogger("operacion.ws")

# Codigo de cierre propio para "token invalido o vencido": el front lo distingue
# de un corte de red y renueva el token antes de reconectar.
CIERRE_NO_AUTENTICADO = 4401


@database_sync_to_async
def _usuario_activo(usuario_id) -> bool:
    from usuarios.models import Usuario

    return Usuario.objects.filter(pk=usuario_id, is_active=True).exists()


async def autenticar(query_string: bytes) -> int | None:
    """Valida el JWT de la query string.

    Args:
        query_string: La query string cruda del handshake.

    Returns:
        El id del usuario, o ``None`` si el token falta, es invalido, vencio o
        el usuario fue dado de baja.
    """
    token = parse_qs(query_string.decode()).get("token", [None])[0]
    if not token:
        return None
    try:
        usuario_id = AccessToken(token)["user_id"]
    except (TokenError, KeyError):
        return None
    return usuario_id if await _usuario_activo(usuario_id) else None


class TelemetriaConsumer(AsyncWebsocketConsumer):
    """Reenvia la telemetria del controlador a un navegador."""

    async def connect(self) -> None:
        """Autentica, acepta y empieza a reenviar."""
        self._tarea: asyncio.Task | None = None
        self._redis = None
        if await autenticar(self.scope["query_string"]) is None:
            await self.close(code=CIERRE_NO_AUTENTICADO)
            return
        await self.accept()
        self._redis = aioredis.from_url(f"{settings.REDIS_URL}/{settings.REDIS_DB_PUBSUB}")
        self._tarea = asyncio.create_task(self._reenviar())

    async def _reenviar(self) -> None:
        """Manda la ultima telemetria conocida y despues cada mensaje nuevo."""
        ultima = await self._redis.get(p.CLAVE_TELEMETRIA)
        if ultima:
            await self.send(text_data=ultima.decode())
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(p.CANAL_TELEMETRIA)
        try:
            async for mensaje in pubsub.listen():
                if mensaje["type"] == "message":
                    await self.send(text_data=mensaje["data"].decode())
        finally:
            await pubsub.unsubscribe(p.CANAL_TELEMETRIA)
            await pubsub.aclose()

    async def disconnect(self, code) -> None:
        """Corta el reenvio y libera la conexion a Redis."""
        if self._tarea is not None:
            self._tarea.cancel()
        if self._redis is not None:
            await self._redis.aclose()
