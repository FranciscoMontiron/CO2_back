"""Configuracion de la app de procedimientos guiados."""

from django.apps import AppConfig


class ProcedimientosConfig(AppConfig):
    """Procedimientos guiados que sigue el operador en «Nuevo ensayo»."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "procedimientos"
    verbose_name = "Procedimientos guiados"
