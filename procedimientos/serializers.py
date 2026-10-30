"""Serializadores de los procedimientos guiados.

El procedimiento se lee y se escribe entero, con sus pasos y validaciones
anidados: es la forma en que lo edita el front y la unica en que se puede
validar como un todo (por ejemplo, que una salida apunte a un paso que existe).

El orden de pasos y validaciones sale de su posicion en la lista; el cliente no
lo manda. Asi no puede haber dos pasos con el mismo numero.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.db import transaction
from rest_framework import serializers

from comun.enums import TipoValidacion

from .catalogo import validar_procedimiento
from .models import PasoGuiado, ProcedimientoGuiado, ValidacionPaso


class ValidacionPasoSerializer(serializers.ModelSerializer):
    """Una validacion, con su detalle."""

    class Meta:
        """Metadatos del serializador."""

        model = ValidacionPaso
        fields = ["id", "orden", "texto", "detalle", "tipo", "condicion"]
        read_only_fields = ["id", "orden"]
        extra_kwargs = {"detalle": {"required": False}, "tipo": {"required": False}}


class PasoGuiadoSerializer(serializers.ModelSerializer):
    """Un paso, con sus validaciones."""

    validaciones = ValidacionPasoSerializer(many=True)

    class Meta:
        """Metadatos del serializador."""

        model = PasoGuiado
        fields = [
            "id",
            "orden",
            "titulo",
            "subtitulo",
            "fase",
            "indicadores",
            "acciones",
            "condicion_avance",
            "salidas",
            "validaciones",
        ]
        read_only_fields = ["id", "orden"]
        extra_kwargs = {
            "subtitulo": {"required": False},
            "indicadores": {"required": False},
            "acciones": {"required": False},
            "condicion_avance": {"required": False},
            "salidas": {"required": False},
        }


def _siguiente_codigo() -> str:
    """Genera el proximo codigo libre con la forma ``PROC-001``."""
    numeros = [
        int(c.split("-")[-1])
        for c in ProcedimientoGuiado.objects.filter(codigo__regex=r"^PROC-\d+$").values_list(
            "codigo", flat=True
        )
    ]
    return f"PROC-{(max(numeros) + 1 if numeros else 1):03d}"


class ProcedimientoGuiadoSerializer(serializers.ModelSerializer):
    """Procedimiento completo: pasos y validaciones anidados."""

    pasos = PasoGuiadoSerializer(many=True)
    creado_por = serializers.SerializerMethodField()
    programas = serializers.SerializerMethodField()

    class Meta:
        """Metadatos del serializador."""

        model = ProcedimientoGuiado
        fields = [
            "codigo",
            "nombre",
            "descripcion",
            "tipo_red",
            "version",
            "activo",
            "creado_por",
            "actualizado_en",
            "programas",
            "pasos",
        ]
        read_only_fields = ["codigo", "version", "activo", "creado_por", "actualizado_en"]
        extra_kwargs = {"descripcion": {"required": False}, "tipo_red": {"required": False}}

    def get_creado_por(self, procedimiento: ProcedimientoGuiado) -> str:
        """Nombre de quien creo el procedimiento."""
        return str(procedimiento.creado_por) if procedimiento.creado_por else ""

    def get_programas(self, procedimiento: ProcedimientoGuiado) -> list[dict]:
        """Programas activos que usan este procedimiento."""
        return [
            {"codigo": prog.codigo, "nombre": prog.nombre}
            for prog in procedimiento.programas.all()
            if prog.activo
        ]

    def validate(self, datos: dict) -> dict:
        """Valida el procedimiento entero contra el catalogo.

        Raises:
            ValidationError: Con la lista de problemas encontrados.
        """
        pasos = datos.get("pasos", [])
        for paso in pasos:
            for v in paso.get("validaciones", []):
                v.setdefault("tipo", TipoValidacion.MANUAL)
        errores = validar_procedimiento(pasos)
        if errores:
            raise serializers.ValidationError({"pasos": errores})
        return datos

    @staticmethod
    def _crear_pasos(procedimiento: ProcedimientoGuiado, pasos: list[dict]) -> None:
        for n, datos_paso in enumerate(pasos, 1):
            validaciones = datos_paso.pop("validaciones", [])
            paso = PasoGuiado.objects.create(procedimiento=procedimiento, orden=n, **datos_paso)
            for i, datos_validacion in enumerate(validaciones, 1):
                ValidacionPaso.objects.create(paso=paso, orden=i, **datos_validacion)

    @transaction.atomic
    def create(self, datos: dict) -> ProcedimientoGuiado:
        """Crea el procedimiento con sus pasos."""
        pasos = datos.pop("pasos")
        request = self.context.get("request")
        usuario = getattr(request, "user", None)
        procedimiento = ProcedimientoGuiado.objects.create(
            codigo=_siguiente_codigo(),
            creado_por=usuario if usuario is not None and usuario.is_authenticated else None,
            **datos,
        )
        self._crear_pasos(procedimiento, pasos)
        return procedimiento

    @transaction.atomic
    def update(self, procedimiento: ProcedimientoGuiado, datos: dict) -> ProcedimientoGuiado:
        """Reemplaza los datos y los pasos, y sube la version."""
        pasos = datos.pop("pasos", None)
        for campo, valor in datos.items():
            setattr(procedimiento, campo, valor)
        procedimiento.version += 1
        procedimiento.save()
        if pasos is not None:
            procedimiento.pasos.all().delete()
            self._crear_pasos(procedimiento, pasos)
        return procedimiento


def copiar(procedimiento: ProcedimientoGuiado, usuario) -> ProcedimientoGuiado:
    """Duplica un procedimiento con sus pasos, como punto de partida de otro.

    Args:
        procedimiento: El original.
        usuario: Quien hace la copia.

    Returns:
        La copia, en version 1 y sin programas asignados.
    """
    datos = ProcedimientoGuiadoSerializer(procedimiento).data
    serializador = ProcedimientoGuiadoSerializer(
        data={
            "nombre": f"{datos['nombre']} (copia)"[:120],
            "descripcion": datos["descripcion"],
            "tipo_red": datos["tipo_red"],
            "pasos": datos["pasos"],
        },
        context={"request": SimpleNamespace(user=usuario)},
    )
    serializador.is_valid(raise_exception=True)
    return serializador.save()
