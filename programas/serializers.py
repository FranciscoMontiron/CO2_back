"""Serializador de programas en su representacion de laboratorio.

El laboratorio describe un programa con cuatro numeros: potencia objetivo,
duracion de pulso, desplazamiento por pulso y un criterio de fin. El modelo los
reparte en tres objetos, y este serializador hace la traduccion en los dos
sentidos:

=========================  ================================================
Campo de la API            Donde vive en el modelo
=========================  ================================================
``perfil_periodo``         ``Periodo.tipo`` (constante, lineal o exponencial)
``desplazamiento_um``      ``Periodo.periodo_base``: el periodo de la primera marca
``incremento_um``          ``Periodo.incremento`` (solo perfil lineal)
``factor``                 ``Periodo.factor`` (solo perfil exponencial)
``duracion_pulso_ms``      ``Paso`` de grabado: ``tiempo_de_pulso``
``pulsos``                 ``Paso`` de grabado: ``repeticiones``
``distancia_mm``           ``valor_criterio_fin`` si el criterio es LONGITUD
``potencia_objetivo_mw``   ``Programa.potencia_objetivo``, guardada en W
=========================  ================================================

La distancia y la cantidad de pulsos no son independientes: la longitud es la
suma de los periodos de las marcas (``marcas x periodo`` si el perfil es
constante). Segun el criterio de fin, una se ingresa y la otra se deriva.

Los programas multipaso que se puedan cargar por el admin se leen igual, pero
esta representacion solo los describe en terminos de su paso de grabado.
"""

from __future__ import annotations

import math

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from comun.enums import CriterioFin, TipoPeriodo, TipoRed

from .models import Paso, Periodo, Programa

# Criterios que admite el formulario de laboratorio. TIEMPO y ATENUACION_OBJETIVO
# existen en el dominio pero necesitan el lazo de control para tener sentido.
CRITERIOS_EDITABLES = [CriterioFin.LONGITUD, CriterioFin.CANT_MARCAS, CriterioFin.MANUAL]

ETIQUETA_GRABADO = "G01"

# Tope de marcas al derivar los pulsos de una longitud con perfil variable: corta
# un perfil que casi no crece en vez de iterar sin fin.
MAXIMO_MARCAS = 100_000


def _marcas_hasta(periodo: Periodo, longitud_um: float) -> int:
    """Cuenta cuantas marcas entran en ``longitud_um`` con el perfil dado.

    Args:
        periodo: Perfil de periodo (no hace falta que este guardado).
        longitud_um: Longitud disponible en micrometros.

    Returns:
        La cantidad de marcas completas.

    Raises:
        ValueError: Si el periodo deja de ser positivo o las marcas superan el tope.
    """
    if periodo.tipo == TipoPeriodo.CONSTANTE:
        return math.floor(longitud_um / periodo.periodo_base)
    acumulado, n = 0.0, 0
    while n < MAXIMO_MARCAS:
        distancia = periodo.calcular_distancia(n)
        if distancia <= 0:
            raise ValueError(f"El periodo deja de ser positivo en la marca {n + 1}.")
        if acumulado + distancia > longitud_um:
            return n
        acumulado += distancia
        n += 1
    raise ValueError(f"La longitud requiere mas de {MAXIMO_MARCAS} marcas.")


def _paso_de_grabado(programa: Programa) -> Paso | None:
    """Devuelve el paso que dispara el laser, o el primero si ninguno lo hace."""
    pasos = list(programa.pasos.all())
    return next((p for p in pasos if p.tiempo_de_pulso > 0), pasos[0] if pasos else None)


def _siguiente_codigo() -> str:
    """Genera el proximo codigo libre con la forma ``PRG-001``."""
    numeros = [
        int(c.split("-")[-1])
        for c in Programa.objects.filter(codigo__regex=r"^PRG-\d+$").values_list(
            "codigo", flat=True
        )
    ]
    return f"PRG-{(max(numeros) + 1 if numeros else 1):03d}"


