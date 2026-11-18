"""Contrato genérico de motor: lo cumple el DRV8825 y debe cumplirlo cualquier driver futuro."""

import threading
import time

import pytest

from controller import drivers  # noqa: F401 - registra los drivers
from controller.factory import MOTOR_CONTRATO, REGISTRO_COMPONENTES, HardwareFactory
from controller.hardware_config import validar_configuracion, validar_operacion
from tests.test_hardware import BackendFalso

TIPOS_MOTOR = [t for t, c in REGISTRO_COMPONENTES.items() if c.CONTRATO == "MOTOR"]


def crear(tipo, **parametros):
    """Crea e inicializa un driver del tipo dado con pines consecutivos y un backend falso."""
    clase = REGISTRO_COMPONENTES[tipo]
    pines = {nombre: 20 + i for i, nombre in enumerate(clase.PINES)}
    datos = {"tipo": tipo, "pines": pines, "parametros": parametros, "estado": "Vigente"}
    config = validar_configuracion({"componentes": {"eje": datos}})["componentes"]["eje"]
    driver = HardwareFactory.crear_componente("eje", config, BackendFalso())
    driver.inicializar()
    return driver


def orden(driver, comando, **argumentos):
    """Valida la orden como lo hace el gestor y la ejecuta."""
    validar_operacion(driver.config, comando, argumentos)
    return driver.ejecutar(comando, argumentos)


def test_hay_al_menos_un_motor_registrado():
    assert "STEPPER_DRV8825" in TIPOS_MOTOR


@pytest.mark.parametrize("tipo", TIPOS_MOTOR)
def test_declara_comandos_y_campos_del_contrato(tipo):
    clase = REGISTRO_COMPONENTES[tipo]
    for comando, argumentos in MOTOR_CONTRATO["comandos"].items():
        assert clase.COMANDOS[comando] == argumentos
    for campo, tipo_campo in MOTOR_CONTRATO["campos"].items():
        assert clase.CAMPOS[campo] == tipo_campo


@pytest.mark.parametrize("tipo", TIPOS_MOTOR)
def test_no_mueve_sin_referenciar(tipo):
    driver = crear(tipo)
    assert driver.leer()["referenciado"] is False
    with pytest.raises(ValueError):
        orden(driver, "mover_a", posicion=1, velocidad=1)
    with pytest.raises(ValueError):
        orden(driver, "mover", delta=1, velocidad=1)


@pytest.mark.parametrize("tipo", TIPOS_MOTOR)
def test_mover_a_y_mover_llegan_y_actualizan_posicion(tipo):
    driver = crear(tipo)
    orden(driver, "referenciar", posicion=0)
    assert driver.leer()["referenciado"] is True
    orden(driver, "mover_a", posicion=1, velocidad=1)
    assert driver.leer()["posicion"] == pytest.approx(1)
    orden(driver, "mover", delta=-0.5, velocidad=1)
    lectura = driver.leer()
    assert lectura["posicion"] == pytest.approx(0.5)
    assert lectura["en_movimiento"] is False
    assert lectura["referenciado"] is True


@pytest.mark.parametrize("tipo", TIPOS_MOTOR)
def test_rechaza_fuera_de_limites_y_sobre_velocidad(tipo):
    driver = crear(tipo, minimo=0, maximo=10, velocidad_maxima=1)
    orden(driver, "referenciar", posicion=0)
    for comando, argumentos in (
        ("mover_a", {"posicion": 11, "velocidad": 1}),
        ("mover_a", {"posicion": -1, "velocidad": 1}),
        ("mover_a", {"posicion": 1, "velocidad": 2}),
        ("mover_a", {"posicion": 1, "velocidad": 0}),
        ("mover", {"delta": -1, "velocidad": 1}),  # destino -1 < minimo (depende de la posición)
        ("mover", {"delta": 11, "velocidad": 1}),
        ("mover", {"delta": 1, "velocidad": 1.5}),
    ):
        with pytest.raises(ValueError):
            orden(driver, comando, **argumentos)
    assert driver.leer()["posicion"] == 0


@pytest.mark.parametrize("tipo", TIPOS_MOTOR)
def test_detener_durante_movimiento_frena_y_pide_referenciar(tipo):
    driver = crear(tipo)
    orden(driver, "referenciar", posicion=0)
    error = []

    def mover():
        try:
            orden(driver, "mover_a", posicion=100, velocidad=1)
        except BaseException as exc:  # noqa: BLE001 - se verifica abajo
            error.append(exc)

    hilo = threading.Thread(target=mover)
    hilo.start()
    limite = time.monotonic() + 3
    while not driver.leer()["en_movimiento"] and time.monotonic() < limite:
        time.sleep(0.001)
    assert driver.leer()["en_movimiento"] is True
    driver.cancelar.set()
    hilo.join(3)
    assert not hilo.is_alive()
    assert error and isinstance(error[0], InterruptedError)
    lectura = driver.leer()
    assert lectura["en_movimiento"] is False
    assert lectura["referenciado"] is False
    assert lectura["habilitado"] is False
    driver.cancelar.clear()
    with pytest.raises(ValueError):
        orden(driver, "mover_a", posicion=1, velocidad=1)


