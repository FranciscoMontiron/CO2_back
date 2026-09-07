"""Configuracion de despliegue sobre la Raspberry Pi 5."""

from .base import *  # noqa: F403

DEBUG = False

# RNF003: HTTPS entre front y back. nginx termina TLS; estas opciones evitan
# que Django emita cookies por un canal en claro.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
