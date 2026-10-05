"""Programas de grabado: la receta que el controlador ejecuta.

Un ``Programa`` es una secuencia ordenada de ``Paso``, mas el perfil de
``Periodo`` que define como se espacian las marcas a lo largo de la fibra.

El programa es **lo que se disena**; la ``Red`` es lo que sale. Por eso el
periodo cuelga de aca y no de la red: se define antes de fabricar.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from comun.enums import CriterioFin, TipoPeriodo, TipoRed


class Periodo(models.Model):
    """Perfil de variacion del periodo a lo largo de la red.

    El Diagrama de Clases lo modela como jerarquia con tres subclases. Aca se
    aplana a una tabla con discriminador ``tipo`` y la estrategia resuelta en
    :meth:`calcular_distancia`. El motivo es concreto: las subclases aportan cero
    y un atributo, y la herencia multitabla de Django costaria un JOIN en cada
    lectura de programa a cambio de ninguna ventaja.
    """

    tipo = models.CharField(
        "tipo", max_length=15, choices=TipoPeriodo.choices, default=TipoPeriodo.CONSTANTE
    )
    periodo_base = models.FloatField("periodo base [um]", validators=[MinValueValidator(1e-6)])
    incremento = models.FloatField(
        "incremento [um]",
        null=True,
        blank=True,
        help_text="Solo para el perfil LINEAL.",
    )
    factor = models.FloatField(
        "factor",
        null=True,
        blank=True,
        help_text="Solo para el perfil EXPONENCIAL.",
    )

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "periodo"
        verbose_name_plural = "periodos"

    def __str__(self) -> str:
        """Devuelve el tipo de perfil y su periodo base."""
        return f"{self.get_tipo_display()} de {self.periodo_base} um"

    def clean(self) -> None:
        """Valida que esten los parametros que el perfil elegido necesita.

        Raises:
            ValidationError: Si falta el parametro del perfil, o si se carga uno
                que no corresponde.
        """
        if self.tipo == TipoPeriodo.LINEAL and self.incremento is None:
            raise ValidationError({"incremento": "El perfil LINEAL necesita un incremento."})
        if self.tipo == TipoPeriodo.EXPONENCIAL and self.factor is None:
            raise ValidationError({"factor": "El perfil EXPONENCIAL necesita un factor."})
        if self.tipo == TipoPeriodo.CONSTANTE and (
            self.incremento is not None or self.factor is not None
        ):
            raise ValidationError(
                "El perfil CONSTANTE no lleva incremento ni factor: dejarlos cargados "
                "sugiere que se cambio el tipo y quedo basura de la version anterior."
            )

    def calcular_distancia(self, n: int) -> float:
        """Devuelve el periodo correspondiente a la marca ``n``.

        Args:
            n: Indice de la marca, empezando en 0.

        Returns:
            El periodo en micrometros para esa marca.

        Raises:
            ValueError: Si el tipo de perfil no esta contemplado.
        """
        if self.tipo == TipoPeriodo.CONSTANTE:
            return self.periodo_base
        if self.tipo == TipoPeriodo.LINEAL:
            return self.periodo_base + (self.incremento or 0.0) * n
        if self.tipo == TipoPeriodo.EXPONENCIAL:
            return self.periodo_base * ((self.factor or 1.0) ** n)
        raise ValueError(f"Perfil de periodo no contemplado: {self.tipo}")

    def distancia_acumulada(self, cantidad_marcas: int) -> float:
        """Suma los periodos de las primeras ``cantidad_marcas`` marcas.

        Es lo que da la longitud total de la red. Para el perfil constante el
        resultado es ``periodo_base x marcas``, el invariante que el Diagrama de
        Clases deja asentado.

        Args:
            cantidad_marcas: Cuantas marcas acumular.

        Returns:
            La distancia total en micrometros.
        """
        return sum(self.calcular_distancia(i) for i in range(cantidad_marcas))


class Programa(models.Model):
    """Receta de grabado: que hacer, en que orden y hasta cuando."""

    codigo = models.CharField("codigo", max_length=40, unique=True)
    nombre = models.CharField("nombre", max_length=120)
    descripcion = models.TextField("descripcion", blank=True)
    version = models.PositiveIntegerField("version", default=1)
    tipo_red = models.CharField("tipo de red", max_length=10, choices=TipoRed.choices)
    potencia_objetivo = models.FloatField("potencia objetivo [W]")
    criterio_fin = models.CharField("criterio de fin", max_length=25, choices=CriterioFin.choices)
    valor_criterio_fin = models.FloatField("valor del criterio de fin", null=True, blank=True)
    periodo = models.OneToOneField(
        Periodo,
        verbose_name="periodo",
        on_delete=models.PROTECT,
        related_name="programa",
        null=True,
        blank=True,
    )
    validado = models.BooleanField("validado", default=False)
    activo = models.BooleanField("activo", default=True)
    creado_por = models.ForeignKey(
        "usuarios.Usuario",
        verbose_name="creado por",
        on_delete=models.SET_NULL,
        related_name="programas",
        null=True,
        blank=True,
    )
    creado_en = models.DateTimeField("creado en", auto_now_add=True)
    actualizado_en = models.DateTimeField("actualizado en", auto_now=True)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "programa"
        verbose_name_plural = "programas"
        ordering = ["codigo", "-version"]

    def __str__(self) -> str:
        """Devuelve el codigo y la version."""
        return f"{self.codigo} v{self.version}"

    def validar(self) -> bool:
        """Verifica que el programa sea ejecutable.

        Un programa sin pasos, con el orden duplicado o sin el valor que su
        criterio de fin necesita no puede llegar al controlador: seria una
        parada a mitad de una fibra.

        Returns:
            ``True`` si el programa quedo validado.

        Raises:
            ValidationError: Con el detalle de lo que falta.
        """
        errores = []
        pasos = list(self.pasos.all())
        if not pasos:
            errores.append("El programa no tiene pasos.")
        ordenes = [p.orden for p in pasos]
        if len(ordenes) != len(set(ordenes)):
            errores.append("Hay pasos con el mismo orden.")
        if self.criterio_fin != CriterioFin.MANUAL and self.valor_criterio_fin is None:
            errores.append(f"El criterio de fin {self.criterio_fin} necesita un valor.")
        if self.periodo is None:
            errores.append("El programa no tiene definido un perfil de periodo.")
        if errores:
            raise ValidationError(errores)

        self.validado = True
        self.save(update_fields=["validado"])
        return True

    def duracion_estimada(self) -> int:
        """Estima cuanto tarda la corrida.

        Suma el tiempo de pulso de cada paso por sus repeticiones. Es una cota
        inferior: no contempla el desplazamiento del motor entre marcas, que
        depende del perfil de periodo y de la velocidad del eje.

        Returns:
            La duracion estimada en milisegundos.
        """
        return sum(paso.duracion_estimada() for paso in self.pasos.all())


class Paso(models.Model):
    """Instruccion individual de un programa.

    ``etiqueta`` es el codigo G y ``parametro`` su argumento, tal como lo deja
    asentado la nota del Diagrama de Clases.
    """

    programa = models.ForeignKey(
        Programa, verbose_name="programa", on_delete=models.CASCADE, related_name="pasos"
    )
    orden = models.PositiveIntegerField("orden")
    etiqueta = models.CharField("etiqueta (codigo G)", max_length=20)
    parametro = models.FloatField("parametro", null=True, blank=True)
    repeticiones = models.PositiveIntegerField("repeticiones", default=1)
    tiempo_de_pulso = models.PositiveIntegerField(
        "tiempo de pulso [ms]",
        default=0,
        help_text="Cero para instrucciones que no disparan el laser.",
    )

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "paso"
        verbose_name_plural = "pasos"
        # La composicion del diagrama es {ordered}: un programa es una secuencia,
        # no un conjunto. El orden es parte del dato, no una preferencia de
        # presentacion.
        ordering = ["programa", "orden"]
        constraints = [
            models.UniqueConstraint(fields=["programa", "orden"], name="paso_programa_orden_unico")
        ]

    def __str__(self) -> str:
        """Devuelve el orden y la etiqueta del paso."""
        return f"{self.orden}. {self.etiqueta}"

    def duracion_estimada(self) -> int:
        """Estima la duracion del paso.

        Returns:
            ``tiempo_de_pulso x repeticiones`` en milisegundos.
        """
        return self.tiempo_de_pulso * self.repeticiones
