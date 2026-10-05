"""Trazabilidad de la fabricacion (RF006, RF007).

``RegistroDeFabricacion`` es el «ensayo» de RF006/RF007: la corrida concreta de
un programa, con quien la ejecuto, cuando, con que configuracion y que salio.

``Checkpoint`` es lo que **reemplaza a la telemetria persistida**: en vez de
guardar 86.400 muestras por dia, se asientan los valores medidos en los puntos de
control del ensayo. Responde "a que temperatura y potencia se grabo esta red" con
un punado de filas por corrida (ADR-0007).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from comun.enums import EstadoEjecucion, EstadoSistema, ModoEjecucion, TipoCheckpoint


class RegistroDeFabricacion(models.Model):
    """Una corrida de un programa de grabado."""

    codigo = models.CharField("codigo", max_length=40, unique=True)
    programa = models.ForeignKey(
        "programas.Programa",
        verbose_name="programa",
        on_delete=models.PROTECT,
        related_name="registros",
    )
    red = models.ForeignKey(
        "fabricacion.Red",
        verbose_name="red producida",
        on_delete=models.SET_NULL,
        related_name="registros",
        null=True,
        blank=True,
    )
    configuracion = models.ForeignKey(
        "operacion.Configuracion",
        verbose_name="configuracion vigente",
        on_delete=models.PROTECT,
        related_name="registros",
        null=True,
        blank=True,
    )
    procedimiento = models.ForeignKey(
        "fabricacion.Procedimiento",
        verbose_name="procedimiento de medicion",
        on_delete=models.SET_NULL,
        related_name="registros",
        null=True,
        blank=True,
    )
    operadores = models.ManyToManyField(
        "usuarios.Usuario", verbose_name="operadores", related_name="registros"
    )
    modo = models.CharField(
        "modo", max_length=10, choices=ModoEjecucion.choices, default=ModoEjecucion.REAL
    )
    estado = models.CharField(
        "estado", max_length=15, choices=EstadoEjecucion.choices, default=EstadoEjecucion.EN_CURSO
    )
    inicio = models.DateTimeField("inicio", default=timezone.now)
    fin = models.DateTimeField("fin", null=True, blank=True)
    calibracion_arco = models.BooleanField("calibracion de arco", default=False)
    observaciones = models.TextField("observaciones", blank=True)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "registro de fabricacion"
        verbose_name_plural = "registros de fabricacion"
        ordering = ["-inicio"]

    def __str__(self) -> str:
        """Devuelve el codigo del registro."""
        return self.codigo

    @property
    def duracion(self) -> int | None:
        """Duracion de la corrida en segundos.

        Returns:
            Los segundos entre inicio y fin, o ``None`` si sigue en curso.
        """
        if self.fin is None:
            return None
        return int((self.fin - self.inicio).total_seconds())

    def clean(self) -> None:
        """Valida la restriccion de modo del Diagrama de Clases.

        ``{modo = REAL => 1 Red}``: una corrida real produce una red; en modo
        PRUEBA se recorre el programa sin grabar, asi que no hay ninguna.

        Raises:
            ValidationError: Si una corrida de prueba tiene red asociada.
        """
        if self.modo == ModoEjecucion.PRUEBA and self.red_id is not None:
            raise ValidationError(
                {"red": "En modo PRUEBA no se graba, asi que no puede haber red asociada."}
            )

    def registrar_checkpoint(
        self,
        tipo: str,
        estado_sistema: str,
        paso=None,
        valores: dict | None = None,
    ) -> Checkpoint:
        """Asienta un punto de control de la corrida.

        Args:
            tipo: Que disparo el checkpoint (:class:`TipoCheckpoint`).
            estado_sistema: Estado de la maquina en ese instante.
            paso: Paso del programa en ejecucion, si corresponde.
            valores: Lecturas de las variables monitoreadas en ese instante,
                como ``{"TEMPERATURA_AGUA": 21.4, ...}``.

        Returns:
            El :class:`Checkpoint` creado.
        """
        return Checkpoint.objects.create(
            registro=self,
            numero=self.checkpoints.count() + 1,
            tipo=tipo,
            paso=paso,
            estado_sistema=estado_sistema,
            valores=valores or {},
        )

    def interrumpir(self, motivo: str) -> None:
        """Marca la corrida como interrumpida y deja asentado el motivo.

        Args:
            motivo: Por que se interrumpio.
        """
        self.estado = EstadoEjecucion.INTERRUMPIDO
        self.fin = timezone.now()
        self.observaciones = f"{self.observaciones}\nInterrumpido: {motivo}".strip()
        self.save(update_fields=["estado", "fin", "observaciones"])

    def abortar(self, motivo: str) -> None:
        """Marca la corrida como abortada: se corto por una decision, no por una falla.

        Args:
            motivo: Por que se aborto.
        """
        self.estado = EstadoEjecucion.ABORTADO
        self.fin = timezone.now()
        self.observaciones = f"{self.observaciones}\nAbortado: {motivo}".strip()
        self.save(update_fields=["estado", "fin", "observaciones"])

    def finalizar(self) -> None:
        """Marca la corrida como completada."""
        self.estado = EstadoEjecucion.COMPLETADO
        self.fin = timezone.now()
        self.save(update_fields=["estado", "fin"])


class Checkpoint(models.Model):
    """Punto de control de una corrida, con las lecturas de ese instante.

    ``valores`` guarda las variables monitoreadas como ``{variable: lectura}``,
    usando las claves de :class:`comun.enums.VariableMonitoreada`. Es un JSON y no
    columnas fijas a proposito: el conjunto de variables lo fija el hardware y
    agregar una no deberia requerir una migracion sobre una tabla con historico.
    """

    registro = models.ForeignKey(
        RegistroDeFabricacion,
        verbose_name="registro",
        on_delete=models.CASCADE,
        related_name="checkpoints",
    )
    numero = models.PositiveIntegerField("numero")
    tipo = models.CharField("tipo", max_length=10, choices=TipoCheckpoint.choices)
    fecha_hora = models.DateTimeField("fecha y hora", auto_now_add=True)
    paso = models.ForeignKey(
        "programas.Paso",
        verbose_name="paso",
        on_delete=models.SET_NULL,
        related_name="checkpoints",
        null=True,
        blank=True,
    )
    estado_sistema = models.CharField(
        "estado del sistema", max_length=15, choices=EstadoSistema.choices
    )
    valores = models.JSONField("valores medidos", default=dict)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "checkpoint"
        verbose_name_plural = "checkpoints"
        ordering = ["registro", "numero"]
        constraints = [
            models.UniqueConstraint(
                fields=["registro", "numero"], name="checkpoint_registro_numero_unico"
            )
        ]

    def __str__(self) -> str:
        """Devuelve el registro y el numero de checkpoint."""
        return f"{self.registro.codigo} cp{self.numero}"
