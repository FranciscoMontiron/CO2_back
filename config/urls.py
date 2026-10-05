"""Ruteo raiz de CO2_back.

Todo lo del backend cuelga de ``/api/`` para que nginx pueda separar por
prefijo el trafico sincrono (``/api/`` a gunicorn) del asincrono (``/ws/`` a
daphne) sin ambiguedad. Ver ADR-0003.
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from config.health import healthz
from operacion.views import (
    AlertaViewSet,
    ControlComandoView,
    ControlEstadoView,
    ControlIniciarView,
    EventoEmergenciaViewSet,
    UmbralViewSet,
)
from programas.views import ProgramaViewSet
from trazabilidad.views import EnsayoViewSet
from usuarios.views import AuditoriaViewSet, IniciarSesionView, UsuarioViewSet, YoView

router = DefaultRouter()
router.register("ensayos", EnsayoViewSet, basename="ensayo")
router.register("programas", ProgramaViewSet, basename="programa")
router.register("alertas", AlertaViewSet, basename="alerta")
router.register("emergencias", EventoEmergenciaViewSet, basename="emergencia")
router.register("umbrales", UmbralViewSet, basename="umbral")
router.register("usuarios", UsuarioViewSet, basename="usuario")
router.register("auditoria", AuditoriaViewSet, basename="auditoria")

urlpatterns = [
    path("api/healthz/", healthz, name="healthz"),
    # Sesion (JWT): el front guarda el par de tokens y renueva el de acceso.
    path("api/auth/token/", IniciarSesionView.as_view(), name="token"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("api/auth/yo/", YoView.as_view(), name="yo"),
    # Control del arreglo: la API encola comandos, el controlador los ejecuta.
    path("api/control/estado/", ControlEstadoView.as_view(), name="control-estado"),
    path("api/control/comandos/", ControlComandoView.as_view(), name="control-comandos"),
    path("api/control/iniciar/", ControlIniciarView.as_view(), name="control-iniciar"),
    path("api/", include(router.urls)),
    # Contrato de la API: es lo que el front consume en `src/api/`.
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="docs",
    ),
    path("admin/", admin.site.urls),
]
