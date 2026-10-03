"""Configuracion de operacion, umbrales, alertas y emergencias.

Aca vive el puente entre lo que el lazo de control mide y lo que el sistema hace
al respecto: ``Umbral`` evalua una lectura, y segun su ``accion`` genera una
``Alerta``, bloquea comandos o dispara una parada que queda asentada como
``EventoEmergencia``.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, Value, When
from django.utils import timezone

from comun.enums import AccionUmbral, OrigenEmergencia, Severidad, VariableMonitoreada


class Configuracion(models.Model):
    """Juego de umbrales con el que opera el sistema.

    Se versiona en vez de editarse: un ensayo tiene que poder decir con que
    umbrales corrio, y si la configuracion se modificara en el lugar esa
    pregunta dejaria de tener respuesta.
    """

    nombre = models.CharField("nombre", max_length=120)
    descripcion = models.TextField("descripcion", blank=True)
    version = models.PositiveIntegerField("version", default=1)
    activa = models.BooleanField("activa", default=False)
    # Mismo motivo que en PicoDeAtenuacion: MySQL no tiene unicos parciales, asi
    # que la unicidad de "hay una sola vigente" se apoya en una columna generada
    # que vale 1 solo para la activa y NULL para el resto.
    _activa_unica = models.GeneratedField(
        expression=Case(When(activa=True, then=Value(1)), default=Value(None)),
        output_field=models.IntegerField(null=True),
        db_persist=True,
        verbose_name="indice de unicidad de la vigente",
    )
    creada_en = models.DateTimeField("creada en", auto_now_add=True)
    definida_por = models.ForeignKey(
        "usuarios.Usuario",
        verbose_name="definida por",
        on_delete=models.SET_NULL,
        related_name="configuraciones",
        null=True,
        blank=True,
    )

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "configuracion"
        verbose_name_plural = "configuraciones"
        ordering = ["nombre", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["nombre", "version"], name="configuracion_nombre_version_unica"
            ),
            # A lo sumo una configuracion vigente. Dos activas significaria que
            # el controlador no sabe cual cargar, y lo descubriria en marcha.
            models.UniqueConstraint(fields=["_activa_unica"], name="una_sola_configuracion_activa"),
        ]

    def __str__(self) -> str:
        """Devuelve el nombre y la version."""
        return f"{self.nombre} v{self.version}"

    def activar(self) -> None:
        """Pone esta configuracion en vigencia y desactiva la anterior."""
        Configuracion.objects.filter(activa=True).exclude(pk=self.pk).update(activa=False)
        self.activa = True
        self.save(update_fields=["activa"])


class Umbral(models.Model):
    """Rango admisible para una variable monitoreada.

    Solo aplica a las variables **analogicas**: las senales digitales
    (fibra alineada, shutter abierto/cerrado) son interlocks booleanos y se
    evaluan como checklist de armado, no como rango (ADR-0007).
    """

    configuracion = models.ForeignKey(
        Configuracion,
        verbose_name="configuracion",
        on_delete=models.CASCADE,
        related_name="umbrales",
    )
    variable = models.CharField("variable", max_length=25, choices=VariableMonitoreada.choices)
    valor_min = models.FloatField("valor minimo")
    valor_max = models.FloatField("valor maximo")
    severidad = models.CharField(
        "severidad", max_length=12, choices=Severidad.choices, default=Severidad.ADVERTENCIA
    )
    accion = models.CharField(
        "accion", max_length=20, choices=AccionUmbral.choices, default=AccionUmbral.ALERTA
    )
    activo = models.BooleanField("activo", default=True)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "umbral"
        verbose_name_plural = "umbrales"
        ordering = ["configuracion", "variable"]
        constraints = [
            models.UniqueConstraint(
                fields=["configuracion", "variable"],
                name="un_umbral_por_variable_y_configuracion",
            ),
            models.CheckConstraint(
                condition=models.Q(valor_min__lt=models.F("valor_max")),
                name="umbral_min_menor_que_max",
            ),
        ]

    def __str__(self) -> str:
        """Devuelve la variable y su rango."""
        return f"{self.get_variable_display()}: [{self.valor_min}, {self.valor_max}]"

    def evaluar(self, valor: float) -> bool:
        """Indica si una lectura esta dentro del rango admisible.

        Args:
            valor: Lectura de la variable.

        Returns:
            ``True`` si la lectura esta dentro del rango (o el umbral esta
            desactivado), ``False`` si lo excede.
        """
        if not self.activo:
            return True
        return self.valor_min <= valor <= self.valor_max

    def disparar(self, valor: float) -> Alerta:
        """Genera la alerta correspondiente a una lectura fuera de rango.

        Args:
            valor: Lectura que excedio el umbral.

        Returns:
            La :class:`Alerta` creada.
        """
        extremo = "por debajo del minimo" if valor < self.valor_min else "por encima del maximo"
        return Alerta.objects.create(
            umbral=self,
            descripcion=(
                f"{self.get_variable_display()} = {valor}: {extremo} "
                f"[{self.valor_min}, {self.valor_max}]."
            ),
            sugerencia=_SUGERENCIAS.get(self.variable, ""),
            severidad=self.severidad,
            valor_medido=valor,
        )


# Que hacer ante cada variable fuera de rango. Vive en el codigo y no en la base
# porque es conocimiento del dominio, no configuracion del operador.
_SUGERENCIAS = {
    VariableMonitoreada.TEMPERATURA_AGUA: (
        "Verificar el chiller y el caudal antes de reanudar: el tubo de CO2 no "
        "tolera operar fuera de rango termico."
    ),
    VariableMonitoreada.CAUDAL_REFRIGERANTE: (
        "Revisar bomba, mangueras y filtro. Sin caudal el tubo se dana en segundos."
    ),
    VariableMonitoreada.POTENCIA_LASER: (
        "Contrastar contra la potencia objetivo del programa y revisar la alineacion."
    ),
    VariableMonitoreada.TENSION_AT: "Revisar la fuente de alta tension.",
    VariableMonitoreada.CORRIENTE_AT: (
        "Corriente fuera de rango con tension normal sugiere problema de descarga."
    ),
    VariableMonitoreada.POSICION_MOTOR: (
        "Verificar fines de carrera y volver a referenciar el eje."
    ),
    VariableMonitoreada.TEMPERATURA_AMBIENTE: "Revisar la ventilacion del laboratorio.",
}


class Alerta(models.Model):
    """Aviso generado por una lectura fuera de umbral."""

    umbral = models.ForeignKey(
        Umbral,
        verbose_name="umbral",
        on_delete=models.SET_NULL,
        related_name="alertas",
        null=True,
    )
    fecha_hora = models.DateTimeField("fecha y hora", auto_now_add=True, db_index=True)
    descripcion = models.TextField("descripcion")
    sugerencia = models.TextField("sugerencia", blank=True)
    severidad = models.CharField("severidad", max_length=12, choices=Severidad.choices)
    valor_medido = models.FloatField("valor medido")
    reconocida = models.BooleanField("reconocida", default=False, db_index=True)
    reconocida_por = models.ForeignKey(
        "usuarios.Usuario",
        verbose_name="reconocida por",
        on_delete=models.SET_NULL,
        related_name="alertas_reconocidas",
        null=True,
        blank=True,
    )
    reconocida_en = models.DateTimeField("reconocida en", null=True, blank=True)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "alerta"
        verbose_name_plural = "alertas"
        ordering = ["-fecha_hora"]

    def __str__(self) -> str:
        """Devuelve la severidad y la descripcion."""
        return f"[{self.severidad}] {self.descripcion[:60]}"

    def reconocer(self, usuario) -> None:
        """Deja constancia de que un operador vio la alerta.

        Args:
            usuario: Quien la reconoce.

        Raises:
            ValidationError: Si la alerta ya estaba reconocida. Reconocerla dos
                veces borraria quien la vio primero.
        """
        if self.reconocida:
            raise ValidationError("La alerta ya fue reconocida.")
        self.reconocida = True
        self.reconocida_por = usuario
        self.reconocida_en = timezone.now()
        self.save(update_fields=["reconocida", "reconocida_por", "reconocida_en"])


class EventoEmergencia(models.Model):
    """Parada de emergencia asentada.

    ``tiempo_respuesta_ms`` **es la evidencia con la que se verifica RNF001**
    (comandos criticos <= 500 ms). No es un campo informativo: es el dato que se
    audita.

    Cuando el origen es ``BOTON_FISICO``, el sistema solo *registra*: la parada
    la ejecuto el interlock por hardware, cortando el enable de la fuente HV y el
    shutter electricamente, sin software en el camino (ADR-0002).
    """

    fecha_hora = models.DateTimeField("fecha y hora", auto_now_add=True, db_index=True)
    origen = models.CharField("origen", max_length=15, choices=OrigenEmergencia.choices)
    descripcion = models.TextField("descripcion", blank=True)
    tiempo_respuesta_ms = models.PositiveIntegerField(
        "tiempo de respuesta [ms]",
        help_text="Evidencia de RNF001: el presupuesto son 500 ms.",
    )
    rearmado_en = models.DateTimeField("rearmado en", null=True, blank=True)
    rearmado_por = models.ForeignKey(
        "usuarios.Usuario",
        verbose_name="rearmado por",
        on_delete=models.SET_NULL,
        related_name="emergencias_rearmadas",
        null=True,
        blank=True,
    )
    registro = models.ForeignKey(
        "trazabilidad.RegistroDeFabricacion",
        verbose_name="registro interrumpido",
        on_delete=models.SET_NULL,
        related_name="emergencias",
        null=True,
        blank=True,
    )

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "evento de emergencia"
        verbose_name_plural = "eventos de emergencia"
        ordering = ["-fecha_hora"]

    def __str__(self) -> str:
        """Devuelve el origen y el tiempo de respuesta."""
        return f"{self.get_origen_display()} ({self.tiempo_respuesta_ms} ms)"

    @property
    def cumple_rnf001(self) -> bool:
        """Indica si la parada respeto el presupuesto de 500 ms de RNF001."""
        return self.tiempo_respuesta_ms <= 500

    def rearmar(self, usuario) -> None:
        """Rearma el sistema despues de una parada.

        El rearme es siempre explicito: que el sistema vuelva solo despues de una
        emergencia es exactamente lo que no debe pasar.

        Args:
            usuario: Quien rearma.

        Raises:
            ValidationError: Si el evento ya fue rearmado.
        """
        if self.rearmado_en is not None:
            raise ValidationError("El evento ya fue rearmado.")
        self.rearmado_en = timezone.now()
        self.rearmado_por = usuario
        self.save(update_fields=["rearmado_en", "rearmado_por"])
