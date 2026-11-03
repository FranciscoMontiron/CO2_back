"""Procedimientos guiados: la checklist que sigue el operador en «Nuevo ensayo».

No confundir con:

* ``fabricacion.Procedimiento``: la receta de *medicion* en el OSA.
* ``programas.Paso``: las instrucciones (codigos G) que ejecuta el controlador.

Un ``ProcedimientoGuiado`` es una secuencia de ``PasoGuiado``. Cada paso agrupa
``ValidacionPaso``: lo que el operador tiene que dejar verificado, cada una con
su propio *detalle* (como se hace). El paso no tiene instrucciones generales:
la explicacion vive en la validacion a la que corresponde.

Los procedimientos reales del CIOp son mayormente manuales (OSA, empalmadora,
motor) y no lineales: "si fabrica otra, vaya al paso 6; si termina, al 7". Por
eso un paso puede declarar ``salidas`` hacia otros pasos, y los sensores, los
botones de comando y los indicadores son opcionales.

Indicadores, acciones, salidas y condiciones se guardan como JSON con un
vocabulario cerrado que valida :mod:`procedimientos.catalogo`. No se guarda
codigo: el front interpreta esos datos, nunca los ejecuta.
"""

from __future__ import annotations

from django.db import models

from comun.enums import TipoRed, TipoValidacion


class ProcedimientoGuiado(models.Model):
    """Checklist guiada, reutilizable por varios programas de grabado."""

    codigo = models.CharField("codigo", max_length=40, unique=True)
    nombre = models.CharField("nombre", max_length=120)
    descripcion = models.TextField("descripcion", blank=True)
    tipo_red = models.CharField(
        "tipo de red", max_length=10, choices=TipoRed.choices, blank=True, default=""
    )
    version = models.PositiveIntegerField("version", default=1)
    activo = models.BooleanField("activo", default=True)
    creado_por = models.ForeignKey(
        "usuarios.Usuario",
        verbose_name="creado por",
        on_delete=models.SET_NULL,
        related_name="procedimientos_guiados",
        null=True,
        blank=True,
    )
    creado_en = models.DateTimeField("creado en", auto_now_add=True)
    actualizado_en = models.DateTimeField("actualizado en", auto_now=True)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "procedimiento guiado"
        verbose_name_plural = "procedimientos guiados"
        ordering = ["codigo"]

    def __str__(self) -> str:
        """Devuelve el codigo, el nombre y la version."""
        return f"{self.codigo} {self.nombre} v{self.version}"


class PasoGuiado(models.Model):
    """Un paso del procedimiento guiado."""

    procedimiento = models.ForeignKey(
        ProcedimientoGuiado,
        verbose_name="procedimiento",
        on_delete=models.CASCADE,
        related_name="pasos",
    )
    orden = models.PositiveIntegerField("orden")
    titulo = models.CharField("titulo", max_length=120)
    subtitulo = models.CharField("subtitulo", max_length=160, blank=True)
    fase = models.CharField(
        "fase",
        max_length=40,
        help_text="Etapa del indicador superior. Pasos seguidos con la misma fase se agrupan.",
    )
    indicadores = models.JSONField(
        "indicadores", default=list, blank=True, help_text="Chips de estado del equipo."
    )
    acciones = models.JSONField(
        "acciones", default=list, blank=True, help_text="Botones que mandan comandos."
    )
    condicion_avance = models.JSONField(
        "condicion de avance",
        null=True,
        blank=True,
        help_text="Lo que tiene que ser cierto en el equipo para avanzar, ademas de validar.",
    )
    salidas = models.JSONField(
        "salidas",
        default=list,
        blank=True,
        help_text="Bifurcaciones a otros pasos. Vacio: sigue al paso siguiente.",
    )

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "paso guiado"
        verbose_name_plural = "pasos guiados"
        ordering = ["procedimiento", "orden"]
        constraints = [
            models.UniqueConstraint(
                fields=["procedimiento", "orden"], name="paso_guiado_orden_unico"
            )
        ]

    def __str__(self) -> str:
        """Devuelve el orden y el titulo."""
        return f"{self.orden}. {self.titulo}"


class ValidacionPaso(models.Model):
    """Algo que tiene que quedar verificado para cerrar un paso."""

    paso = models.ForeignKey(
        PasoGuiado, verbose_name="paso", on_delete=models.CASCADE, related_name="validaciones"
    )
    orden = models.PositiveIntegerField("orden")
    texto = models.CharField("texto", max_length=200)
    detalle = models.TextField(
        "detalle", blank=True, help_text="Como se hace. El operador lo despliega si lo necesita."
    )
    tipo = models.CharField(
        "tipo", max_length=10, choices=TipoValidacion.choices, default=TipoValidacion.MANUAL
    )
    condicion = models.JSONField(
        "condicion",
        null=True,
        blank=True,
        help_text="Solo para las validaciones por sensor.",
    )

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "validacion de paso"
        verbose_name_plural = "validaciones de paso"
        ordering = ["paso", "orden"]
        constraints = [
            models.UniqueConstraint(fields=["paso", "orden"], name="validacion_paso_orden_unico")
        ]

    def __str__(self) -> str:
        """Devuelve el texto de la validacion."""
        return self.texto