class ProgramaSerializer(serializers.ModelSerializer):
    """Programa de grabado en terminos de laboratorio."""

    potencia_objetivo_mw = serializers.FloatField(min_value=0)
    duracion_pulso_ms = serializers.IntegerField(min_value=1)
    perfil_periodo = serializers.ChoiceField(
        choices=TipoPeriodo.choices, default=TipoPeriodo.CONSTANTE
    )
    desplazamiento_um = serializers.FloatField(min_value=0.001)
    incremento_um = serializers.FloatField(required=False, allow_null=True)
    factor = serializers.FloatField(min_value=0.001, required=False, allow_null=True)
    criterio_fin = serializers.ChoiceField(choices=CriterioFin.choices)
    distancia_mm = serializers.FloatField(min_value=0, required=False, allow_null=True)
    pulsos = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    creado_por = serializers.SerializerMethodField()

    class Meta:
        """Metadatos del serializador."""

        model = Programa
        fields = [
            "codigo",
            "nombre",
            "descripcion",
            "tipo_red",
            "version",
            "potencia_objetivo_mw",
            "duracion_pulso_ms",
            "perfil_periodo",
            "desplazamiento_um",
            "incremento_um",
            "factor",
            "criterio_fin",
            "distancia_mm",
            "pulsos",
            "validado",
            "activo",
            "creado_por",
            "actualizado_en",
        ]
        read_only_fields = ["codigo", "version", "validado", "activo", "creado_por", "actualizado_en"]
        extra_kwargs = {"tipo_red": {"required": False}, "descripcion": {"required": False}}

    # ── lectura ─────────────────────────────────────────────────────────────

    def to_representation(self, programa: Programa) -> dict:
        """Arma la representacion de laboratorio a partir del modelo.

        Args:
            programa: El programa a serializar.

        Returns:
            El diccionario con los campos de la API.
        """
        paso = _paso_de_grabado(programa)
        periodo = programa.periodo
        desplazamiento = periodo.periodo_base if periodo else 0.0

        if programa.criterio_fin == CriterioFin.CANT_MARCAS and programa.valor_criterio_fin:
            pulsos = int(programa.valor_criterio_fin)
        elif programa.criterio_fin == CriterioFin.LONGITUD and periodo:
            pulsos = _marcas_hasta(periodo, (programa.valor_criterio_fin or 0) * 1000)
        else:
            pulsos = paso.repeticiones if paso else 0

        if programa.criterio_fin == CriterioFin.LONGITUD:
            distancia = programa.valor_criterio_fin or 0.0
        else:
            distancia = periodo.distancia_acumulada(pulsos) / 1000 if periodo else 0.0

        return {
            "codigo": programa.codigo,
            "nombre": programa.nombre,
            "descripcion": programa.descripcion,
            "version": programa.version,
            "tipo_red": programa.tipo_red,
            "potencia_objetivo_mw": round(programa.potencia_objetivo * 1000, 6),
            "duracion_pulso_ms": paso.tiempo_de_pulso if paso else 0,
            "perfil_periodo": periodo.tipo if periodo else TipoPeriodo.CONSTANTE,
            "desplazamiento_um": desplazamiento,
            "incremento_um": periodo.incremento if periodo else None,
            "factor": periodo.factor if periodo else None,
            "criterio_fin": programa.criterio_fin,
            "distancia_mm": round(distancia, 6),
            "pulsos": pulsos,
            "validado": programa.validado,
            "activo": programa.activo,
            "creado_por": self.get_creado_por(programa),
            "actualizado_en": programa.actualizado_en.isoformat(),
        }

    def get_creado_por(self, programa: Programa) -> str:
        """Nombre de quien creo el programa."""
        return str(programa.creado_por) if programa.creado_por else ""

    # ── escritura ───────────────────────────────────────────────────────────

    def validate(self, datos: dict) -> dict:
        """Completa la cantidad que el criterio de fin deja derivada.

        Args:
            datos: Los campos ya validados individualmente.

        Returns:
            Los datos con ``distancia_mm`` y ``pulsos`` consistentes entre si.

        Raises:
            ValidationError: Si falta el valor que el criterio necesita.
        """
        criterio = datos.get("criterio_fin")
        if criterio not in CRITERIOS_EDITABLES:
            raise serializers.ValidationError(
                {"criterio_fin": "Desde la API solo se admiten LONGITUD, CANT_MARCAS y MANUAL."}
            )
        periodo = self._periodo_de(datos)
        distancia, pulsos = datos.get("distancia_mm"), datos.get("pulsos")

        if criterio == CriterioFin.LONGITUD:
            if not distancia:
                raise serializers.ValidationError(
                    {"distancia_mm": "El criterio LONGITUD necesita la distancia total."}
                )
            try:
                datos["pulsos"] = _marcas_hasta(periodo, distancia * 1000)
            except ValueError as exc:
                raise serializers.ValidationError({"perfil_periodo": str(exc)}) from exc
        elif criterio == CriterioFin.CANT_MARCAS:
            if not pulsos:
                raise serializers.ValidationError(
                    {"pulsos": "El criterio CANT_MARCAS necesita la cantidad de pulsos."}
                )
            if periodo.calcular_distancia(pulsos - 1) <= 0:
                raise serializers.ValidationError(
                    {"perfil_periodo": "El periodo deja de ser positivo antes de la ultima marca."}
                )
            datos["distancia_mm"] = periodo.distancia_acumulada(pulsos) / 1000
        else:  # MANUAL: termina cuando el operador decide
            datos["pulsos"] = pulsos or 0
            datos["distancia_mm"] = distancia
        return datos

    @staticmethod
    def _periodo_de(datos: dict) -> Periodo:
        """Arma, sin guardar, el perfil de periodo que describen los datos.

        Raises:
            ValidationError: Si faltan o sobran parametros para el perfil elegido.
        """
        periodo = Periodo(
            tipo=datos.get("perfil_periodo", TipoPeriodo.CONSTANTE),
            periodo_base=datos["desplazamiento_um"],
            incremento=datos.get("incremento_um"),
            factor=datos.get("factor"),
        )
        try:
            periodo.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"perfil_periodo": exc.messages}) from exc
        return periodo

    def _valor_criterio(self, datos: dict) -> float | None:
        """Elige que numero se guarda como valor del criterio de fin."""
        if datos["criterio_fin"] == CriterioFin.LONGITUD:
            return datos["distancia_mm"]
        if datos["criterio_fin"] == CriterioFin.CANT_MARCAS:
            return float(datos["pulsos"])
        return None

    def _guardar_periodo(self, periodo: Periodo | None, datos: dict) -> Periodo:
        """Crea o actualiza el perfil de periodo con los datos validados."""
        if periodo is None:
            periodo = Periodo()
        periodo.tipo = datos.get("perfil_periodo", TipoPeriodo.CONSTANTE)
        periodo.periodo_base = datos["desplazamiento_um"]
        periodo.incremento = datos.get("incremento_um")
        periodo.factor = datos.get("factor")
        periodo.save()
        return periodo

    @transaction.atomic
    def create(self, datos: dict) -> Programa:
        """Crea el programa con su periodo y su paso de grabado, y lo valida.

        Args:
            datos: Los datos validados.

        Returns:
            El programa creado.
        """
        periodo = self._guardar_periodo(None, datos)
        usuario = self.context["request"].user
        programa = Programa.objects.create(
            codigo=_siguiente_codigo(),
            nombre=datos["nombre"],
            descripcion=datos.get("descripcion", ""),
            tipo_red=datos.get("tipo_red", TipoRed.LPG),
            potencia_objetivo=datos["potencia_objetivo_mw"] / 1000,
            criterio_fin=datos["criterio_fin"],
            valor_criterio_fin=self._valor_criterio(datos),
            periodo=periodo,
            creado_por=usuario if usuario.is_authenticated else None,
        )
        Paso.objects.create(
            programa=programa,
            orden=1,
            etiqueta=ETIQUETA_GRABADO,
            parametro=datos["desplazamiento_um"],
            repeticiones=datos["pulsos"],
            tiempo_de_pulso=datos["duracion_pulso_ms"],
        )
        programa.validar()
        return programa

    @transaction.atomic
    def update(self, programa: Programa, datos: dict) -> Programa:
        """Actualiza el programa, su periodo y su paso de grabado.

        Args:
            programa: El programa existente.
            datos: Los datos validados.

        Returns:
            El programa actualizado.
        """
        programa.periodo = self._guardar_periodo(programa.periodo, datos)
        # Cada edicion es una version nueva: los ensayos ya grabados citan el
        # codigo, y la version dice con que receta se corrio cada tanda.
        programa.version += 1

        programa.nombre = datos["nombre"]
        programa.descripcion = datos.get("descripcion", programa.descripcion)
        programa.tipo_red = datos.get("tipo_red", programa.tipo_red)
        programa.potencia_objetivo = datos["potencia_objetivo_mw"] / 1000
        programa.criterio_fin = datos["criterio_fin"]
        programa.valor_criterio_fin = self._valor_criterio(datos)
        programa.save()

        paso = _paso_de_grabado(programa)
        if paso is None:
            paso = Paso(programa=programa, orden=1, etiqueta=ETIQUETA_GRABADO)
        paso.parametro = datos["desplazamiento_um"]
        paso.repeticiones = datos["pulsos"]
        paso.tiempo_de_pulso = datos["duracion_pulso_ms"]
        paso.save()

        programa.validar()
        return programa
