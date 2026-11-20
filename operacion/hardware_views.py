"""Configuración persistente y control manual de instancias de hardware."""

import json
import time
from uuid import uuid4

import redis
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from comun.enums import TipoRol
from comun.permisos import escritura_solo_para
from controller import protocolo as p
from controller.hardware_config import (
    catalogo,
    guardar_archivo,
    leer_archivo,
    revision,
    validar_configuracion,
    validar_operacion,
)
from operacion import control
from usuarios.models import RegistroAuditoria


def estado_hardware():
    """Solo entrega estados vigentes del proceso controlador."""
    crudo = control.bus().get(p.CLAVE_HARDWARE)
    return json.loads(crudo) if crudo else None


class HardwareCatalogoView(APIView):
    """Capacidades y parámetros de drivers realmente implementados."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Entrega el recurso solicitado sin acceder al hardware."""
        return Response(catalogo())


class HardwareConfigView(APIView):
    """Configuración deseada: escritura atómica con control de concurrencia."""

    permission_classes = [escritura_solo_para(TipoRol.ADMINISTRADOR)]

    def get(self, request):
        """Entrega el recurso solicitado sin acceder al hardware."""
        try:
            datos = leer_archivo()
            return Response(datos, headers={"ETag": f'"{revision(datos)}"'})
        except (ValueError, OSError) as exc:
            return Response({"detail": f"No se pudo leer la configuración: {exc}"}, status=503)

    def post(self, request):
        """Valida y procesa la petición sin acceder directamente al hardware."""
        esperada = request.headers.get("If-Match", "").strip('"')
        if not esperada:
            return Response(
                {"detail": "Recargue la configuración y envíe su ETag en If-Match."}, status=428
            )
        try:
            datos = validar_configuracion(request.data)
            with control.bus().lock("co2:hardware:config:lock", timeout=10, blocking_timeout=2):
                if esperada != revision(leer_archivo()):
                    return Response(
                        {"detail": "La configuración cambió. Recargue antes de guardar."},
                        status=409,
                    )
                datos = guardar_archivo(datos)
            nueva = revision(datos)
            RegistroAuditoria.objects.create(
                usuario=request.user,
                accion="MODIFICAR",
                entidad="configuracion_hardware",
                codigo_entidad=nueva,
                valor_nuevo=json.dumps(datos, ensure_ascii=False),
                critica=True,
                direccion_ip=request.META.get("REMOTE_ADDR"),
            )
            return Response(
                {
                    "revision": nueva,
                    "detail": "Guardado. Se aplicará cuando las salidas estén detenidas.",
                },
                status=202,
                headers={"ETag": f'"{nueva}"'},
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        except (OSError, redis.RedisError) as exc:
            return Response({"detail": f"No se pudo guardar: {exc}"}, status=503)


class HardwareEstadoView(APIView):
    """Inventario aplicado, muestras con calidad y resultados correlacionados."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Entrega el recurso solicitado sin acceder al hardware."""
        try:
            estado = estado_hardware()
        except redis.RedisError:
            estado = None
        if estado is None:
            return Response(
                {"detail": "Sin estado GPIO vigente. Compruebe CONTROLLER_HAL y conexión."},
                status=503,
            )
        return Response(estado)


class HardwareComandoView(APIView):
    """La API acepta la orden; APLICADO llega posteriormente del controlador."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Valida y procesa la petición sin acceder directamente al hardware."""
        try:
            if not isinstance(request.data, dict):
                raise ValueError("La orden debe ser un objeto JSON.")
            estado = estado_hardware()
            if not estado:
                return Response({"detail": "Sin conexión al controlador GPIO."}, status=503)
            if estado["emergencia"] or estado["aplicando"]:
                return Response(
                    {"detail": "Hardware en emergencia o aplicando configuración."}, status=409
                )
            if (
                not estado["revision_aplicada"]
                or request.data.get("revision") != estado["revision_aplicada"]
            ):
                return Response(
                    {"detail": "La revisión solicitada no está aplicada. Recargue el estado."},
                    status=409,
                )
            nombre = request.data.get("componente")
            if not isinstance(nombre, str):
                raise ValueError("Indique el nombre del componente.")
            componente = estado["componentes"].get(nombre)
            if not componente or componente["estado"] != "Vigente":
                raise ValueError("Componente inexistente o eliminado.")
            operacion = request.data.get("operacion")
            argumentos = request.data.get("argumentos", {})
            validar_operacion(componente, operacion, argumentos)
            identificador = str(uuid4())
            control.enviar(
                p.COMPONENTE,
                {
                    "id": identificador,
                    "componente": nombre,
                    "operacion": operacion,
                    "argumentos": argumentos,
                    "revision": estado["revision_aplicada"],
                    "vence_en": time.time() + 10,
                },
                request.user,
                request.META.get("REMOTE_ADDR"),
            )
            return Response(
                {"id": identificador, "estado": "RECIBIDO", "encolado": True}, status=202
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        except (redis.RedisError, control.ControladorNoDisponibleError) as exc:
            return Response({"detail": str(exc)}, status=503)


class HardwareAplicarView(APIView):
    """Reintenta una revisión fallida; nunca fuerza cambios con salidas activas."""

    permission_classes = [escritura_solo_para(TipoRol.ADMINISTRADOR)]

    def post(self, request):
        """Valida y procesa la petición sin acceder directamente al hardware."""
        try:
            # Solo el controlador en modo GPIO publica estado de hardware y sabe aplicar
            # la configuración de componentes; con el HAL simulado no tiene efecto.
            if not estado_hardware():
                return Response(
                    {
                        "detail": "La configuración de componentes solo se aplica con el "
                        "controlador en modo GPIO. En modo simulado no tiene efecto."
                    },
                    status=409,
                )
            control.enviar(
                p.APLICAR_HARDWARE, usuario=request.user, ip=request.META.get("REMOTE_ADDR")
            )
            return Response(
                {"detail": "Reintento solicitado; consulte la revisión aplicada."}, status=202
            )
        except (redis.RedisError, control.ControladorNoDisponibleError) as exc:
            return Response({"detail": str(exc)}, status=503)
