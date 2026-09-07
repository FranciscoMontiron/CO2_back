"""Punto de entrada WSGI: lo sirve gunicorn en el servicio `api`.

Atiende la API REST sincrona (comandos, CRUD, autenticacion). Es la mitad
sincrona del split de ADR-0003, y se mantiene deliberadamente fuera de ASGI
para no arrastrar las vistas DRF al threadpool asincrono.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

application = get_wsgi_application()
