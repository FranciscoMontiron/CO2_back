"""Admin de fabricacion y caracterizacion.

El espectro no se muestra crudo en ninguna lista: son miles de puntos por fila y
cargarlos convertiria cada listado en una consulta pesada sin que nadie los lea.
"""

from django.contrib import admin

from .models import Espectro, Lote, Marca, PicoDeAtenuacion, Procedimiento, Red


class MarcaInline(admin.TabularInline):
    """Marcas grabadas sobre una red."""

    model = Marca
    extra = 0
    fields = ("numero", "posicion", "cant_pulsos", "ciclo_trabajo", "tiempo_de_pulso")


class PicoInline(admin.TabularInline):
    """Resonancias detectadas en el espectro de la red."""

    model = PicoDeAtenuacion
    extra = 0
    fields = ("longitud_onda", "transmitancia", "principal")


@admin.register(Lote)
class LoteAdmin(admin.ModelAdmin):
    """Lotes de fabricacion."""

    list_display = ("numero", "fecha_inicio", "cantidad_redes")
    search_fields = ("numero", "descripcion")
    date_hierarchy = "fecha_inicio"

    @admin.display(description="redes")
    def cantidad_redes(self, obj) -> int:
        """Cuenta las redes del lote."""
        return obj.redes.count()


@admin.register(Red)
class RedAdmin(admin.ModelAdmin):
    """Redes fabricadas."""

    list_display = ("codigo", "lote", "tipo", "estado", "fecha_fabricacion", "marcas_grabadas")
    list_filter = ("tipo", "estado", "lote")
    search_fields = ("numero", "lote__numero")
    inlines = [MarcaInline, PicoInline]

    @admin.display(description="marcas")
    def marcas_grabadas(self, obj) -> int:
        """Cantidad de marcas de la red."""
        return obj.cantidad_marcas


@admin.register(Procedimiento)
class ProcedimientoAdmin(admin.ModelAdmin):
    """Recetas de medicion en el OSA."""

    list_display = ("titulo", "version", "span", "resolucion", "cantidad_de_puntos")
    search_fields = ("titulo",)


@admin.register(Espectro)
class EspectroAdmin(admin.ModelAdmin):
    """Capturas del OSA."""

    list_display = ("red", "procedimiento", "fecha_captura", "cantidad_de_puntos")
    list_filter = ("procedimiento",)
    # `transmitancias` puede tener decenas de miles de valores: editarlo a mano
    # no tiene sentido y renderizarlo cuelga el formulario.
    exclude = ("transmitancias",)
    readonly_fields = ("cantidad_de_puntos",)


@admin.register(PicoDeAtenuacion)
class PicoDeAtenuacionAdmin(admin.ModelAdmin):
    """Resonancias detectadas."""

    list_display = ("red", "longitud_onda", "transmitancia", "principal")
    list_filter = ("principal", "red__lote")
