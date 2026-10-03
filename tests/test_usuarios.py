"""Pruebas de usuarios, roles y auditoria."""

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError

from comun.enums import AccionAuditoria, TipoRol
from usuarios.models import RegistroAuditoria, Rol, Usuario


@pytest.mark.django_db
def test_autenticar_valida_la_contrasena(usuario):
    """La contrasena se guarda hasheada: se verifica, no se compara."""
    assert usuario.autenticar("radio-1898") is True
    assert usuario.autenticar("incorrecta") is False


@pytest.mark.django_db
def test_un_usuario_dado_de_baja_no_autentica(usuario):
    """Aunque la contrasena sea correcta: la baja corta el acceso."""
    usuario.dar_de_baja()
    assert usuario.autenticar("radio-1898") is False


@pytest.mark.django_db
def test_dar_de_baja_desactiva_y_fecha_sin_borrar(usuario):
    """No se borra: los ensayos que ejecuto deben seguir siendo atribuibles."""
    usuario.dar_de_baja()
    usuario.refresh_from_db()

    assert usuario.is_active is False
    assert usuario.fecha_baja is not None
    assert Usuario.objects.filter(pk=usuario.pk).exists()


@pytest.mark.django_db
def test_el_rol_se_sincroniza_con_los_grupos_de_django(usuario, rol):
    """Sin esta sincronizacion los permisos del rol existirian pero `has_perm` no los veria."""
    assert list(usuario.groups.all()) == [rol.group_ptr]


@pytest.mark.django_db
def test_los_permisos_del_rol_llegan_al_usuario(usuario, rol):
    """Es la razon de que `Rol` herede de `Group` en vez de reemplazarlo."""
    permiso = Permission.objects.get(codename="add_programa")
    rol.otorgar(permiso)

    # El cache de permisos es por instancia; se relee desde la base.
    fresco = Usuario.objects.get(pk=usuario.pk)
    assert fresco.tiene_permiso("programas.add_programa") is True


@pytest.mark.django_db
def test_revocar_quita_el_permiso(rol):
    """`otorgar` e `incluye` del diagrama, sobre el motor de permisos de Django."""
    permiso = Permission.objects.get(codename="add_programa")
    rol.otorgar(permiso)
    assert rol.incluye(permiso) is True

    rol.revocar(permiso)
    assert rol.incluye(permiso) is False


@pytest.mark.django_db
def test_quitarle_el_rol_a_un_usuario_lo_deja_sin_grupos(usuario):
    """La baja de rol tiene que propagarse, o quedarian permisos huerfanos."""
    usuario.rol = None
    usuario.save()

    assert usuario.groups.count() == 0


@pytest.mark.django_db
def test_un_rol_por_usuario(db):
    """La regla del diagrama: `rol` es una FK, no una relacion muchos a muchos."""
    assert not Usuario._meta.get_field("rol").many_to_many


@pytest.mark.django_db
def test_el_codigo_de_rol_es_unico(rol):
    """Dos roles ADMINISTRADOR harian ambiguo que significa el codigo."""
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        Rol.objects.create(name="Otro operador", codigo=TipoRol.OPERADOR)


@pytest.mark.django_db
def test_el_registro_de_auditoria_no_se_puede_modificar(usuario):
    """RN010: una bitacora editable no sirve como evidencia."""
    asiento = RegistroAuditoria.objects.create(
        usuario=usuario, accion=AccionAuditoria.COMANDAR, entidad="Laser", critica=True
    )

    asiento.accion = AccionAuditoria.LOGIN
    with pytest.raises(ValidationError, match="inmutables"):
        asiento.save()


@pytest.mark.django_db
def test_el_registro_de_auditoria_no_se_puede_borrar(usuario):
    """Si se pudiera borrar, bastaria con eso para tapar una accion critica."""
    asiento = RegistroAuditoria.objects.create(
        usuario=usuario, accion=AccionAuditoria.ELIMINAR, entidad="Programa"
    )

    with pytest.raises(ValidationError, match="inmutables"):
        asiento.delete()


@pytest.mark.django_db
def test_el_asiento_sobrevive_a_la_baja_del_usuario(usuario):
    """La entidad se referencia de forma generica justamente para esto."""
    RegistroAuditoria.objects.create(
        usuario=usuario, accion=AccionAuditoria.CREAR, entidad="Red", codigo_entidad="L003-R012"
    )
    usuario.delete()

    # La auditoria automatica tambien asienta el alta y la baja del usuario;
    # aca interesa el asiento manual de la Red.
    asiento = RegistroAuditoria.objects.get(entidad="Red")
    assert asiento.usuario is None
    assert asiento.entidad == "Red"
    assert asiento.codigo_entidad == "L003-R012"
