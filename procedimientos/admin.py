"""Admin de procedimientos guiados."""

from django.contrib import admin

from .models import PasoGuiado, ProcedimientoGuiado, ValidacionPaso


class PasoGuiadoInline(admin.StackedInline):
    """Pasos del procedimiento, en orden."""

    model = PasoGuiado
    extra = 0
    ordering = ("orden",)
    show_change_link = True


class ValidacionPasoInline(admin.TabularInline):
    """Validaciones del paso, en orden."""

    model = ValidacionPaso
    extra = 0
    ordering = ("orden",)


@admin.register(ProcedimientoGuiado)
class ProcedimientoGuiadoAdmin(admin.ModelAdmin):
    """Procedimientos guiados."""

    list_display = ("codigo", "nombre", "version", "tipo_red", "activo")
    list_filter = ("tipo_red", "activo")
    search_fields = ("codigo", "nombre")
    inlines = [PasoGuiadoInline]
    readonly_fields = ("creado_en",)


@admin.register(PasoGuiado)
class PasoGuiadoAdmin(admin.ModelAdmin):
    """Pasos guiados, con sus validaciones."""

    list_display = ("__str__", "procedimiento", "fase")
    list_filter = ("procedimiento",)
    inlines = [ValidacionPasoInline]
