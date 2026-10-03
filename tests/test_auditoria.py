"""Pruebas de la auditoria automatica (RN010, issue #12).

El criterio de aceptacion de #12 es que una accion critica quede registrada
**aunque el programador se olvide de invocar la auditoria**. Por eso ninguna de
estas pruebas llama a nada de auditoria: hacen la accion y verifican el asiento.
"""

import json

import pytest
from django.test import RequestFactory

from comun.enums import AccionAuditoria, OrigenEmergencia, VariableMonitoreada
from operacion.models import EventoEmergencia, Umbral
from usuarios.auditoria import AuditoriaMiddleware, _ip_de
from usuarios.models import RegistroAuditoria


def _asientos(entidad):
    """Devuelve los asientos de una entidad, del mas viejo al mas nuevo."""
    return list(RegistroAuditoria.objects.filter(entidad=entidad).order_by("fecha_hora", "pk"))


@pytest.mark.django_db
def test_crear_un_programa_se_audita_solo(programa):
    """Nadie invoco la auditoria: el asiento aparece igual."""
    asientos = _asientos("programas.Programa")

    assert len(asientos) == 1
    assert asientos[0].accion == AccionAuditoria.CREAR
    assert asientos[0].codigo_entidad == str(programa.pk)


@pytest.mark.django_db
def test_modificar_asienta_solo_lo_que_cambio(programa):
    """Un volcado completo por cada campo tocado volveria ilegible la bitacora."""
    programa.nombre = "Grabado LPG corregido"
    programa.save()

    modificacion = _asientos("programas.Programa")[-1]
    assert modificacion.accion == AccionAuditoria.MODIFICAR
    assert json.loads(modificacion.valor_anterior) == {"nombre": "Grabado LPG estandar"}
    assert json.loads(modificacion.valor_nuevo) == {"nombre": "Grabado LPG corregido"}


@pytest.mark.django_db
def test_un_save_sin_cambios_no_genera_asiento(programa):
    """Guardar sin modificar nada no es una accion."""
    antes = len(_asientos("programas.Programa"))
    programa.save()

    assert len(_asientos("programas.Programa")) == antes


@pytest.mark.django_db
def test_borrar_deja_el_estado_que_tenia(configuracion):
    """El asiento de borrado guarda lo que se perdio, o no hay forma de saberlo."""
    umbral = configuracion.umbrales.get()
    pk = umbral.pk
    umbral.delete()

    borrado = [a for a in _asientos("operacion.Umbral") if a.accion == AccionAuditoria.ELIMINAR]
    assert len(borrado) == 1
    assert borrado[0].codigo_entidad == str(pk)
    assert json.loads(borrado[0].valor_anterior)["valor_max"] == 28.0


@pytest.mark.django_db
def test_tocar_un_umbral_es_critico(configuracion):
    """Los umbrales son seguridad: cambiarlos se marca critico (RN010)."""
    umbral = configuracion.umbrales.get()
    umbral.valor_max = 40.0
    umbral.save()

    assert _asientos("operacion.Umbral")[-1].critica is True


@pytest.mark.django_db
def test_una_emergencia_se_audita_como_critica():
    """Una parada de emergencia es el caso critico por excelencia."""
    EventoEmergencia.objects.create(origen=OrigenEmergencia.BOTON_FISICO, tiempo_respuesta_ms=90)
    assert _asientos("operacion.EventoEmergencia")[0].critica is True


@pytest.mark.django_db
def test_lo_que_no_es_seguridad_no_se_marca_critico(programa):
    """Marcar todo como critico es lo mismo que no marcar nada."""
    assert _asientos("programas.Programa")[0].critica is False


@pytest.mark.django_db
def test_la_contrasena_nunca_llega_a_la_bitacora(usuario):
    """Ni hasheada: un hash en un registro que leen muchos es material de ataque."""
    for asiento in _asientos("usuarios.Usuario"):
        assert "password" not in asiento.valor_nuevo
        assert "password" not in asiento.valor_anterior


@pytest.mark.django_db
def test_los_checkpoints_no_inflan_la_bitacora(registro):
    """Son de alto volumen y los escribe la maquina: no se auditan."""
    from comun.enums import EstadoSistema, TipoCheckpoint

    for _ in range(5):
        registro.registrar_checkpoint(
            tipo=TipoCheckpoint.TIEMPO, estado_sistema=EstadoSistema.GRABANDO
        )
    assert not RegistroAuditoria.objects.filter(entidad="trazabilidad.Checkpoint").exists()


@pytest.mark.django_db
def test_el_inicio_de_sesion_se_audita(client, usuario):
    """Quien entro y cuando es lo primero que se pregunta ante un incidente."""
    client.login(username="mcurie", password="radio-1898")

    login = RegistroAuditoria.objects.filter(accion=AccionAuditoria.LOGIN).get()
    assert login.usuario == usuario


@pytest.mark.django_db
def test_el_asiento_se_atribuye_a_quien_hizo_la_peticion(usuario, configuracion):
    """El middleware es lo que conecta la accion con la persona."""
    peticion = RequestFactory().post("/", REMOTE_ADDR="10.0.0.7")
    peticion.user = usuario

    def vista(request):
        Umbral.objects.create(
            configuracion=configuracion,
            variable=VariableMonitoreada.CORRIENTE_AT,
            valor_min=0.0,
            valor_max=25.0,
        )

    AuditoriaMiddleware(vista)(peticion)

    asiento = _asientos("operacion.Umbral")[-1]
    assert asiento.usuario == usuario
    assert asiento.direccion_ip == "10.0.0.7"


@pytest.mark.django_db
def test_fuera_de_una_peticion_el_asiento_queda_sin_usuario(lote):
    """Lo que hace un comando o el controlador es del sistema, no de una persona."""
    asiento = _asientos("fabricacion.Lote")[0]
    assert asiento.usuario is None


def test_detras_de_nginx_se_toma_la_ip_del_cliente():
    """nginx termina TLS: REMOTE_ADDR es nginx, el cliente viene en X-Forwarded-For."""
    peticion = RequestFactory().get(
        "/", REMOTE_ADDR="172.18.0.5", HTTP_X_FORWARDED_FOR="200.45.1.9, 172.18.0.5"
    )
    assert _ip_de(peticion) == "200.45.1.9"


def test_sin_proxy_se_usa_la_ip_directa():
    """Sin cabecera de proxy, la IP es la de la conexion."""
    assert _ip_de(RequestFactory().get("/", REMOTE_ADDR="10.0.0.7")) == "10.0.0.7"
