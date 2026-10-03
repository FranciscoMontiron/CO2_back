"""Configuracion de la app de fabricacion y caracterizacion de redes."""

from django.apps import AppConfig


class FabricacionConfig(AppConfig):
    """Fabricacion y caracterizacion de redes."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "fabricacion"
    verbose_name = "Fabricacion y caracterizacion de redes"
