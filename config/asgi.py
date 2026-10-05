"""Punto de entrada ASGI: lo sirve daphne en el servicio `ws`.

Es la mitad asincrona del split de ADR-0003. Su unica responsabilidad es el
canal WebSocket de telemetria, alarmas y estado; la API REST no pasa por aca.

El protocolo ``http`` se enruta igual porque el healthcheck del contenedor `ws`
consulta ``/api/healthz/`` sobre el mismo puerto. nginx no le manda trafico
``/api/``: eso va a gunicorn.
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

django_asgi_app = get_asgi_application()

# Se importa despues de inicializar Django: el consumer usa modelos y settings.
from django.urls import path  # noqa: E402

from operacion.consumers import TelemetriaConsumer  # noqa: E402

# La superficie asincrona del sistema tiene que caber en esta lista: si crece
# mucho, el split de ADR-0003 dejo de cumplir su proposito.
websocket_urlpatterns = [
    path("ws/telemetria/", TelemetriaConsumer.as_asgi()),
]

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # La autenticacion la resuelve el consumer con el JWT de la query
        # string: el browser no permite headers en el handshake de WebSocket.
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