@pytest.mark.parametrize("tipo", TIPOS_MOTOR)
def test_detener_como_comando_deja_el_eje_sin_referencia(tipo):
    driver = crear(tipo)
    orden(driver, "referenciar", posicion=0)
    orden(driver, "detener")
    lectura = driver.leer()
    assert lectura["referenciado"] is False
    assert lectura["habilitado"] is False


@pytest.mark.parametrize("tipo", TIPOS_MOTOR)
def test_plazo_es_distancia_sobre_velocidad_mas_cinco(tipo):
    driver = crear(tipo, velocidad_maxima=2)
    orden(driver, "referenciar", posicion=10)
    assert driver.plazo("mover_a", {"posicion": 30, "velocidad": 2}) == pytest.approx(20 / 2 + 5)
    assert driver.plazo("mover_a", {"posicion": 4, "velocidad": 1}) == pytest.approx(6 / 1 + 5)
    assert driver.plazo("mover", {"delta": -3, "velocidad": 1.5}) == pytest.approx(3 / 1.5 + 5)
    assert driver.plazo("detener", {}) == 5.0


def test_el_drv8825_valida_sus_parametros():
    base = {
        "tipo": "STEPPER_DRV8825",
        "pines": {"pin_step": 22, "pin_dir": 23, "pin_enable": 24},
        "estado": "Vigente",
    }
    for parametros in (
        {"unidad": "cm"},
        {"pasos_por_unidad": 3000, "velocidad_maxima": 1},
        {"minimo": 10, "maximo": 5},
    ):
        with pytest.raises(ValueError):
            validar_configuracion({"componentes": {"e": {**base, "parametros": parametros}}})
    validar_configuracion({"componentes": {"e": {**base, "parametros": {"unidad": "°"}}}})


def _config_motor(parametros):
    return {
        "tipo": "STEPPER_DRV8825",
        "pines": {"pin_step": 22, "pin_dir": 23, "pin_enable": 24},
        "parametros": parametros,
        "estado": "Vigente",
    }


def test_config_con_nombres_antiguos_se_migra():
    antiguo = {"pasos_por_mm": 100, "carrera_max_mm": 50, "velocidad_mm_s": 2}
    salida = validar_configuracion({"componentes": {"e": _config_motor(antiguo)}})
    assert salida["componentes"]["e"]["parametros"] == {
        "unidad": "mm",
        "pasos_por_unidad": 100,
        "minimo": 0,
        "maximo": 50,
        "velocidad_maxima": 2,
    }


def test_config_mezclada_antigua_y_nueva_se_rechaza():
    mezcla = {"pasos_por_mm": 100, "maximo": 50}
    with pytest.raises(ValueError, match="No mezcle"):
        validar_configuracion({"componentes": {"e": _config_motor(mezcla)}})


def test_plazo_con_velocidad_cero_no_falla_y_tiene_tope():
    driver = crear("STEPPER_DRV8825", maximo=10000, velocidad_maxima=0.01, pasos_por_unidad=1)
    assert driver.plazo("mover", {"delta": 1, "velocidad": 0}) == 5.0
    assert driver.plazo("mover_a", {"posicion": 1, "velocidad": -1}) == 5.0
    assert driver.plazo("mover", {"delta": 9000, "velocidad": 0.01}) == 600.0


@pytest.mark.asyncio
async def test_velocidad_cero_es_rechazada_sin_excepcion_ajena(tmp_path):
    from controller.hal_base import OperacionRechazada
    from tests.test_hardware import motor, sistema

    async with sistema(tmp_path, {"eje": motor()}) as (g, _, _):
        with pytest.raises(OperacionRechazada):
            await g.ejecutar_y_esperar("eje", "mover_a", {"posicion": 1, "velocidad": 0})


class _SinEspera:
    """Sustituto de ``cancelar`` que nunca está activo ni espera: acelera los pasos."""

    def is_set(self):
        return False

    def wait(self, _timeout=None):
        return False


@pytest.mark.parametrize("tipo", TIPOS_MOTOR)
def test_mover_relativo_no_acumula_el_redondeo_a_pasos(tipo, monkeypatch):
    """100 × 0,552 mm a 200 pasos/mm: cada mover redondeado aparte daría 55,0 mm."""
    monkeypatch.setattr(drivers.time, "sleep", lambda _s: None)
    driver = crear(tipo, pasos_por_unidad=200, minimo=0, maximo=300, velocidad_maxima=10)
    driver.cancelar = _SinEspera()
    orden(driver, "referenciar", posicion=0)
    for _ in range(100):
        orden(driver, "mover", delta=0.552, velocidad=10)
    assert driver.leer()["posicion"] == pytest.approx(55.2, abs=1 / 200)
    # mover_a fija la posición comandada; el relativo sigue desde ahí.
    orden(driver, "mover_a", posicion=10, velocidad=10)
    for _ in range(10):
        orden(driver, "mover", delta=0.552, velocidad=10)
    assert driver.leer()["posicion"] == pytest.approx(15.52, abs=1 / 200)
