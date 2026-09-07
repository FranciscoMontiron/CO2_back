"""Configuracion de desarrollo y de la simulacion arm64 (ADR-0005)."""

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# En desarrollo se permite cualquier origen para no pelear con el puerto de
# Vite del front. En produccion manda CORS_ALLOWED_ORIGINS de forma estricta.
CORS_ALLOW_ALL_ORIGINS = True
