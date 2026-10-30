"""Vistas de los procedimientos guiados."""

from django.db.models import Prefetch
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from comun.enums import TipoRol
from comun.permisos import escritura_solo_para

from .catalogo import catalogo
from .models import PasoGuiado, ProcedimientoGuiado
from .serializers import ProcedimientoGuiadoSerializer, copiar


class ProcedimientoGuiadoViewSet(viewsets.ModelViewSet):
    """CRUD de procedimientos guiados, identificados por su codigo.

    Los lee cualquier usuario autenticado (el operador los sigue); los crean,
    modifican y borran el investigador y el administrador, igual que los
    programas.
    """

    serializer_class = ProcedimientoGuiadoSerializer
    lookup_field = "codigo"
    permission_classes = [escritura_solo_para(TipoRol.ADMINISTRADOR, TipoRol.INVESTIGADOR)]

    def get_queryset(self):
        """Solo los procedimientos activos, con todo lo anidado precargado."""
        return (
            ProcedimientoGuiado.objects.filter(activo=True)
            .select_related("creado_por")
            .prefetch_related(
                Prefetch("pasos", queryset=PasoGuiado.objects.prefetch_related("validaciones")),
                "programas",
            )
            .order_by("codigo")
        )

    def perform_destroy(self, procedimiento: ProcedimientoGuiado) -> None:
        """Borra el procedimiento si ningun programa activo lo usa.

        Raises:
            ValidationError: Si hay programas que lo tienen asignado.
        """
        en_uso = [p.codigo for p in procedimiento.programas.all() if p.activo]
        if en_uso:
            raise ValidationError(
                {"detail": f"Lo usan los programas {', '.join(en_uso)}: asigneles otro antes."}
            )
        # Los programas dados de baja que lo citaban se quedan sin procedimiento.
        procedimiento.programas.update(procedimiento=None)
        procedimiento.delete()

    @action(detail=True, methods=["post"])
    def duplicar(self, request, codigo=None):
        """``POST /api/procedimientos/{codigo}/duplicar/``: copia para editar."""
        copia = copiar(self.get_object(), request.user)
        return Response(
            self.get_serializer(self.get_queryset().get(pk=copia.pk)).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"])
    def catalogo(self, request):
        """``GET /api/procedimientos/catalogo/``: fuentes, operadores y comandos."""
        return Response(catalogo())
