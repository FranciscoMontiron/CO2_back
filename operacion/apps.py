"""Configuracion de la app de configuracion, alertas y emergencias."""

from django.apps import AppConfig


class OperacionConfig(AppConfig):
    """Configuracion, alertas y emergencias."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "operacion"
    verbose_name = "Configuracion, alertas y emergencias"
