"""Configuracion de la app de usuarios, roles y auditoria."""

from django.apps import AppConfig


class UsuariosConfig(AppConfig):
    """Usuarios, roles, permisos y registro de auditoria."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "usuarios"
    verbose_name = "Usuarios, roles y auditoria"

    def ready(self) -> None:
        """Engancha la auditoria automatica a las senales de los modelos (RN010)."""
        from . import auditoria

        auditoria.conectar()
