"""Configuracion de la app de trazabilidad de la fabricacion."""

from django.apps import AppConfig


class TrazabilidadConfig(AppConfig):
    """Trazabilidad de la fabricacion."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "trazabilidad"
    verbose_name = "Trazabilidad de la fabricacion"
