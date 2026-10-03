"""Configuracion de la app de programas de grabado."""

from django.apps import AppConfig


class ProgramasConfig(AppConfig):
    """Programas de grabado."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "programas"
    verbose_name = "Programas de grabado"
