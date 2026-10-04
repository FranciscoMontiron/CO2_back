"""Serializadores de usuarios, sesion y auditoria."""

import json

from django.contrib.auth.signals import user_logged_in
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from comun.permisos import rol_de

from .models import RegistroAuditoria, Usuario


class UsuarioSerializer(serializers.ModelSerializer):
    """Usuario, tal como lo ve el resto del sistema."""

    nombre = serializers.SerializerMethodField()
    rol = serializers.SerializerMethodField()
    activo = serializers.BooleanField(source="is_active")
    ultimo_acceso = serializers.DateTimeField(source="last_login")

    class Meta:
        """Metadatos del serializador."""

        model = Usuario
        fields = ["id", "username", "nombre", "rol", "activo", "ultimo_acceso"]

    def get_nombre(self, obj) -> str:
        """Nombre completo, o el usuario si no lo cargo."""
        return str(obj)

    def get_rol(self, obj) -> str | None:
        """Codigo del rol del usuario."""
        return rol_de(obj)


class IniciarSesionSerializer(TokenObtainPairSerializer):
    """Emite el par de tokens JWT y devuelve quien inicio sesion.

    simplejwt no dispara la senal ``user_logged_in`` de Django. Sin ella el
    inicio de sesion **no quedaria auditado** (RN010) y ``last_login`` no se
    actualizaria: se dispara a mano aca, con la peticion, para que la auditoria
    registre tambien la IP.
    """

    def validate(self, attrs: dict) -> dict:
        """Valida las credenciales y completa la respuesta.

        Args:
            attrs: Usuario y contrasena enviados.

        Returns:
            Los tokens mas los datos del usuario.
        """
        datos = super().validate(attrs)
        user_logged_in.send(
            sender=self.user.__class__, request=self.context.get("request"), user=self.user
        )
        datos["usuario"] = UsuarioSerializer(self.user).data
        return datos


class AuditoriaSerializer(serializers.ModelSerializer):
    """Asiento de auditoria con un resumen legible."""

    usuario = serializers.SerializerMethodField()
    detalle = serializers.SerializerMethodField()

    class Meta:
        """Metadatos del serializador."""

        model = RegistroAuditoria
        fields = [
            "id",
            "fecha_hora",
            "usuario",
            "accion",
            "entidad",
            "codigo_entidad",
            "critica",
            "direccion_ip",
            "detalle",
        ]

    def get_usuario(self, obj) -> str:
        """Usuario que hizo la accion, o ``sistema`` si no hubo persona."""
        return obj.usuario.username if obj.usuario else "sistema"

    def get_detalle(self, obj) -> str:
        """Resume el asiento en una linea.

        En las modificaciones nombra los campos que cambiaron: la bitacora solo
        guarda esos (ver ``usuarios/auditoria.py``).
        """
        entidad = obj.entidad.split(".")[-1]
        ref = f"{entidad} #{obj.codigo_entidad}" if obj.codigo_entidad else entidad
        if obj.accion == "LOGIN":
            return "Inicio de sesion"
        if obj.accion == "CREAR":
            return f"Alta de {ref}"
        if obj.accion == "ELIMINAR":
            return f"Baja de {ref}"
        if obj.accion == "MODIFICAR":
            try:
                campos = ", ".join(json.loads(obj.valor_nuevo or "{}"))
            except json.JSONDecodeError:
                campos = ""
            return f"{ref}: cambio {campos}" if campos else f"Modificacion de {ref}"
        return ref
