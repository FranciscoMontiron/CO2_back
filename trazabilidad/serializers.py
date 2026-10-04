"""Serializadores de los ensayos (``RegistroDeFabricacion``).

El listado lleva solo lo que muestra la tabla del historial. El detalle agrega
la caracterizacion (procedimiento y resonancias). La curva del espectro va por
un endpoint aparte: son cientos o miles de puntos que no tienen por que viajar
en cada listado (ADR-0007).
"""

from rest_framework import serializers

from .models import RegistroDeFabricacion


class _OperadorSerializer(serializers.Serializer):
    """Operador de una corrida."""

    username = serializers.CharField()
    nombre = serializers.SerializerMethodField()

    def get_nombre(self, usuario) -> str:
        """Nombre completo, o el usuario si no lo cargo."""
        return str(usuario)


class EnsayoSerializer(serializers.ModelSerializer):
    """Ensayo tal como aparece en el historial."""

    duracion_s = serializers.IntegerField(source="duracion", read_only=True)
    programa = serializers.SerializerMethodField()
    operadores = _OperadorSerializer(many=True, read_only=True)
    red = serializers.SerializerMethodField()

    class Meta:
        """Metadatos del serializador."""

        model = RegistroDeFabricacion
        fields = [
            "codigo",
            "estado",
            "modo",
            "inicio",
            "fin",
            "duracion_s",
            "observaciones",
            "programa",
            "operadores",
            "red",
        ]

    def get_programa(self, registro) -> dict:
        """Programa con el que se grabo."""
        return {"codigo": registro.programa.codigo, "nombre": registro.programa.nombre}

    def get_red(self, registro) -> dict | None:
        """Red producida, o ``None`` en modo PRUEBA."""
        red = registro.red
        if red is None:
            return None
        return {
            "codigo": red.codigo,
            "lote": red.lote.numero,
            "numero": red.numero,
            "tipo": red.tipo,
            "estado": red.estado,
        }


class EnsayoDetalleSerializer(EnsayoSerializer):
    """Ensayo con su caracterizacion completa."""

    periodo_um = serializers.SerializerMethodField()
    procedimiento = serializers.SerializerMethodField()
    resonancias = serializers.SerializerMethodField()
    tiene_espectro = serializers.SerializerMethodField()

    class Meta(EnsayoSerializer.Meta):
        """Metadatos del serializador."""

        fields = EnsayoSerializer.Meta.fields + [
            "periodo_um",
            "procedimiento",
            "resonancias",
            "tiene_espectro",
        ]

    def get_red(self, registro) -> dict | None:
        """Red producida, con sus derivados de fabricacion."""
        datos = super().get_red(registro)
        if datos is not None:
            datos["cantidad_marcas"] = registro.red.cantidad_marcas
            datos["longitud_mm"] = registro.red.longitud
        return datos

    def get_periodo_um(self, registro) -> float | None:
        """Periodo base del programa: el desplazamiento entre marcas."""
        periodo = registro.programa.periodo
        return periodo.periodo_base if periodo else None

    def get_procedimiento(self, registro) -> dict | None:
        """Receta con la que se caracterizo la red en el OSA.

        Se prefiere la del espectro (es la que efectivamente se uso) y si no hay
        espectro todavia, la que se asigno a la corrida.
        """
        espectro = getattr(registro.red, "espectro", None) if registro.red else None
        proc = espectro.procedimiento if espectro else registro.procedimiento
        if proc is None:
            return None
        return {
            "titulo": proc.titulo,
            "version": proc.version,
            "span_nm": proc.span,
            "resolucion_nm": proc.resolucion,
            "sensibilidad": proc.sensibilidad,
            "escala_vertical_db_div": proc.escala_vertical,
            "corriente_sld_ma": proc.corriente_sld,
        }

    def get_resonancias(self, registro) -> list[dict]:
        """Resonancias de la red, la principal primero y despues por profundidad."""
        if registro.red is None:
            return []
        picos = sorted(registro.red.picos.all(), key=lambda p: (not p.principal, p.transmitancia))
        return [
            {
                "longitud_onda_nm": p.longitud_onda,
                "transmitancia_db": p.transmitancia,
                "principal": p.principal,
            }
            for p in picos
        ]

    def get_tiene_espectro(self, registro) -> bool:
        """Indica si hay curva para pedir a ``/curva/``."""
        return bool(registro.red and hasattr(registro.red, "espectro"))
