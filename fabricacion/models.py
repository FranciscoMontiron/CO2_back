"""Fabricacion y caracterizacion optica de redes.

Cubre el ciclo completo de una red: el lote que la agrupa, las marcas que el
laser graba sobre la fibra, y la caracterizacion posterior en el analizador de
espectro optico (OSA).

Una decision que atraviesa todo el modulo: **el espectro no se guarda en la fila
de** ``Red``. Vive en :class:`Espectro`, en su propia tabla, para que listar
redes no arrastre miles de puntos por fila (ADR-0007).
"""

from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Case, F, Value, When

from comun.enums import EstadoRed, TipoRed


class Procedimiento(models.Model):
    """Receta de medicion en el OSA.

    Es reutilizable: describe *como* se mide, no *que* se midio. El resultado de
    aplicarla es un :class:`Espectro`.
    """

    titulo = models.CharField("titulo", max_length=120)
    descripcion = models.TextField("descripcion", blank=True)
    version = models.CharField("version", max_length=20, default="1.0")
    span = models.FloatField("span [nm]", validators=[MinValueValidator(0.0)])
    resolucion = models.FloatField("resolucion [nm]", validators=[MinValueValidator(1e-6)])
    sensibilidad = models.CharField("sensibilidad", max_length=40, blank=True)
    escala_vertical = models.FloatField("escala vertical [dB/div]", null=True, blank=True)
    corriente_sld = models.FloatField("corriente del SLD [mA]", null=True, blank=True)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "procedimiento de medicion"
        verbose_name_plural = "procedimientos de medicion"
        ordering = ["titulo", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["titulo", "version"], name="procedimiento_titulo_version_unico"
            )
        ]

    def __str__(self) -> str:
        """Devuelve titulo y version."""
        return f"{self.titulo} v{self.version}"

    @property
    def cantidad_de_puntos(self) -> int:
        """Cantidad de puntos que produce un barrido con esta receta.

        Returns:
            ``span / resolucion`` redondeado hacia abajo. Es lo que determina el
            tamano de cada :class:`Espectro`, y por lo tanto el volumen de la
            base (RNF008).
        """
        return int(self.span / self.resolucion)

    def importar(self, red: Red, contenido_csv: str, fecha_captura=None) -> Espectro:
        """Crea el espectro de una red a partir del CSV exportado por el OSA.

        Se guardan las longitudes de onda tal como vienen: el OSA puede exportar
        el barrido diezmado con paso variable, asi que no se asume grilla
        regular (ADR-0008). Si la red ya tenia espectro, se reemplaza.

        Args:
            red: La red caracterizada.
            contenido_csv: El texto del archivo exportado por el OSA.
            fecha_captura: Cuando se midio. Por defecto, ahora.

        Returns:
            El :class:`Espectro` creado o actualizado.

        Raises:
            EspectroInvalidoError: Si el CSV no es legible o sus longitudes de onda
                no son estrictamente crecientes.
        """
        from django.utils import timezone

        from .caracterizacion import leer_csv_osa, verificar_orden

        puntos = leer_csv_osa(contenido_csv)
        verificar_orden(puntos)
        espectro, _ = Espectro.objects.update_or_create(
            red=red,
            defaults={
                "procedimiento": self,
                "fecha_captura": fecha_captura or timezone.now(),
                "longitudes_onda": [nm for nm, _ in puntos],
                "transmitancias": [db for _, db in puntos],
            },
        )
        return espectro


class Lote(models.Model):
    """Conjunto de redes fabricadas bajo las mismas condiciones."""

    numero = models.PositiveIntegerField("numero", unique=True)
    descripcion = models.TextField("descripcion", blank=True)
    fecha_inicio = models.DateField("fecha de inicio")

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "lote"
        verbose_name_plural = "lotes"
        ordering = ["-numero"]

    def __str__(self) -> str:
        """Devuelve el numero de lote."""
        return f"Lote {self.numero}"

    def promedio_longitud_onda(self) -> float | None:
        """Promedia la longitud de onda de la resonancia principal del lote.

        Solo promedia redes viables: incluir las rotas o inviables mezclaria
        mediciones de redes que no son el producto.

        Returns:
            El promedio en nm, o ``None`` si todavia no hay redes caracterizadas.
        """
        return PicoDeAtenuacion.objects.filter(
            red__lote=self, red__estado=EstadoRed.VIABLE, principal=True
        ).aggregate(models.Avg("longitud_onda"))["longitud_onda__avg"]

    def promedio_transmitancia(self) -> float | None:
        """Promedia la transmitancia de la resonancia principal del lote.

        Returns:
            El promedio en dB, o ``None`` si todavia no hay redes caracterizadas.
        """
        return PicoDeAtenuacion.objects.filter(
            red__lote=self, red__estado=EstadoRed.VIABLE, principal=True
        ).aggregate(models.Avg("transmitancia"))["transmitancia__avg"]


