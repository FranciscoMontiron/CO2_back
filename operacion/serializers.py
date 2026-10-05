"""Serializadores de alertas, emergencias y umbrales."""

from rest_framework import serializers

from comun.enums import UNIDAD_DE_VARIABLE, AccionUmbral, Severidad, nombre_de_variable

from .models import Alerta, EventoEmergencia, Umbral


class AlertaSerializer(serializers.ModelSerializer):
    """Alerta generada por un umbral excedido."""

    origen = serializers.SerializerMethodField()
    reconocida_por = serializers.SerializerMethodField()

    class Meta:
        """Metadatos del serializador."""

        model = Alerta
        fields = [
            "id",
            "fecha_hora",
            "severidad",
            "origen",
            "descripcion",
            "sugerencia",
            "valor_medido",
            "reconocida",
            "reconocida_por",
            "reconocida_en",
        ]

    def get_origen(self, alerta) -> str:
        """La variable que disparo la alerta, o ``Sistema`` si no hubo umbral."""
        if alerta.umbral is None:
            return "Sistema"
        return nombre_de_variable(alerta.umbral.variable)

    def get_reconocida_por(self, alerta) -> str | None:
        """Quien reconocio la alerta."""
        return str(alerta.reconocida_por) if alerta.reconocida_por else None


class EventoEmergenciaSerializer(serializers.ModelSerializer):
    """Parada de emergencia asentada."""

    origen_display = serializers.CharField(source="get_origen_display", read_only=True)
    cumple_rnf001 = serializers.BooleanField(read_only=True)
    rearmado_por = serializers.SerializerMethodField()
    registro = serializers.SerializerMethodField()

    class Meta:
        """Metadatos del serializador."""

        model = EventoEmergencia
        fields = [
            "id",
            "fecha_hora",
            "origen",
            "origen_display",
            "descripcion",
            "tiempo_respuesta_ms",
            "cumple_rnf001",
            "rearmado_en",
            "rearmado_por",
            "registro",
        ]

    def get_rearmado_por(self, evento) -> str | None:
        """Quien rearmo el sistema."""
        return str(evento.rearmado_por) if evento.rearmado_por else None

    def get_registro(self, evento) -> str | None:
        """Codigo del ensayo interrumpido, si lo hubo."""
        return evento.registro.codigo if evento.registro else None


class UmbralSerializer(serializers.ModelSerializer):
    """Umbral de la configuracion vigente."""

    parametro = serializers.SerializerMethodField()
    unidad = serializers.SerializerMethodField()
    critico = serializers.SerializerMethodField()

    class Meta:
        """Metadatos del serializador."""

        model = Umbral
        fields = [
            "id",
            "variable",
            "parametro",
            "valor_min",
            "valor_max",
            "unidad",
            "severidad",
            "accion",
            "critico",
            "activo",
        ]

    def get_parametro(self, umbral) -> str:
        """Nombre legible de la variable, sin la unidad."""
        return nombre_de_variable(umbral.variable)

    def get_unidad(self, umbral) -> str:
        """Unidad de la variable."""
        return UNIDAD_DE_VARIABLE.get(umbral.variable, "")

    def get_critico(self, umbral) -> bool:
        """Es critico si es de severidad critica o si para la maquina."""
        return (
            umbral.severidad == Severidad.CRITICA or umbral.accion == AccionUmbral.PARADA_EMERGENCIA
        )
