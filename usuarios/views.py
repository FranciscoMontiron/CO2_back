"""Vistas de sesion, usuarios y auditoria."""

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from comun.enums import TipoRol
from comun.permisos import solo_roles

from .models import RegistroAuditoria, Usuario
from .serializers import AuditoriaSerializer, IniciarSesionSerializer, UsuarioSerializer


class IniciarSesionView(TokenObtainPairView):
    """``POST /api/auth/token/``: usuario y contrasena a cambio del par JWT."""

    serializer_class = IniciarSesionSerializer


class YoView(APIView):
    """``GET /api/auth/yo/``: el usuario de la sesion actual.

    El front lo usa al recargar la pagina: tiene el token pero no sabe de quien
    es ni que rol tiene.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Devuelve el usuario autenticado.

        Args:
            request: La peticion autenticada.

        Returns:
            El usuario serializado.
        """
        return Response(UsuarioSerializer(request.user).data)


class UsuarioViewSet(viewsets.ReadOnlyModelViewSet):
    """Listado de usuarios.

    Lo lee cualquier usuario autenticado: el front lo muestra en solo lectura a
    los roles no administradores, y lo usa para armar el filtro por operador.
    La edicion sigue yendo por el admin de Django hasta que haya pantalla.
    """

    queryset = Usuario.objects.select_related("rol").order_by("username")
    serializer_class = UsuarioSerializer
    permission_classes = [IsAuthenticated]


class AuditoriaViewSet(viewsets.ReadOnlyModelViewSet):
    """Bitacora de auditoria. Solo el administrador (RN010).

    Es de solo lectura por construccion: el modelo es inmutable y esta vista no
    expone escritura.
    """

    queryset = RegistroAuditoria.objects.select_related("usuario").order_by("-fecha_hora")
    serializer_class = AuditoriaSerializer
    permission_classes = [solo_roles(TipoRol.ADMINISTRADOR)]