class Red(models.Model):
    """Red de periodo largo grabada sobre la fibra.

    Los atributos derivados del diagrama (``/codigo``, ``/cantidadMarcas``,
    ``/longitud``, ``/curva``) se exponen como propiedades y **no** se persisten:
    guardarlos abriria la puerta a que queden desincronizados de las marcas
    reales, que es la unica fuente de verdad.
    """

    numero = models.PositiveIntegerField("numero")
    lote = models.ForeignKey(
        Lote, verbose_name="lote", on_delete=models.PROTECT, related_name="redes"
    )
    tipo = models.CharField("tipo", max_length=10, choices=TipoRed.choices)
    estado = models.CharField(
        "estado", max_length=10, choices=EstadoRed.choices, default=EstadoRed.VIABLE
    )
    fecha_fabricacion = models.DateField("fecha de fabricacion")

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "red"
        verbose_name_plural = "redes"
        ordering = ["-lote__numero", "numero"]
        constraints = [
            models.UniqueConstraint(fields=["lote", "numero"], name="red_lote_numero_unico")
        ]

    def __str__(self) -> str:
        """Devuelve el codigo de la red."""
        return self.codigo

    @property
    def codigo(self) -> str:
        """Codigo identificatorio de la red, derivado del lote y su numero."""
        return self.generar_codigo()

    def generar_codigo(self) -> str:
        """Compone el codigo de la red.

        Returns:
            Un codigo con la forma ``L003-R012-LPG``.
        """
        return f"L{self.lote.numero:03d}-R{self.numero:03d}-{self.tipo}"

    @property
    def cantidad_marcas(self) -> int:
        """Cantidad de marcas grabadas sobre la red."""
        return self.marcas.count()

    @property
    def longitud(self) -> float | None:
        """Longitud de la red en mm.

        Se calcula como ``cantidad de marcas x periodo``, el invariante que el
        Diagrama de Clases deja asentado. El periodo lo define el programa, asi
        que si la red no tiene fabricacion asociada no hay con que calcularlo.

        Returns:
            La longitud en mm, o ``None`` si no se puede derivar.
        """
        registro = self.registros.first()
        if registro is None or registro.programa.periodo is None:
            return None
        periodo = registro.programa.periodo
        marcas = self.cantidad_marcas
        if marcas == 0:
            return 0.0
        # calcular_distancia devuelve micrometros acumulados; la red se expresa
        # en milimetros, que es como la mide el laboratorio.
        return periodo.distancia_acumulada(marcas) / 1000.0

    def resonancia_principal(self) -> PicoDeAtenuacion | None:
        """Devuelve la resonancia principal de la red.

        Returns:
            El :class:`PicoDeAtenuacion` marcado como principal, o ``None`` si la
            red todavia no fue caracterizada.
        """
        return self.picos.filter(principal=True).first()


class Marca(models.Model):
    """Marca individual grabada por el laser sobre la fibra."""

    red = models.ForeignKey(
        Red, verbose_name="red", on_delete=models.CASCADE, related_name="marcas"
    )
    numero = models.PositiveIntegerField("numero")
    posicion = models.FloatField("posicion [um]")
    cant_pulsos = models.PositiveIntegerField("cantidad de pulsos")
    ciclo_trabajo = models.FloatField("ciclo de trabajo [%]")
    tiempo_de_pulso = models.PositiveIntegerField("tiempo de pulso [ms]")

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "marca"
        verbose_name_plural = "marcas"
        ordering = ["red", "numero"]
        constraints = [
            models.UniqueConstraint(fields=["red", "numero"], name="marca_red_numero_unica")
        ]

    def __str__(self) -> str:
        """Devuelve la marca y la red a la que pertenece."""
        return f"{self.red.codigo} marca {self.numero}"


