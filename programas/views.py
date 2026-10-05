"""Vistas de programas de grabado."""

from rest_framework import viewsets

from comun.enums import TipoRol
from comun.permisos import escritura_solo_para

from .models import Programa
from .serializers import ProgramaSerializer


class ProgramaViewSet(viewsets.ModelViewSet):
    """CRUD de programas, identificados por su codigo.

    Lo usa cualquier usuario autenticado; lo crean, modifican y borran solo el
    investigador y el administrador, igual que en el front.
    """

    serializer_class = ProgramaSerializer
    lookup_field = "codigo"
    permission_classes = [escritura_solo_para(TipoRol.ADMINISTRADOR, TipoRol.INVESTIGADOR)]

    def get_queryset(self):
        """Solo los programas activos: los dados de baja no se ofrecen para grabar."""
        return (
            Programa.objects.filter(activo=True)
            .select_related("periodo", "creado_por")
            .prefetch_related("pasos")
            .order_by("codigo")
        )

    def perform_destroy(self, programa: Programa) -> None:
        """Borra el programa, o lo da de baja si ya se uso.

        Un programa con corridas asociadas no se puede borrar: esas corridas son
        el registro de trazabilidad de RF006/RF007 y tienen que poder decir con
        que programa se grabo. En ese caso se desactiva y deja de ofrecerse.

        Args:
            programa: El programa a eliminar.
        """
        if programa.registros.exists():
            programa.activo = False
            programa.save(update_fields=["activo", "actualizado_en"])
            return
        periodo = programa.periodo
        programa.delete()
        if periodo is not None:
            periodo.delete()
