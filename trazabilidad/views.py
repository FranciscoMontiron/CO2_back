"""Vistas de los ensayos."""

import re

from django.db.models import Q
from django.http import Http404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import RegistroDeFabricacion
from .serializers import EnsayoDetalleSerializer, EnsayoSerializer


class EnsayoViewSet(viewsets.ReadOnlyModelViewSet):
    """Historial de ensayos, identificados por su codigo.

    Es de solo lectura: las corridas las crea el controlador al ejecutar un
    programa (M3), no una persona desde un formulario.

    Filtros por query string: ``estado``, ``modo``, ``programa`` (codigo),
    ``operador`` (username), ``lote`` (numero), ``desde`` y ``hasta``
    (``AAAA-MM-DD``) y ``q`` (busca en el codigo y en las observaciones).
    """

    lookup_field = "codigo"
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        """El detalle lleva la caracterizacion; el listado no."""
        return EnsayoDetalleSerializer if self.action != "list" else EnsayoSerializer

    def get_queryset(self):
        """Aplica los filtros pedidos sobre el historial."""
        qs = (
            RegistroDeFabricacion.objects.select_related(
                "programa__periodo", "red__lote", "red__espectro__procedimiento", "procedimiento"
            )
            .prefetch_related("operadores", "red__picos", "red__marcas")
            .order_by("-inicio")
        )
        p = self.request.query_params
        filtros = {
            "estado": "estado",
            "modo": "modo",
            "programa": "programa__codigo",
            "operador": "operadores__username",
            "desde": "inicio__date__gte",
            "hasta": "inicio__date__lte",
        }
        for parametro, campo in filtros.items():
            if p.get(parametro):
                qs = qs.filter(**{campo: p[parametro]})
        if p.get("lote"):
            # Acepta "3", "LOT-003" o "L003": interesa el numero.
            digitos = re.findall(r"\d+", p["lote"])
            qs = qs.filter(red__lote__numero=int(digitos[0])) if digitos else qs.none()
        if p.get("q"):
            qs = qs.filter(Q(codigo__icontains=p["q"]) | Q(observaciones__icontains=p["q"]))
        return qs.distinct()

    @action(detail=True, methods=["get"])
    def curva(self, request, codigo=None):
        """``GET /api/ensayos/{codigo}/curva/``: el espectro de la red.

        Args:
            request: La peticion.
            codigo: Codigo del ensayo.

        Returns:
            Los dos ejes del espectro como arreglos paralelos.

        Raises:
            Http404: Si la red no fue caracterizada todavia.
        """
        registro = self.get_object()
        espectro = getattr(registro.red, "espectro", None) if registro.red else None
        if espectro is None:
            raise Http404("La red de este ensayo todavia no fue caracterizada.")
        return Response(
            {
                "red": registro.red.codigo,
                "fecha_captura": espectro.fecha_captura,
                "procedimiento": str(espectro.procedimiento),
                "longitudes_onda_nm": espectro.longitudes_de_onda(),
                "transmitancias_db": espectro.transmitancias,
            }
        )
