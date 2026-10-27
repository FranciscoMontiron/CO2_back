"""Serializadores de usuarios, sesion y auditoria."""

import json

from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.signals import user_logged_in
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from comun.enums import TipoRol
from comun.permisos import rol_de

from .models import RegistroAuditoria, Rol, Usuario


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


class UsuarioAdminSerializer(serializers.ModelSerializer):
    """Usuario completo, para el ABM del administrador (RF011, RN011).

    Agrega a :class:`UsuarioSerializer` los datos que solo el administrador ve y
    edita. La contrasena entra pero nunca sale, y se guarda hasheada.
    """

    nombre = serializers.SerializerMethodField()
    rol = serializers.ChoiceField(choices=TipoRol.choices)
    activo = serializers.BooleanField(source="is_active", required=False)
    ultimo_acceso = serializers.DateTimeField(source="last_login", read_only=True)
    password = serializers.CharField(
        write_only=True, required=False, style={"input_type": "password"}
    )

    class Meta:
        """Metadatos del serializador."""

        model = Usuario
        fields = [
            "id",
            "username",
            "nombre",
            "first_name",
            "last_name",
            "email",
            "legajo",
            "rol",
            "activo",
            "ultimo_acceso",
            "fecha_baja",
            "password",
        ]
        read_only_fields = ["fecha_baja"]

    def get_nombre(self, obj) -> str:
        """Nombre completo, o el usuario si no lo cargo."""
        return str(obj)

    def to_representation(self, instance) -> dict:
        """Devuelve el rol como codigo, igual que el serializador publico."""
        datos = super().to_representation(instance)
        datos["rol"] = rol_de(instance)
        return datos

    def validate_legajo(self, valor):
        """Un legajo vacio se guarda como nulo: es unico, y dos vacios chocarian."""
        return valor or None

    def validate_rol(self, valor: str) -> Rol:
        """Convierte el codigo de rol en el rol de la base."""
        try:
            return Rol.objects.get(codigo=valor)
        except Rol.DoesNotExist as exc:
            raise serializers.ValidationError("Ese rol no esta cargado en el sistema.") from exc

    def validate_password(self, valor: str) -> str:
        """Aplica los validadores de contrasena de Django."""
        validate_password(valor, user=self.instance)
        return valor

    def validate(self, attrs: dict) -> dict:
        """Exige contrasena en el alta e impide que el administrador se bloquee.

        Sin esta ultima regla, un administrador podria quitarse el rol o darse de
        baja a si mismo y dejar el sistema sin nadie que lo administre.
        """
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError(
                {"password": "La contrasena es obligatoria en el alta."}
            )
        solicitante = getattr(self.context.get("request"), "user", None)
        if self.instance is None or solicitante is None or self.instance.pk != solicitante.pk:
            return attrs
        if attrs.get("is_active") is False:
            raise serializers.ValidationError({"activo": "No puede darse de baja a si mismo."})
        rol = attrs.get("rol")
        se_quita_admin = rol is not None and rol.codigo != TipoRol.ADMINISTRADOR
        if se_quita_admin and not solicitante.is_superuser:
            raise serializers.ValidationError({"rol": "No puede quitarse el rol de administrador."})
        return attrs

    def _aplicar_estado(self, usuario: Usuario, attrs: dict) -> None:
        """Mantiene la fecha de baja coherente con ``activo``."""
        if "is_active" not in attrs:
            return
        if attrs["is_active"] and usuario.fecha_baja is not None:
            usuario.fecha_baja = None
        elif not attrs["is_active"] and usuario.fecha_baja is None:
            usuario.fecha_baja = timezone.now()

    def create(self, validated_data: dict) -> Usuario:
        """Da de alta al usuario con la contrasena hasheada."""
        clave = validated_data.pop("password")
        usuario = Usuario(**validated_data)
        usuario.set_password(clave)
        usuario.save()
        return usuario

    def update(self, instance: Usuario, validated_data: dict) -> Usuario:
        """Modifica al usuario; la contrasena solo cambia si se envia."""
        clave = validated_data.pop("password", None)
        self._aplicar_estado(instance, validated_data)
        for campo, valor in validated_data.items():
            setattr(instance, campo, valor)
        if clave:
            instance.set_password(clave)
        instance.save()
        return instance


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
