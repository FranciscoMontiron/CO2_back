"""Admin de trazabilidad de la fabricacion."""

from django.contrib import admin

from .models import Checkpoint, RegistroDeFabricacion


class CheckpointInline(admin.TabularInline):
    """Puntos de control de la corrida."""

    model = Checkpoint
    extra = 0
    fields = ("numero", "tipo", "fecha_hora", "paso", "estado_sistema", "valores")
    readonly_fields = ("fecha_hora",)


@admin.register(RegistroDeFabricacion)
class RegistroDeFabricacionAdmin(admin.ModelAdmin):
    """Corridas de fabricacion: el ensayo de RF006/RF007."""

    list_display = ("codigo", "programa", "red", "modo", "estado", "inicio", "duracion")
    list_filter = ("estado", "modo", "calibracion_arco")
    search_fields = ("codigo", "observaciones")
    date_hierarchy = "inicio"
    filter_horizontal = ("operadores",)
    inlines = [CheckpointInline]


@admin.register(Checkpoint)
class CheckpointAdmin(admin.ModelAdmin):
    """Puntos de control con las lecturas del instante."""

    list_display = ("registro", "numero", "tipo", "fecha_hora", "estado_sistema")
    list_filter = ("tipo", "estado_sistema")
