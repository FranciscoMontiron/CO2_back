"""Serializador de programas: un programa es un arbol de pasos.

La API lee y escribe ``{"codigo", "nombre", "descripcion", "procedimiento",
"pasos": [arbol]}``. Cada nodo del arbol es un diccionario JSON (ver
``controller.programa``) que se guarda como una fila de :class:`Paso` con su
``padre``. Al guardar se valida con ``validar_programa`` contra el hardware
vigente: en GPIO, el archivo de configuracion; en simulado, el inventario fijo.

``resumen`` (solo lectura) cuenta lo que hara el programa; es ``None`` si el
arbol ya no valida contra el hardware actual (por ejemplo, un programa viejo).
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from rest_framework import serializers

from comun.enums import TipoPaso, TipoRed
from controller.hardware_config import leer_archivo, validar_configuracion
from controller.programa import INVENTARIO_SIMULADO, resumen, validar_programa
from procedimientos.models import ProcedimientoGuiado

from .models import Paso, Programa


def inventario_api() -> dict:
    """Devuelve el inventario de hardware contra el que se validan los programas.

    Returns:
        El archivo de hardware validado si ``CONTROLLER_HAL`` es ``gpio``; si no,
        el inventario simulado.

    Raises:
        ValueError: Si el archivo de hardware no es valido.
    """
    if settings.CONTROLLER_HAL == "gpio":
        return validar_configuracion(leer_archivo())
    return INVENTARIO_SIMULADO


def _nodo_de(paso: Paso, hijos_de: dict) -> dict:
    """Arma el nodo JSON de una fila, con sus hijos si es un bloque."""
    if paso.tipo == TipoPaso.ESPERA:
        return {"tipo": "ESPERA", "duracion_ms": paso.tiempo_de_pulso}
    if paso.tipo == TipoPaso.ACCION:
        return {
            "tipo": "ACCION",
            "componente": paso.componente,
            "comando": paso.comando,
            "argumentos": paso.argumentos,
        }
    hijos = [_nodo_de(h, hijos_de) for h in hijos_de.get(paso.pk, [])]
    if paso.tipo == TipoPaso.BUCLE:
        return {"tipo": "BUCLE", "repeticiones": paso.repeticiones, "pasos": hijos}
    return {"tipo": "PARALELO", "pasos": hijos}


def arbol_de_pasos(programa: Programa) -> list[dict]:
    """Reconstruye el arbol de un programa a partir de sus filas.

    Args:
        programa: El programa guardado.

    Returns:
        La lista de nodos raiz, en orden.
    """
    hijos_de: dict = {}
    for paso in sorted(programa.pasos.all(), key=lambda p: p.orden):
        hijos_de.setdefault(paso.padre_id, []).append(paso)
    return [_nodo_de(p, hijos_de) for p in hijos_de.get(None, [])]


def _guardar_nodos(programa: Programa, nodos: list[dict], padre: Paso | None = None) -> None:
    """Crea las filas de ``nodos`` (ya validados) bajo ``padre``."""
    for orden, nodo in enumerate(nodos, 1):
        datos = {"programa": programa, "padre": padre, "orden": orden, "tipo": nodo["tipo"]}
        if nodo["tipo"] == TipoPaso.ACCION:
            datos.update(
                componente=nodo["componente"],
                comando=nodo["comando"],
                argumentos=nodo["argumentos"],
            )
        elif nodo["tipo"] == TipoPaso.ESPERA:
            datos["tiempo_de_pulso"] = nodo["duracion_ms"]
        elif nodo["tipo"] == TipoPaso.BUCLE:
            datos["repeticiones"] = nodo["repeticiones"]
        paso = Paso.objects.create(**datos)
        if "pasos" in nodo:
            _guardar_nodos(programa, nodo["pasos"], paso)


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
    """Programa de grabado como arbol de pasos."""

    pasos = serializers.JSONField()
    procedimiento = serializers.SlugRelatedField(
        slug_field="codigo",
        queryset=ProcedimientoGuiado.objects.filter(activo=True),
        required=False,
        allow_null=True,
    )

    class Meta:
        """Metadatos del serializador."""

        model = Programa
        fields = ["codigo", "nombre", "descripcion", "procedimiento", "pasos"]
        read_only_fields = ["codigo"]
        extra_kwargs = {"descripcion": {"required": False}}

    # ── lectura ─────────────────────────────────────────────────────────────

    def _inventario(self) -> dict | None:
        """Inventario vigente, leido una vez por serializador (``None`` si no valida)."""
        if not hasattr(self, "_inventario_cache"):
            try:
                self._inventario_cache = inventario_api()
            except ValueError:
                self._inventario_cache = None
        return self._inventario_cache

    def _resumen_de(self, arbol: list[dict]) -> dict | None:
        inventario = self._inventario()
        if inventario is None or not arbol:
            return None
        try:
            return resumen(arbol, inventario)
        except ValueError:
            return None

    def to_representation(self, programa: Programa) -> dict:
        """Arma la representacion de la API a partir del modelo.

        Args:
            programa: El programa a serializar.

        Returns:
            El diccionario con los campos de la API.
        """
        arbol = arbol_de_pasos(programa)
        return {
            "codigo": programa.codigo,
            "nombre": programa.nombre,
            "descripcion": programa.descripcion,
            "version": programa.version,
            "tipo_red": programa.tipo_red,
            "procedimiento": programa.procedimiento.codigo if programa.procedimiento else None,
            "procedimiento_nombre": programa.procedimiento.nombre if programa.procedimiento else "",
            "pasos": arbol,
            "resumen": self._resumen_de(arbol),
            "validado": programa.validado,
            "activo": programa.activo,
            "creado_por": str(programa.creado_por) if programa.creado_por else "",
            "actualizado_en": programa.actualizado_en.isoformat(),
        }

    # ── escritura ───────────────────────────────────────────────────────────

    def validate(self, datos: dict) -> dict:
        """Valida el arbol contra el hardware vigente y lo deja normalizado.

        Args:
            datos: Los campos ya validados individualmente.

        Returns:
            Los datos con ``pasos`` normalizado.

        Raises:
            ValidationError: Con la ubicacion del problema (``Paso X.Y: ...``).
        """
        if "pasos" in datos:
            try:
                datos["pasos"] = validar_programa(datos["pasos"], inventario_api())
            except ValueError as exc:
                raise serializers.ValidationError({"pasos": [str(exc)]}) from exc
        return datos

    @transaction.atomic
    def create(self, datos: dict) -> Programa:
        """Crea el programa con su arbol de pasos; queda validado.

        Args:
            datos: Los datos validados.

        Returns:
            El programa creado.
        """
        usuario = self.context["request"].user
        programa = Programa.objects.create(
            codigo=_siguiente_codigo(),
            nombre=datos["nombre"],
            descripcion=datos.get("descripcion", ""),
            tipo_red=TipoRed.LPG,
            procedimiento=datos.get("procedimiento"),
            validado=True,
            creado_por=usuario if usuario.is_authenticated else None,
        )
        _guardar_nodos(programa, datos["pasos"])
        return programa

    @transaction.atomic
    def update(self, programa: Programa, datos: dict) -> Programa:
        """Reemplaza los datos y el arbol; cada edicion es una version nueva.

        Los ensayos ya grabados citan el codigo, y la version dice con que
        receta se corrio cada tanda.

        Args:
            programa: El programa existente.
            datos: Los datos validados.

        Returns:
            El programa actualizado.
        """
        programa.version += 1
        programa.nombre = datos.get("nombre", programa.nombre)
        programa.descripcion = datos.get("descripcion", programa.descripcion)
        if "procedimiento" in datos:
            programa.procedimiento = datos["procedimiento"]
        if "pasos" in datos:
            programa.pasos.all().delete()
            _guardar_nodos(programa, datos["pasos"])
            programa.validado = True
        programa.save()
        # La lectura posterior no debe ver filas prefetcheadas viejas.
        programa._prefetched_objects_cache = {}
        return programa
