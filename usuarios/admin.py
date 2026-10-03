"""Admin de usuarios, roles y auditoria."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import RegistroAuditoria, Rol, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    """Usuarios del sistema.

    Extiende el admin de Django en vez de rehacerlo: asi se conservan el cambio
    de contrasena seguro y el filtrado de permisos que ya trae.
    """

    list_display = ("username", "first_name", "last_name", "rol", "is_active", "last_login")
    list_filter = ("rol", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (
        ("Datos del laboratorio", {"fields": ("legajo", "rol", "fecha_baja")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Datos del laboratorio", {"fields": ("legajo", "rol")}),
    )


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    """Roles y sus permisos."""

    list_display = ("name", "codigo", "activo", "cantidad_permisos")
    list_filter = ("activo",)
    filter_horizontal = ("permissions",)

    @admin.display(description="permisos")
    def cantidad_permisos(self, obj) -> int:
        """Cuenta los permisos concedidos al rol."""
        return obj.permissions.count()


@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(admin.ModelAdmin):
    """Bitacora de auditoria (RN010).

    Solo lectura, en linea con la inmutabilidad del modelo: si el admin
    permitiera editar, la garantia dependeria de que nadie entre por ahi.
    """

    list_display = ("fecha_hora", "usuario", "accion", "entidad", "codigo_entidad", "critica")
    list_filter = ("accion", "critica")
    search_fields = ("entidad", "codigo_entidad", "usuario__username")
    date_hierarchy = "fecha_hora"

    def has_add_permission(self, request) -> bool:
        """Los asientos los escribe el sistema, no una persona."""
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        """La auditoria es inmutable (RN010)."""
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        """La auditoria no se borra (RN010)."""
        return False
