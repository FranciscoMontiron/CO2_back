"""Pruebas de la carga inicial y del admin.

La carga inicial es lo primero que corre un integrante nuevo del equipo: si
falla, la primera impresion del proyecto es un traceback. El admin es la unica
interfaz que existe hasta que se conecte el front.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse

from comun.enums import TipoRol
from fabricacion.models import Red
from operacion.models import Configuracion, Umbral
from trazabilidad.models import RegistroDeFabricacion
from usuarios.models import Rol, Usuario


def _cargar(*args):
    """Corre el comando y devuelve lo que imprimio."""
    salida = StringIO()
    call_command("cargar_datos_iniciales", *args, stdout=salida)
    return salida.getvalue()


@pytest.mark.django_db
def test_la_carga_crea_los_tres_roles():
    """Sin roles no se puede asignar ninguno a un usuario."""
    _cargar()
    assert set(Rol.objects.values_list("codigo", flat=True)) == set(TipoRol.values)


@pytest.mark.django_db
def test_la_carga_deja_una_configuracion_vigente_con_todos_los_umbrales():
    """El controlador necesita exactamente una configuracion activa para arrancar."""
    _cargar()
    vigente = Configuracion.objects.get(activa=True)
    assert Umbral.objects.filter(configuracion=vigente).count() == 7


@pytest.mark.django_db
def test_el_administrador_recibe_todos_los_permisos():
    """Si el administrador no puede administrar, nadie puede."""
    from django.contrib.auth.models import Permission

    _cargar()
    admin = Rol.objects.get(codigo=TipoRol.ADMINISTRADOR)
    assert admin.permissions.count() == Permission.objects.count()


@pytest.mark.django_db
def test_la_carga_es_idempotente():
    """Correrla dos veces no puede duplicar nada: se corre en cada despliegue."""
    _cargar("--con-ejemplo")
    _cargar("--con-ejemplo")

    assert Rol.objects.count() == 3
    assert Configuracion.objects.count() == 1
    assert Red.objects.count() == 1
    assert RegistroDeFabricacion.objects.count() == 1


@pytest.mark.django_db
def test_el_ejemplo_respeta_el_invariante_de_longitud():
    """Es la prueba de punta a punta de la cadena derivada.

    `Red.longitud` llega al perfil de periodo pasando por la corrida y el
    programa: 40 marcas a 500 um tienen que dar 20 mm.
    """
    _cargar("--con-ejemplo")
    red = Red.objects.get()

    assert red.cantidad_marcas == 40
    assert red.longitud == pytest.approx(20.0)
    assert red.resonancia_principal() is not None


@pytest.mark.django_db
def test_sin_ejemplo_no_carga_datos_de_fabricacion():
    """Produccion no tiene que arrancar con un ensayo inventado adentro."""
    _cargar()
    assert not Red.objects.exists()


@pytest.fixture
def cliente_admin(client, db):
    """Devuelve un cliente HTTP logueado como superusuario."""
    Usuario.objects.create_superuser(username="admin", password="admin", email="a@b.c")
    client.login(username="admin", password="admin")
    return client


@pytest.mark.django_db
@pytest.mark.parametrize(
    "modelo",
    [
        "usuarios_usuario",
        "usuarios_rol",
        "usuarios_registroauditoria",
        "fabricacion_lote",
        "fabricacion_red",
        "fabricacion_procedimiento",
        "fabricacion_espectro",
        "fabricacion_picodeatenuacion",
        "programas_programa",
        "programas_periodo",
        "trazabilidad_registrodefabricacion",
        "trazabilidad_checkpoint",
        "operacion_configuracion",
        "operacion_umbral",
        "operacion_alerta",
        "operacion_eventoemergencia",
    ],
)
def test_cada_listado_del_admin_carga_con_datos(cliente_admin, modelo):
    """Un listado que explota con datos reales es un listado que nadie usa."""
    _cargar("--con-ejemplo")
    respuesta = cliente_admin.get(reverse(f"admin:{modelo}_changelist"))
    assert respuesta.status_code == 200


@pytest.mark.django_db
def test_el_admin_no_permite_editar_la_auditoria(cliente_admin):
    """Coherente con RN010: el admin no puede ser la puerta trasera."""
    from comun.enums import AccionAuditoria
    from usuarios.models import RegistroAuditoria

    asiento = RegistroAuditoria.objects.create(accion=AccionAuditoria.LOGIN, entidad="Sesion")
    url = reverse("admin:usuarios_registroauditoria_change", args=[asiento.pk])
    respuesta = cliente_admin.post(url, {"entidad": "Adulterado"})

    asiento_releido = RegistroAuditoria.objects.get(pk=asiento.pk)
    assert asiento_releido.entidad == "Sesion"
    assert respuesta.status_code in (302, 403)
