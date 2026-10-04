"""Permisos de la API por rol funcional.

El rol sale de ``Usuario.rol`` (un rol por usuario, ver ``usuarios/models.py``).
El superusuario de Django pasa siempre: es la cuenta de rescate, y bloquearla
por no tener rol asignado dejaria el sistema sin forma de administrarse.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from comun.enums import TipoRol


def rol_de(usuario) -> str | None:
    """Devuelve el codigo de rol de un usuario, o ``None`` si no tiene.

    Args:
        usuario: El usuario autenticado.

    Returns:
        El valor de :class:`TipoRol`, o ``None``.
    """
    if usuario is None or not usuario.is_authenticated:
        return None
    if usuario.is_superuser:
        return TipoRol.ADMINISTRADOR
    return usuario.rol.codigo if usuario.rol_id else None


def solo_roles(*roles: str) -> type[BasePermission]:
    """Construye un permiso que admite solo a los roles indicados.

    Args:
        *roles: Valores de :class:`TipoRol` admitidos.

    Returns:
        Una clase de permiso de DRF.
    """

    class SoloRoles(BasePermission):
        """Admite solo a ciertos roles, para cualquier metodo."""

        message = "Su rol no tiene acceso a este recurso."

        def has_permission(self, request, view) -> bool:
            """Verifica el rol del usuario."""
            return rol_de(request.user) in roles

    return SoloRoles


def escritura_solo_para(*roles: str) -> type[BasePermission]:
    """Construye un permiso de lectura libre y escritura restringida.

    Cualquier usuario autenticado lee; solo los roles indicados crean, modifican
    o borran. Es el caso de los programas: el operador los usa, pero los disenan
    el investigador y el administrador.

    Args:
        *roles: Valores de :class:`TipoRol` que pueden escribir.

    Returns:
        Una clase de permiso de DRF.
    """

    class EscrituraRestringida(BasePermission):
        """Lectura para todos los autenticados, escritura para algunos roles."""

        message = "Su rol puede consultar este recurso, pero no modificarlo."

        def has_permission(self, request, view) -> bool:
            """Deja pasar las lecturas y filtra las escrituras por rol."""
            if request.method in SAFE_METHODS:
                return bool(request.user and request.user.is_authenticated)
            return rol_de(request.user) in roles

    return EscrituraRestringida
