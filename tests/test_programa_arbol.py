"""Validación y resumen del árbol de pasos de un programa."""

import copy

import pytest

from controller.hardware_config import validar_configuracion
from controller.programa import INVENTARIO_SIMULADO, resumen, validar_programa


@pytest.fixture
def config():
    return validar_configuracion(INVENTARIO_SIMULADO)


def accion(componente, comando, **argumentos):
    return {
        "tipo": "ACCION",
        "componente": componente,
        "comando": comando,
        "argumentos": argumentos,
    }


def pulso(ms=400):
    return accion("shutter", "pulso", duracion_ms=ms)


def bucle(n, *pasos):
    return {"tipo": "BUCLE", "repeticiones": n, "pasos": list(pasos)}


def paralelo(*pasos):
    return {"tipo": "PARALELO", "pasos": list(pasos)}


def helicoidal():
    return [
        accion("shutter", "armar"),
        bucle(
            200,
            paralelo(
                pulso(400),
                accion("motor_x", "mover", delta=0.55, velocidad=0.1),
                accion("motor_rotacion", "mover", delta=3.6, velocidad=0.9),
            ),
        ),
    ]


def test_programa_valido_se_normaliza(config):
    pasos = helicoidal()
    original = copy.deepcopy(pasos)
    normalizado = validar_programa(pasos, config)
    assert normalizado == original
    assert pasos == original  # no muta la entrada


CASOS = [
    ("componente inexistente", [accion("fantasma", "armar")], "Paso 1"),
    ("comando inexistente", [pulso(), accion("shutter", "volar")], "Paso 2"),
    ("falta argumento", [accion("shutter", "pulso")], "Paso 1"),
    ("argumento de más", [accion("laser", "encender", extra=1)], "Paso 1"),
    ("argumento de tipo erróneo", [accion("shutter", "pulso", duracion_ms="400")], "Paso 1"),
    ("pulso fuera de rango", [accion("shutter", "pulso", duracion_ms=0)], "Paso 1"),
    (
        "mover fuera de límites",
        [accion("motor_x", "mover_a", posicion=9999, velocidad=1)],
        "Paso 1",
    ),
    ("tipo de nodo desconocido", [pulso(), {"tipo": "SALTAR"}], "Paso 2"),
    ("espera inválida", [pulso(), {"tipo": "ESPERA", "duracion_ms": -5}], "Paso 2"),
    ("bucle con 0 repeticiones", [bucle(0, pulso())], "Paso 1"),
    ("bucle con demasiadas repeticiones", [bucle(10001, pulso())], "Paso 1"),
    ("bucle sin hijos", [pulso(), bucle(2)], "Paso 2"),
    ("bucle dentro de paralelo", [pulso(), paralelo(pulso(), bucle(2, pulso()))], "Paso 2.2"),
    (
        "dos acciones sobre el mismo componente en paralelo",
        [pulso(), pulso(), paralelo(pulso(), pulso(200))],
        "Paso 3",
    ),
    (
        "más de tres niveles",
        [bucle(2, bucle(2, bucle(2, bucle(2, pulso()))))],
        "Paso 1.1.1.1",
    ),
    ("sin ninguna acción", [{"tipo": "ESPERA", "duracion_ms": 10}], "Programa"),
    ("lista vacía", [], "Programa"),
    ("no es lista", {"tipo": "ESPERA"}, "Programa"),
]


@pytest.mark.parametrize(("pasos", "ubicacion"), [c[1:] for c in CASOS], ids=[c[0] for c in CASOS])
def test_programa_invalido_se_rechaza(config, pasos, ubicacion):
    with pytest.raises(ValueError) as exc:
        validar_programa(pasos, config)
    assert ubicacion in str(exc.value)


def test_componente_eliminado_se_rechaza(config):
    config["componentes"]["laser"]["estado"] = "Eliminado"
    with pytest.raises(ValueError, match="Paso 1"):
        validar_programa([accion("laser", "encender")], config)


def test_anidado_en_tres_niveles_es_valido(config):
    pasos = [bucle(2, pulso())]
    for _ in range(2):
        pasos = [bucle(2, *pasos)]
    assert validar_programa(pasos, config)


def test_resumen_expande_bucles_y_paralelos(config):
    r = resumen(helicoidal(), config)
    assert r["pulsos"] == 200
    assert r["acciones"] == 1 + 200 * 3
    assert r["recorrido"] == {
        "motor_x": pytest.approx(110.0),
        "motor_rotacion": pytest.approx(720.0),
    }
    assert r["duracion_minima_ms"] >= 80000


def test_resumen_paralelo_toma_el_maximo_y_mover_a_usa_la_ultima_posicion(config):
    pasos = [
        accion("motor_x", "referenciar", posicion=10),
        accion("motor_x", "mover_a", posicion=20, velocidad=1),
        bucle(
            3,
            accion("motor_x", "mover_a", posicion=30, velocidad=1),
            accion("motor_x", "mover_a", posicion=20, velocidad=1),
        ),
        paralelo(pulso(400), {"tipo": "ESPERA", "duracion_ms": 1000}),
        {"tipo": "ESPERA", "duracion_ms": 50},
    ]
    r = resumen(pasos, config)
    assert r["recorrido"]["motor_x"] == pytest.approx(10 + 3 * 20)
    # El motor suma |d|/v: 10 mm + 3 × 20 mm a 1 mm/s, más el paralelo y la espera.
    assert r["duracion_minima_ms"] == 70_000 + 1000 + 50
    assert r["pulsos"] == 1


def test_resumen_bucle_con_mover_relativo_avanza_la_posicion_n_veces(config):
    pasos = [
        accion("motor_x", "referenciar", posicion=0),
        bucle(10, accion("motor_x", "mover", delta=5, velocidad=1)),
        accion("motor_x", "mover_a", posicion=0, velocidad=1),
    ]
    assert resumen(pasos, config)["recorrido"]["motor_x"] == pytest.approx(100)


def test_resumen_bucles_anidados_con_mover_relativo(config):
    pasos = [
        accion("motor_x", "referenciar", posicion=0),
        bucle(3, bucle(4, accion("motor_x", "mover", delta=2, velocidad=1))),
        accion("motor_x", "mover_a", posicion=0, velocidad=1),
    ]
    assert resumen(pasos, config)["recorrido"]["motor_x"] == pytest.approx(24 + 24)


def test_resumen_duracion_suma_el_motor_con_la_extrapolacion_del_bucle(config):
    pasos = [
        accion("motor_x", "referenciar", posicion=0),
        bucle(10, accion("motor_x", "mover", delta=5, velocidad=2)),  # 10 × 2,5 s
        accion("motor_x", "mover_a", posicion=0, velocidad=5),  # vuelve 50 mm: 10 s
        accion("motor_rotacion", "mover_a", posicion=90, velocidad=10),  # sin referencia: 0
    ]
    assert resumen(pasos, config)["duracion_minima_ms"] == 25_000 + 10_000


def test_resumen_paralelo_con_motor_toma_el_maximo(config):
    pasos = [
        accion("motor_x", "referenciar", posicion=0),
        paralelo(pulso(400), accion("motor_x", "mover", delta=0.55, velocidad=1)),
    ]
    assert resumen(pasos, config)["duracion_minima_ms"] == 550
