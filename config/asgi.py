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

# Los consumers se agregan cuando se cierre el Diagrama de Clases v4. La
# superficie asincrona del sistema tiene que caber en esta lista: si crece
# mucho, el split de ADR-0003 dejo de cumplir su proposito.
websocket_urlpatterns: list = []

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # TODO(ADR-0003): envolver en AuthMiddlewareStack + middleware propio de
        # JWT por query-string. El browser no permite headers en el handshake
        # de WebSocket, asi que la autenticacion no puede reusar la de DRF.
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
