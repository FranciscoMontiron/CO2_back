"""Admin de configuracion, umbrales, alertas y emergencias."""

from django.contrib import admin

from .models import Alerta, Configuracion, EventoEmergencia, Umbral


class UmbralInline(admin.TabularInline):
    """Umbrales de la configuracion."""

    model = Umbral
    extra = 1
    fields = ("variable", "valor_min", "valor_max", "severidad", "accion", "activo")


@admin.register(Configuracion)
class ConfiguracionAdmin(admin.ModelAdmin):
    """Configuraciones de operacion. Se versionan, no se editan."""

    list_display = ("nombre", "version", "activa", "creada_en", "definida_por")
    list_filter = ("activa",)
    inlines = [UmbralInline]
    readonly_fields = ("creada_en",)


@admin.register(Umbral)
class UmbralAdmin(admin.ModelAdmin):
    """Umbrales por variable monitoreada."""

    list_display = ("variable", "valor_min", "valor_max", "severidad", "accion", "activo")
    list_filter = ("variable", "severidad", "accion", "activo")


@admin.register(Alerta)
class AlertaAdmin(admin.ModelAdmin):
    """Alertas generadas por umbrales excedidos."""

    list_display = ("fecha_hora", "severidad", "valor_medido", "reconocida", "reconocida_por")
    list_filter = ("severidad", "reconocida")
    date_hierarchy = "fecha_hora"
    readonly_fields = ("fecha_hora",)


@admin.register(EventoEmergencia)
class EventoEmergenciaAdmin(admin.ModelAdmin):
    """Paradas de emergencia. `tiempo_respuesta_ms` es la evidencia de RNF001."""

    list_display = ("fecha_hora", "origen", "tiempo_respuesta_ms", "cumple_rnf001", "rearmado_en")
    list_filter = ("origen",)
    date_hierarchy = "fecha_hora"
    readonly_fields = ("fecha_hora",)

    @admin.display(boolean=True, description="cumple RNF001")
    def cumple_rnf001(self, obj) -> bool:
        """Indica si la parada entro en el presupuesto de 500 ms."""
        return obj.cumple_rnf001
