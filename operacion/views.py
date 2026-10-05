"""Vistas de alertas, emergencias y umbrales."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from comun.enums import ModoEjecucion
from controller import protocolo
from fabricacion.models import Lote, Red
from programas.models import Programa
from programas.serializers import ProgramaSerializer
from trazabilidad.models import RegistroDeFabricacion

from . import control
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


# ── Control del arreglo ──────────────────────────────────────────────────────
#  La API no toca el hardware: valida, deja asentado y manda el comando al
#  controlador por Redis (ADR-0002, ADR-0004). El resultado se ve en la
#  telemetria, no en la respuesta HTTP.


def _ip(request) -> str | None:
    """IP del cliente detras de nginx."""
    reenviada = request.META.get("HTTP_X_FORWARDED_FOR")
    return reenviada.split(",")[0].strip() if reenviada else request.META.get("REMOTE_ADDR")


def _sin_controlador() -> Response:
    return Response(
        {"detail": "Sin conexion al controlador: los comandos estan bloqueados."},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class ControlEstadoView(APIView):
    """``GET /api/control/estado/``: la ultima telemetria del controlador.

    El front la usa para pintar el estado apenas carga, antes de que llegue el
    primer mensaje por WebSocket.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Devuelve la ultima telemetria, o 503 si el controlador no publica."""
        telemetria = control.ultima_telemetria()
        if telemetria is None:
            return _sin_controlador()
        return Response(telemetria)


class ControlComandoView(APIView):
    """``POST /api/control/comandos/``: ``{"accion": ..., "falla": ...}``.

    Acciones admitidas: las de ``controller.protocolo.COMANDOS_SIMPLES``. La
    respuesta es ``202``: el comando se encolo; si el controlador lo rechaza, el
    motivo aparece en el mensaje de la telemetria.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Valida y encola el comando."""
        accion = request.data.get("accion")
        if accion not in protocolo.COMANDOS_SIMPLES:
            return Response({"detail": f"Accion desconocida: {accion}"}, status=400)
        datos = {}
        if accion == protocolo.INYECTAR_FALLA:
            if settings.CONTROLLER_HAL != "simulado":
                return Response(
                    {"detail": "Las fallas solo se pueden simular con el HAL simulado."}, status=409
                )
            falla = request.data.get("falla", protocolo.FALLA_NINGUNA)
            if falla not in protocolo.FALLAS:
                return Response({"detail": f"Falla desconocida: {falla}"}, status=400)
            datos["falla"] = falla
        if accion == protocolo.EMERGENCIA:
            datos["origen"] = "OPERADOR"
        if accion == protocolo.MOVER_MOTOR:
            try:
                datos["posicion_mm"] = float(request.data.get("posicion_mm"))
            except (TypeError, ValueError):
                return Response({"detail": "Falta posicion_mm o no es un numero."}, status=400)
        try:
            control.enviar(accion, datos, request.user, _ip(request))
        except control.ControladorNoDisponibleError:
            return _sin_controlador()
        if accion == protocolo.REARMAR:
            # El rearme lo hace una persona: queda asentado quien fue.
            pendiente = (
                EventoEmergencia.objects.filter(rearmado_en__isnull=True)
                .order_by("-fecha_hora")
                .first()
            )
            if pendiente is not None:
                pendiente.rearmar(request.user)
        return Response({"accion": accion, "encolado": True}, status=status.HTTP_202_ACCEPTED)


class ControlIniciarView(APIView):
    """``POST /api/control/iniciar/``: ``{"programa": "PRG-001", "modo": "REAL"}``.

    Crea el registro del ensayo (y la red, en modo REAL) y le manda el programa
    al controlador. Valida contra la ultima telemetria que el sistema este LISTO
    con el laser encendido: si no, el ensayo nunca arrancaria y quedaria un
    registro huerfano.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Arranca la ejecucion de un programa."""
        telemetria = control.ultima_telemetria()
        if telemetria is None or not control.controlador_vivo():
            return _sin_controlador()
        if telemetria["estado"] != "LISTO" or not telemetria["actuadores"]["laser"]:
            return Response(
                {"detail": "El sistema tiene que estar LISTO y con el laser encendido."},
                status=status.HTTP_409_CONFLICT,
            )
        if telemetria.get("bloqueado"):
            return Response(
                {"detail": "Hay un umbral excedido que bloquea los comandos."},
                status=status.HTTP_409_CONFLICT,
            )

        modo = request.data.get("modo", ModoEjecucion.REAL)
        if modo not in ModoEjecucion.values:
            return Response({"detail": f"Modo desconocido: {modo}"}, status=400)
        programa = Programa.objects.filter(codigo=request.data.get("programa"), activo=True).first()
        if programa is None or not programa.validado:
            return Response({"detail": "Programa inexistente o sin validar."}, status=400)

        registro = _crear_registro(programa, modo, request.user, request.data.get("lote"))
        datos_programa = ProgramaSerializer(programa).data
        periodo = programa.periodo
        try:
            control.enviar(
                protocolo.INICIAR,
                {
                    "registro": registro.codigo,
                    "modo": modo,
                    "programa": {
                        "codigo": programa.codigo,
                        "nombre": programa.nombre,
                        "potencia_objetivo_mw": datos_programa["potencia_objetivo_mw"],
                        "duracion_pulso_ms": datos_programa["duracion_pulso_ms"],
                        "pulsos": datos_programa["pulsos"],
                        "criterio_fin": programa.criterio_fin,
                        "periodo": {
                            "tipo": periodo.tipo,
                            "base_um": periodo.periodo_base,
                            "incremento_um": periodo.incremento,
                            "factor": periodo.factor,
                        },
                    },
                },
                request.user,
                _ip(request),
            )
        except control.ControladorNoDisponibleError:
            registro.abortar("El controlador dejo de responder antes de arrancar.")
            return _sin_controlador()
        return Response(
            {
                "registro": registro.codigo,
                "red": registro.red.codigo if registro.red else None,
                "programa": programa.codigo,
                "modo": modo,
            },
            status=status.HTTP_201_CREATED,
        )


def _siguiente_codigo_ensayo() -> str:
    """Proximo codigo libre con la forma ``ENS-2026-00001``."""
    prefijo = f"ENS-{timezone.now():%Y}-"
    numeros = [
        int(c.removeprefix(prefijo))
        for c in RegistroDeFabricacion.objects.filter(codigo__startswith=prefijo).values_list(
            "codigo", flat=True
        )
        if c.removeprefix(prefijo).isdigit()
    ]
    return f"{prefijo}{(max(numeros) + 1 if numeros else 1):05d}"


@transaction.atomic
def _crear_registro(programa, modo, usuario, numero_lote=None) -> RegistroDeFabricacion:
    """Crea el registro del ensayo y, en modo REAL, la red que se va a grabar.

    La red va al lote indicado, o al mas reciente si no se indica. El
    procedimiento de medicion es el del interrogador optico del arreglo.
    """
    red = None
    if modo == ModoEjecucion.REAL:
        if numero_lote:
            lote, _ = Lote.objects.get_or_create(
                numero=int(numero_lote), defaults={"fecha_inicio": timezone.localdate()}
            )
        else:
            lote = Lote.objects.order_by("-numero").first() or Lote.objects.create(
                numero=1, fecha_inicio=timezone.localdate()
            )
        siguiente = (lote.redes.aggregate(m=Max("numero"))["m"] or 0) + 1
        red = Red.objects.create(
            lote=lote,
            numero=siguiente,
            tipo=programa.tipo_red,
            fecha_fabricacion=timezone.localdate(),
        )
    procedimiento = control.procedimiento_del_arreglo()
    registro = RegistroDeFabricacion.objects.create(
        codigo=_siguiente_codigo_ensayo(),
        programa=programa,
        red=red,
        modo=modo,
        configuracion=Configuracion.objects.filter(activa=True).first(),
        procedimiento=procedimiento,
    )
    registro.operadores.add(usuario)
    return registro
