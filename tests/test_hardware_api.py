"""Contrato HTTP: permisos, configuración concurrente y órdenes físicas."""

import json
from contextlib import nullcontext

import pytest
from rest_framework.test import APIClient

from controller import protocolo as p
from controller.hardware_config import revision, validar_configuracion
from operacion import control
from tests.test_hardware import componente
from usuarios.models import RegistroAuditoria


@pytest.fixture
def cliente_hardware(usuario, monkeypatch, tmp_path):
    class Bus:
        def __init__(self):
            self.comandos = []
            self.estado = None

        def get(self, clave):
            return json.dumps(self.estado) if self.estado else None

        def lock(self, *args, **kwargs):
            return nullcontext()

        def xadd(self, stream, campos, **kwargs):
            self.comandos.append(json.loads(campos["datos"]))

    bus = Bus()
    monkeypatch.setenv("HARDWARE_CONFIG_PATH", str(tmp_path / "hardware.json"))
    monkeypatch.setattr(control, "bus", lambda *args, **kwargs: bus)
    monkeypatch.setattr(control, "controlador_vivo", lambda: True)
    cliente = APIClient()
    cliente.force_authenticate(usuario)
    return cliente, bus


@pytest.mark.django_db
def test_operador_lee_catalogo_pero_no_modifica_configuracion(cliente_hardware):
    cliente, _ = cliente_hardware
    respuesta = cliente.get("/api/hardware/config/")
    assert respuesta.data == {"componentes": {}}
    assert respuesta.headers["ETag"]
    assert "SHUTTER_GPIO" in cliente.get("/api/hardware/catalogo/").data
    respuesta = cliente.post(
        "/api/hardware/config/",
        {"componentes": {}},
        format="json",
        HTTP_IF_MATCH=respuesta.headers["ETag"],
    )
    assert respuesta.status_code == 403
    assert cliente.post("/api/hardware/aplicar/").status_code == 403


@pytest.mark.django_db
def test_guardar_controla_version_y_no_inventa_aplicacion(cliente_hardware, usuario):
    cliente, _ = cliente_hardware
    usuario.is_superuser = True
    usuario.save()
    etag = cliente.get("/api/hardware/config/").headers["ETag"]
    config = {"componentes": {"s": componente()}}
    assert cliente.post("/api/hardware/config/", config, format="json").status_code == 428
    respuesta = cliente.post("/api/hardware/config/", config, format="json", HTTP_IF_MATCH=etag)
    assert respuesta.status_code == 202
    assert respuesta.data["revision"]
    assert cliente.get("/api/hardware/config/").data == validar_configuracion(config)
    assert (
        cliente.post("/api/hardware/config/", config, format="json", HTTP_IF_MATCH=etag).status_code
        == 409
    )
    assert cliente.get("/api/hardware/estado/").status_code == 503


@pytest.mark.django_db
def test_json_invalido_no_sobrescribe_configuracion(cliente_hardware, usuario):
    cliente, _ = cliente_hardware
    usuario.is_superuser = True
    usuario.save()
    etag = cliente.get("/api/hardware/config/").headers["ETag"]
    config = {"componentes": {"s": componente(pin="PIN2")}}
    respuesta = cliente.post("/api/hardware/config/", config, format="json", HTTP_IF_MATCH=etag)
    assert respuesta.status_code == 400
    assert cliente.get("/api/hardware/config/").data == {"componentes": {}}


@pytest.mark.django_db
def test_comando_por_id_de_componente_se_audita_y_solo_se_acepta(cliente_hardware):
    cliente, bus = cliente_hardware
    config = validar_configuracion({"componentes": {"cualquier nombre": componente()}})
    bus.estado = {
        "componentes": config["componentes"],
        "revision_aplicada": revision(config),
        "emergencia": False,
        "aplicando": False,
    }
    datos = {
        "componente": "cualquier nombre",
        "operacion": "armar",
        "argumentos": {},
        "revision": revision(config),
    }
    respuesta = cliente.post("/api/hardware/comandos/", datos, format="json")
    assert respuesta.status_code == 202
    assert respuesta.data["estado"] == "RECIBIDO"
    cmd = bus.comandos[0]
    assert cmd["id"] == respuesta.data["id"] and cmd["accion"] == p.COMPONENTE
    assert cmd["componente"] == "cualquier nombre"
    assert cmd["vence_en"] > cmd["t_envio"] / 1000
    assert RegistroAuditoria.objects.filter(codigo_entidad=p.COMPONENTE, critica=True).exists()
    bus.estado["emergencia"] = True
    assert cliente.post("/api/hardware/comandos/", datos, format="json").status_code == 409
    assert len(bus.comandos) == 1


@pytest.mark.django_db
def test_orden_obsoleta_y_entrada_incorrecta_no_se_encolan(cliente_hardware):
    cliente, bus = cliente_hardware
    bus.estado = {
        "componentes": {},
        "revision_aplicada": "nueva",
        "emergencia": False,
        "aplicando": False,
    }
    assert (
        cliente.post("/api/hardware/comandos/", {"revision": "vieja"}, format="json").status_code
        == 409
    )
    assert cliente.post("/api/hardware/comandos/", [], format="json").status_code == 400
    assert not bus.comandos


@pytest.mark.django_db
def test_resultado_hardware_se_persiste_una_sola_vez(usuario):
    from operacion.management.commands.escuchar_controlador import al_hardware

    datos = {
        "id": "comando-unico",
        "usuario": usuario.username,
        "componente": "s",
        "operacion": "abrir",
        "estado": "APLICADO",
    }
    al_hardware(datos)
    al_hardware(datos)
    asientos = RegistroAuditoria.objects.filter(entidad="resultado_hardware")
    assert asientos.count() == 1
    assert asientos.get().usuario == usuario
    assert json.loads(asientos.get().valor_nuevo)["estado"] == "APLICADO"


@pytest.mark.django_db
def test_aplicar_sin_controlador_gpio_no_se_envia(cliente_hardware, usuario):
    """Con el HAL simulado no hay estado GPIO: aplicar se rechaza y no llega al controlador."""
    cliente, bus = cliente_hardware
    usuario.is_superuser = True
    usuario.save()
    respuesta = cliente.post("/api/hardware/aplicar/")
    assert respuesta.status_code == 409
    assert "modo GPIO" in respuesta.data["detail"]
    assert bus.comandos == []
    bus.estado = {
        "emergencia": False, "aplicando": False, "revision_aplicada": None, "componentes": {}
    }
    assert cliente.post("/api/hardware/aplicar/").status_code == 202
    assert bus.comandos[-1]["accion"] == p.APLICAR_HARDWARE
