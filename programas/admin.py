"""Admin de programas de grabado."""

from django.contrib import admin

from .models import Paso, Periodo, Programa


class PasoInline(admin.TabularInline):
    """Pasos del programa, en orden."""

    model = Paso
    extra = 1
    fields = ("orden", "etiqueta", "parametro", "repeticiones", "tiempo_de_pulso")
    ordering = ("orden",)


@admin.register(Programa)
class ProgramaAdmin(admin.ModelAdmin):
    """Programas de grabado."""

    list_display = ("codigo", "nombre", "version", "tipo_red", "validado", "activo")
    list_filter = ("tipo_red", "validado", "activo", "criterio_fin")
    search_fields = ("codigo", "nombre")
    inlines = [PasoInline]
    readonly_fields = ("validado", "creado_en")


@admin.register(Periodo)
class PeriodoAdmin(admin.ModelAdmin):
    """Perfiles de periodo."""

    list_display = ("__str__", "tipo", "periodo_base", "incremento", "factor")
    list_filter = ("tipo",)
