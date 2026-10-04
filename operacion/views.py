"""Vistas de alertas, emergencias y umbrales."""

from django.core.exceptions import ValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Alerta, Configuracion, EventoEmergencia, Umbral
from .serializers import AlertaSerializer, EventoEmergenciaSerializer, UmbralSerializer


class AlertaViewSet(viewsets.ReadOnlyModelViewSet):
    """Alertas por umbral excedido. Cualquier usuario autenticado las reconoce.

    El reconocimiento es del operador de turno: es quien esta frente a la
    maquina cuando la alerta salta.
    """

    queryset = Alerta.objects.select_related("umbral", "reconocida_por").order_by("-fecha_hora")
    serializer_class = AlertaSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["post"])
    def reconocer(self, request, pk=None):
        """``POST /api/alertas/{id}/reconocer/``: deja constancia de quien la vio.

        Args:
            request: La peticion autenticada.
            pk: Id de la alerta.

        Returns:
            La alerta reconocida, o ``409`` si ya lo estaba.
        """
        alerta = self.get_object()
        try:
            alerta.reconocer(request.user)
        except ValidationError as exc:
            return Response({"detail": exc.messages[0]}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(alerta).data)


class EventoEmergenciaViewSet(viewsets.ReadOnlyModelViewSet):
    """Historial de paradas de emergencia.

    Solo lectura: las paradas las registra el controlador (M4). El rearme desde
    la interfaz llega junto con el E-Stop de software.
    """

    queryset = EventoEmergencia.objects.select_related("rearmado_por", "registro").order_by(
        "-fecha_hora"
    )
    serializer_class = EventoEmergenciaSerializer
    permission_classes = [IsAuthenticated]


class UmbralViewSet(viewsets.ReadOnlyModelViewSet):
    """Umbrales de la configuracion vigente.

    Se listan solo los de la configuracion activa: son los que el controlador
    carga, y mostrar los de configuraciones viejas confundiria sobre que limites
    estan rigiendo.
    """

    serializer_class = UmbralSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Umbrales de la configuracion activa, o ninguno si no hay vigente."""
        vigente = Configuracion.objects.filter(activa=True).first()
        if vigente is None:
            return Umbral.objects.none()
        return Umbral.objects.filter(configuracion=vigente).order_by("variable")