class Espectro(models.Model):
    """Captura cruda del OSA para una red.

    Guarda las longitudes de onda **y** las transmitancias, como dos arreglos
    paralelos. En M1 se guardaba solo la longitud inicial y se reconstruia el eje
    asumiendo grilla regular; el primer CSV real del laboratorio (un Yokogawa
    AQ6370B) vino diezmado con cinco pasos distintos, de 1 a 10 nm, y ese supuesto
    se cayo. Ver ADR-0008.

    Es ``OneToOne`` con ``Red`` porque se asumio que cada red se caracteriza una
    sola vez. Si el laboratorio vuelve a medir redes viejas, pasar a varias
    capturas es cambiar este campo por una ``ForeignKey`` y mover
    :class:`PicoDeAtenuacion` para que cuelgue de aca: no hay que rehacer el
    esquema (ADR-0007, pendiente 1).
    """

    red = models.OneToOneField(
        Red, verbose_name="red", on_delete=models.CASCADE, related_name="espectro"
    )
    procedimiento = models.ForeignKey(
        Procedimiento,
        verbose_name="procedimiento",
        on_delete=models.PROTECT,
        related_name="espectros",
    )
    fecha_captura = models.DateTimeField("fecha de captura")
    longitudes_onda = models.JSONField("longitudes de onda [nm]", default=list)
    transmitancias = models.JSONField("transmitancias [dB]", default=list)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "espectro"
        verbose_name_plural = "espectros"
        ordering = ["-fecha_captura"]

    def __str__(self) -> str:
        """Devuelve la red y la fecha de captura."""
        return f"Espectro de {self.red.codigo} ({self.fecha_captura:%Y-%m-%d})"

    @property
    def cantidad_de_puntos(self) -> int:
        """Cantidad de muestras del barrido."""
        return len(self.transmitancias)

    @property
    def longitud_onda_inicial(self) -> float | None:
        """Primera longitud de onda del barrido, o ``None`` si esta vacio."""
        return self.longitudes_onda[0] if self.longitudes_onda else None

    def longitudes_de_onda(self) -> list[float]:
        """Devuelve el eje de longitudes de onda tal como lo exporto el OSA.

        Returns:
            Las longitudes de onda en nm, del mismo largo que ``transmitancias``.
        """
        return list(self.longitudes_onda)

    def puntos(self) -> list[tuple[float, float]]:
        """Devuelve el espectro como pares (longitud de onda, transmitancia).

        Returns:
            Lista de tuplas ``(nm, dB)`` lista para graficar.
        """
        return list(zip(self.longitudes_de_onda(), self.transmitancias, strict=True))

    @transaction.atomic
    def detectar_resonancias(
        self, profundidad_minima: float = 3.0, ancho_minimo: int = 1
    ) -> list[PicoDeAtenuacion]:
        """Detecta las resonancias del espectro y las asienta en la red.

        Reemplaza las detecciones anteriores en lugar de sumarse a ellas: correr
        la deteccion dos veces tiene que dar el mismo resultado, no el doble de
        picos. La mas profunda queda marcada como principal.

        El Diagrama de Clases pone este metodo en ``Procedimiento``; vive aca
        porque opera sobre los datos de una captura concreta, y el procedimiento
        es solo la receta.

        Args:
            profundidad_minima: Cuanto debajo de la linea de base tiene que caer
                un valle para contar como resonancia, en dB.
            ancho_minimo: Puntos consecutivos minimos del valle. Filtra ruido.

        Returns:
            Los :class:`PicoDeAtenuacion` creados, ordenados por longitud de onda.
        """
        from .caracterizacion import detectar_minimos

        indices = detectar_minimos(self.transmitancias, profundidad_minima, ancho_minimo)
        lambdas = self.longitudes_de_onda()

        self.red.picos.all().delete()
        if not indices:
            return []

        mas_profundo = min(indices, key=lambda i: self.transmitancias[i])
        return PicoDeAtenuacion.objects.bulk_create(
            [
                PicoDeAtenuacion(
                    red=self.red,
                    longitud_onda=lambdas[i],
                    transmitancia=self.transmitancias[i],
                    principal=(i == mas_profundo),
                )
                for i in indices
            ]
        )


class PicoDeAtenuacion(models.Model):
    """Resonancia detectada en el espectro de una red.

    Es la parte consultable de la caracterizacion: lo que el laboratorio compara
    entre redes. Lo extrae ``detectar_resonancias()`` a partir del
    :class:`Espectro`, en vez de tipearse a mano.
    """

    red = models.ForeignKey(Red, verbose_name="red", on_delete=models.CASCADE, related_name="picos")
    longitud_onda = models.FloatField("longitud de onda [nm]", db_index=True)
    transmitancia = models.FloatField("transmitancia [dB]")
    principal = models.BooleanField("es la principal", default=False)
    # Columna generada que vale `red_id` solo cuando el pico es el principal, y
    # NULL en el resto. Es el truco para tener un unico parcial en MySQL, que
    # NO soporta `UniqueConstraint(condition=...)`: Django lo acepta sin chistar
    # y despues no crea nada, con lo cual la garantia existiria solo en el papel.
    # Un indice unico de MySQL admite multiples NULL, asi que los picos comunes
    # no se estorban y los principales colisionan por red.
    _red_principal = models.GeneratedField(
        expression=Case(When(principal=True, then=F("red")), default=Value(None)),
        output_field=models.BigIntegerField(null=True),
        db_persist=True,
        verbose_name="indice de unicidad de la principal",
    )

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "pico de atenuacion"
        verbose_name_plural = "picos de atenuacion"
        ordering = ["red", "longitud_onda"]
        constraints = [
            # Una red tiene a lo sumo una resonancia principal. Sin esto, dos
            # corridas de deteccion podrian dejar dos principales y
            # `resonancia_principal()` devolveria cualquiera de las dos.
            models.UniqueConstraint(
                fields=["_red_principal"], name="una_sola_resonancia_principal_por_red"
            )
        ]

    def __str__(self) -> str:
        """Devuelve la longitud de onda y la atenuacion."""
        marca = " (principal)" if self.principal else ""
        return f"{self.longitud_onda:.2f} nm / {self.transmitancia:.2f} dB{marca}"
