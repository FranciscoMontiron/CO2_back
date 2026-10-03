"""Usuarios, roles, permisos y auditoria.

El Diagrama de Clases modela ``Usuario``, ``Rol`` y ``Permiso`` desde cero. Como
UML esta bien, pero implementarlo literal seria un error: Django ya trae las tres
cosas con hasheo de contrasenas, sesiones, admin y la integracion con DRF. Lo que
se hace aca es **mapear** el diagrama sobre lo que Django ya da:

======================  ==========================================
Diagrama                Implementacion
======================  ==========================================
``Usuario``             ``AbstractUser`` + legajo y fecha de baja
``Rol``                 subclase de ``Group`` (hereda sus permisos)
``Permiso``             ``django.contrib.auth.models.Permission``
``autenticar(clave)``   ``check_password``
``tienePermiso(cod)``   ``has_perm``
======================  ==========================================

``Permiso.codigo`` del diagrama (``programa.crear``) mapea 1:1 contra el
``app_label.codename`` de Django, asi que la equivalencia no es forzada.
"""

from django.contrib.auth.models import AbstractUser, Group
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from comun.enums import AccionAuditoria, TipoRol


class Rol(Group):
    """Rol funcional del sistema.

    Hereda de ``Group`` en lugar de reemplazarlo: asi los permisos que se le
    asignan los ve el motor de permisos de Django sin traduccion intermedia, y
    ``user.has_perm()`` funciona de fabrica. Los atributos que el diagrama pide y
    ``Group`` no tiene (codigo, descripcion, activo) se agregan aca.
    """

    codigo = models.CharField("codigo", max_length=20, choices=TipoRol.choices, unique=True)
    descripcion = models.TextField("descripcion", blank=True)
    activo = models.BooleanField("activo", default=True)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "rol"
        verbose_name_plural = "roles"
        ordering = ["codigo"]

    def __str__(self) -> str:
        """Devuelve el nombre legible del rol."""
        return self.name or self.codigo

    def otorgar(self, permiso) -> None:
        """Concede un permiso al rol.

        Args:
            permiso: Instancia de ``Permission`` a conceder.
        """
        self.permissions.add(permiso)

    def revocar(self, permiso) -> None:
        """Quita un permiso del rol.

        Args:
            permiso: Instancia de ``Permission`` a revocar.
        """
        self.permissions.remove(permiso)

    def incluye(self, permiso) -> bool:
        """Indica si el rol tiene concedido un permiso.

        Args:
            permiso: Instancia de ``Permission`` a consultar.

        Returns:
            ``True`` si el permiso esta concedido.
        """
        return self.permissions.filter(pk=permiso.pk).exists()


class Usuario(AbstractUser):
    """Usuario del sistema.

    ``AbstractUser`` ya aporta ``username``, ``first_name`` (nombre),
    ``last_name`` (apellido), ``email``, ``password`` hasheada, ``is_active``
    (activo), ``date_joined`` (fecha de alta) y ``last_login`` (ultimo acceso).
    Solo se agrega lo que el diagrama pide y Django no tiene.

    La regla **un rol por usuario** se modela con una clave foranea y no con la
    relacion muchos-a-muchos ``groups`` que trae Django. Para que el motor de
    permisos siga funcionando, ``save()`` mantiene ``groups`` sincronizado con
    ``rol``: es el precio de conservar las dos cosas a la vez.
    """

    legajo = models.CharField("legajo", max_length=20, blank=True, null=True, unique=True)
    rol = models.ForeignKey(
        Rol,
        verbose_name="rol",
        on_delete=models.PROTECT,
        related_name="usuarios",
        null=True,
        blank=True,
    )
    fecha_baja = models.DateTimeField("fecha de baja", null=True, blank=True)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        ordering = ["username"]

    def __str__(self) -> str:
        """Devuelve el nombre completo, o el usuario si no se cargo."""
        completo = self.get_full_name()
        return completo if completo else self.username

    def save(self, *args, **kwargs) -> None:
        """Guarda el usuario y sincroniza su grupo con el rol asignado.

        Sin esta sincronizacion, los permisos del rol existirian en la base pero
        ``has_perm()`` no los veria, y la autorizacion quedaria muda.
        """
        super().save(*args, **kwargs)
        self.groups.set([self.rol] if self.rol else [])

    def autenticar(self, clave: str) -> bool:
        """Verifica la contrasena del usuario.

        Un usuario dado de baja no autentica aunque la contrasena sea correcta.

        Args:
            clave: Contrasena en texto plano a verificar.

        Returns:
            ``True`` si la contrasena es correcta y el usuario esta activo.
        """
        return self.is_active and self.check_password(clave)

    def tiene_permiso(self, codigo: str) -> bool:
        """Indica si el usuario tiene un permiso.

        Args:
            codigo: Codigo del permiso en formato ``app_label.codename``, por
                ejemplo ``programas.add_programa``.

        Returns:
            ``True`` si el usuario tiene el permiso.
        """
        return self.has_perm(codigo)

    def dar_de_baja(self) -> None:
        """Desactiva al usuario y registra el momento de la baja.

        No se borra: los ensayos que ejecuto deben seguir siendo atribuibles, y
        un ``DELETE`` romperia la trazabilidad de RF006/RF007.
        """
        self.is_active = False
        self.fecha_baja = timezone.now()
        self.save(update_fields=["is_active", "fecha_baja"])


class RegistroAuditoria(models.Model):
    """Asiento inmutable de una accion auditable (RN010).

    Inmutable de verdad, no por convencion: ``save()`` rechaza las
    modificaciones y ``delete()`` rechaza el borrado. Una bitacora de auditoria
    que se puede editar no sirve como evidencia.

    La entidad afectada se referencia de forma generica (``entidad`` +
    ``codigo_entidad``) en lugar de con claves foraneas, porque el asiento debe
    sobrevivir al borrado de lo que describe.
    """

    fecha_hora = models.DateTimeField("fecha y hora", auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(
        "usuarios.Usuario",
        verbose_name="usuario",
        on_delete=models.SET_NULL,
        related_name="auditoria",
        null=True,
    )
    accion = models.CharField("accion", max_length=20, choices=AccionAuditoria.choices)
    entidad = models.CharField("entidad", max_length=100)
    codigo_entidad = models.CharField("codigo de la entidad", max_length=100, blank=True)
    valor_anterior = models.TextField("valor anterior", blank=True)
    valor_nuevo = models.TextField("valor nuevo", blank=True)
    critica = models.BooleanField("critica", default=False, db_index=True)
    direccion_ip = models.GenericIPAddressField("direccion IP", null=True, blank=True)

    class Meta:
        """Metadatos del modelo."""

        verbose_name = "registro de auditoria"
        verbose_name_plural = "registros de auditoria"
        ordering = ["-fecha_hora"]
        indexes = [models.Index(fields=["entidad", "codigo_entidad"])]

    def __str__(self) -> str:
        """Devuelve una linea legible del asiento."""
        quien = self.usuario.username if self.usuario else "(sistema)"
        return f"{self.fecha_hora:%Y-%m-%d %H:%M} {quien} {self.accion} {self.entidad}"

    def save(self, *args, **kwargs) -> None:
        """Crea el asiento. Rechaza cualquier intento de modificarlo.

        Raises:
            ValidationError: Si se intenta guardar un asiento ya existente.
        """
        if self.pk is not None:
            raise ValidationError(
                "Los registros de auditoria son inmutables (RN010): no se modifican."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        """Rechaza el borrado.

        Raises:
            ValidationError: Siempre.
        """
        raise ValidationError("Los registros de auditoria son inmutables (RN010): no se borran.")
