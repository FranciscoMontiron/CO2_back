"""Roles: qué componente alimenta cada tarjeta del dashboard."""

import pytest

from controller.hardware_config import revision, validar_configuracion
from controller.roles import ROLES, campo_de, resolver




def _comp(tipo, pin, **extra):
    return {"tipo": tipo, "pines": {"pin_señal": pin}, "estado": "Vigente", **extra}


def _config_lm35(**extra):
    comp = {
        "tipo": "LM35",
        "pines": {},
        "parametros": {"adc": "MCP3008", "canal": 0},
        "estado": "Vigente",
        **extra,
    }
    return {"componentes": {"sonda": comp}}


def test_configuracion_sin_roles_sigue_valida():
    cfg = validar_configuracion(_config_lm35())
    assert "rol" not in cfg["componentes"]["sonda"]


@pytest.mark.parametrize(
    "caso",
    [
        "desconocido",
        "repetido",
        "shutter_en_salida",
        "ganancia_sin_rol",
        "ganancia_en_laser",
        "inhibir_no_booleano",
    ],
)
def test_rol_invalido_se_rechaza(caso):
    if caso == "desconocido":
        cfg = _config_lm35(rol="NO_EXISTE")
    elif caso == "repetido":
        cfg = _config_lm35(rol="TEMPERATURA_AGUA")
        otro = dict(cfg["componentes"]["sonda"])
        otro["parametros"] = {"adc": "MCP3008", "canal": 1}
        cfg["componentes"]["otra"] = otro
    elif caso == "shutter_en_salida":
        cfg = {"componentes": {"s": _comp("SALIDA_GPIO", 17, rol="SHUTTER")}}
    elif caso == "ganancia_sin_rol":
        cfg = _config_lm35(ganancia=2)
    elif caso == "ganancia_en_laser":
        cfg = {"componentes": {"s": _comp("SALIDA_GPIO", 17, rol="LASER", ganancia=2)}}
    else:
        cfg = _config_lm35(rol="TEMPERATURA_AGUA", inhibir_en_prueba="si")
    with pytest.raises(ValueError):
        validar_configuracion(cfg)


def test_rol_valido_se_normaliza():
    cfg = validar_configuracion(_config_lm35(rol="TEMPERATURA_AGUA", ganancia=2, offset=1))
    comp = cfg["componentes"]["sonda"]
    assert (comp["rol"], comp["ganancia"], comp["offset"]) == ("TEMPERATURA_AGUA", 2, 1)


def test_campo_de():
    assert campo_de("CAUDAL_REFRIGERANTE", {"tension_v": "numero"}) == "tension_v"
    assert campo_de("TEMPERATURA_AGUA", {"tension_v": "numero"}) is None
    assert set(ROLES) >= {"LASER", "SHUTTER", "POSICION_MOTOR"}


def _muestra(valores, t=100.0, calidad="OK"):
    return {"valores": valores, "calidad": calidad, "monotonic": t}


def test_resolver_publica_el_valor_con_escala():
    cfg = validar_configuracion(_config_lm35(rol="TEMPERATURA_AGUA", ganancia=2, offset=1))
    res = resolver(cfg, {"sonda": _muestra({"temperatura_c": 23.5, "tension_v": 0.2})}, 101.0)
    assert res["variables"]["TEMPERATURA_AGUA"] == 48.0


def test_resolver_da_none_sin_rol_vencido_o_con_error():
    cfg = validar_configuracion(_config_lm35(rol="TEMPERATURA_AGUA"))
    m = {"temperatura_c": 20.0}
    assert resolver(cfg, {"sonda": _muestra(m)}, 101.0)["variables"]["TEMPERATURA_AGUA"] == 20.0
    assert resolver(cfg, {"sonda": _muestra(m)}, 104.0)["variables"]["TEMPERATURA_AGUA"] is None
    assert resolver(cfg, {"sonda": _muestra(m, calidad="ERROR")}, 101.0)["variables"][
        "TEMPERATURA_AGUA"
    ] is None
    assert resolver(cfg, {}, 101.0)["variables"]["TEMPERATURA_AGUA"] is None
    sin_rol = validar_configuracion(_config_lm35())
    assert resolver(sin_rol, {"sonda": _muestra(m)}, 101.0)["variables"]["TEMPERATURA_AGUA"] is None


def test_resolver_sin_roles_todo_none():
    res = resolver({"componentes": {}}, {}, 1.0)
    assert all(v is None for g in res.values() for v in g.values())
    assert res["senales"]["SHUTTER_ABIERTO"] is None
    assert res["extra"]["temperatura_laser"] is None


def test_resolver_shutter_y_laser():
    cfg = validar_configuracion(
        {
            "componentes": {
                "obt": {
                    "tipo": "SHUTTER_GPIO",
                    "pines": _pines("SHUTTER_GPIO", 17),
                    "estado": "Vigente",
                    "rol": "SHUTTER",
                },
                "las": _comp("SALIDA_GPIO", 5, rol="LASER"),
            }
        }
    )
    muestras = {
        "obt": _muestra({"armado": True, "salida_apertura": False}),
        "las": _muestra({"salida_activa": True}),
    }
    res = resolver(cfg, muestras, 101.0)
    assert res["actuadores"]["shutter_armado"] is True
    assert res["actuadores"]["shutter_abierto"] is False
    assert res["senales"]["SHUTTER_ABIERTO"] is False
    assert res["actuadores"]["laser"] is True
    vencido = resolver(cfg, muestras, 200.0)
    assert vencido["actuadores"]["shutter_abierto"] is None
    assert vencido["senales"]["SHUTTER_ABIERTO"] is None


def _pines(tipo, base):
    from controller.hardware_config import catalogo

    return {p: base + i for i, p in enumerate(catalogo()[tipo]["pines"])}


def test_cambiar_rol_cambia_la_revision():
    a = validar_configuracion(_config_lm35(rol="TEMPERATURA_AGUA"))
    b = validar_configuracion(_config_lm35(rol="TEMPERATURA_LASER"))
    assert revision(a) != revision(b)
