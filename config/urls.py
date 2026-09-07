"""Ruteo raiz de CO2_back.

Todo lo del backend cuelga de ``/api/`` para que nginx pueda separar por
prefijo el trafico sincrono (``/api/`` a gunicorn) del asincrono (``/ws/`` a
daphne) sin ambiguedad. Ver ADR-0003.

Las rutas de dominio se agregan cuando se cierre el Diagrama de Clases v4.
"""

from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from config.health import healthz

urlpatterns = [
    path("api/healthz/", healthz, name="healthz"),
    # Contrato de la API: es lo que el front consume para reemplazar
    # `src/mock/api.ts` sin adivinar firmas.
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="docs",
    ),
    path("admin/", admin.site.urls),
]
