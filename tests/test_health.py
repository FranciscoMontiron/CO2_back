"""Pruebas del endpoint de salud.

No tocan MySQL ni Redis: las dependencias se sustituyen, porque lo que se
verifica aca es el contrato del endpoint (que codigo HTTP devuelve y con que
forma de cuerpo), no que los servicios externos esten levantados.
"""

import json

import pytest
from django.test import RequestFactory

from config import health


@pytest.fixture
def peticion():
    """Devuelve una peticion GET a /api/healthz/."""
    return RequestFactory().get("/api/healthz/")


def test_healthz_devuelve_200_con_todo_sano(peticion, monkeypatch):
    """Con MySQL y Redis respondiendo, el endpoint devuelve 200."""
    monkeypatch.setattr(health, "_revisar_base_de_datos", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_revisar_redis", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_revisar_controlador", lambda: (True, "ok"))

    respuesta = health.healthz(peticion)
    cuerpo = json.loads(respuesta.content)

    assert respuesta.status_code == 200
    assert cuerpo["estado"] == "ok"
    assert set(cuerpo["dependencias"]) == {"mysql", "redis", "controlador"}


def test_healthz_devuelve_503_si_falla_mysql(peticion, monkeypatch):
    """Sin MySQL la API no puede operar, asi que el healthcheck falla."""
    monkeypatch.setattr(health, "_revisar_base_de_datos", lambda: (False, "caido"))
    monkeypatch.setattr(health, "_revisar_redis", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_revisar_controlador", lambda: (True, "ok"))

    respuesta = health.healthz(peticion)

    assert respuesta.status_code == 503
    assert json.loads(respuesta.content)["estado"] == "degradado"


def test_healthz_devuelve_503_si_falla_redis(peticion, monkeypatch):
    """Sin Redis no hay bus ni channel layer: tampoco se puede operar."""
    monkeypatch.setattr(health, "_revisar_base_de_datos", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_revisar_redis", lambda: (False, "caido"))
    monkeypatch.setattr(health, "_revisar_controlador", lambda: (True, "ok"))

    respuesta = health.healthz(peticion)

    assert respuesta.status_code == 503


def test_controlador_caido_no_tumba_el_healthcheck(peticion, monkeypatch):
    """El controlador caido se informa, pero la API sigue siendo util.

    Verifica la decision de ADR-0002: la API mantiene disponibles las consultas
    historicas y la administracion aunque el hardware no responda. Lo que no
    puede pasar es que el estado se oculte.
    """
    monkeypatch.setattr(health, "_revisar_base_de_datos", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_revisar_redis", lambda: (True, "ok"))
    monkeypatch.setattr(
        health, "_revisar_controlador", lambda: (False, "heartbeat vencido o ausente")
    )

    respuesta = health.healthz(peticion)
    cuerpo = json.loads(respuesta.content)

    assert respuesta.status_code == 200
    assert cuerpo["dependencias"]["controlador"]["ok"] is False


def test_revisar_redis_reporta_el_error_sin_propagarlo(monkeypatch):
    """Una caida de Redis se reporta como detalle, no como excepcion.

    Si el healthcheck propagara la excepcion, Docker veria un 500 sin
    informacion y el diagnostico se volveria adivinanza.
    """

    def _explotar(*args, **kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(health.redis.Redis, "from_url", _explotar)

    ok, detalle = health._revisar_redis()

    assert ok is False
    assert "connection refused" in detalle


def test_revisar_controlador_reporta_el_error_sin_propagarlo(monkeypatch):
    """Una caida de Redis al leer el heartbeat se reporta, no se propaga."""

    def _explotar(*args, **kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(health.redis.Redis, "from_url", _explotar)

    ok, detalle = health._revisar_controlador()

    assert ok is False
    assert "connection refused" in detalle
