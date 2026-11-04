"""Pruebas de comportamiento sin GPIO: configuración, arbitraje y cancelación."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from controller.hardware import GestorHardware
from controller.hardware_config import guardar_archivo, pin_bcm, validar_configuracion


class PinFalso:
    def __init__(self, pin, activo_alto=True):
        self.pin, self.activo_alto, self.value = pin, activo_alto, False
        self.historial = []
        self.cerrado = False

    def on(self):
        assert not self.cerrado
        self.value = True
        self.historial.append(True)

    def off(self):
        self.value = False
        self.historial.append(False)

    def close(self):
        self.cerrado = True


class BackendFalso:
    def __init__(self):
        self.pines = {}
        self.cerrado = False

    def salida(self, pin, activo_alto):
        salida = PinFalso(pin, activo_alto)
        self.pines[pin] = salida
        return salida

    def entrada(self, pin, pull_up):
        return self.salida(pin, True)

    def adc(self, canal, dispositivo, vref):
        adc = PinFalso(canal)
        adc.voltage = 0.235
        return adc

    def cerrar(self):
        self.cerrado = True


def componente(tipo="SHUTTER_GPIO", pin=17):
    return {"tipo": tipo, "pines": {"pin_señal": pin}, "estado": "Vigente"}


def motor():
    return {
        "tipo": "STEPPER_DRV8825",
        "estado": "Vigente",
        "pines": {"pin_step": 22, "pin_dir": 23, "pin_enable": 24},
        "parametros": {"pasos_por_mm": 200, "carrera_max_mm": 10, "velocidad_mm_s": 1},
    }


async def esperar(condicion, segundos=3):
    limite = time.monotonic() + segundos
    while not condicion():
        if time.monotonic() > limite:
            raise AssertionError("No se cumplió la condición dentro del plazo")
        await asyncio.sleep(0.005)


@asynccontextmanager
async def sistema(tmp_path, componentes, **opciones):
    ruta = tmp_path / "hardware.json"
    guardar_archivo({"componentes": componentes}, ruta)
    backend = BackendFalso()
    gestor = GestorHardware(ruta, backend_factory=lambda: backend, **opciones)
    parar = asyncio.Event()
    tarea = asyncio.create_task(gestor.correr(parar))
    try:
        await esperar(lambda: gestor.revision_aplicada is not None or gestor.error_config)
        assert gestor.revision_aplicada, gestor.error_config
        yield gestor, backend, ruta
    finally:
        parar.set()
        await asyncio.wait_for(tarea, 5)


def orden(gestor, nombre, operacion, **argumentos):
    return {
        "id": str(uuid4()),
        "componente": nombre,
        "operacion": operacion,
        "argumentos": argumentos,
        "revision": gestor.revision_aplicada,
        "vence_en": time.time() + 10,
    }


async def ejecutar(gestor, nombre, operacion, **argumentos):
    cmd = orden(gestor, nombre, operacion, **argumentos)
    gestor.encolar(cmd)
    await esperar(lambda: gestor.resultados[cmd["id"]]["estado"] not in ("EN_COLA", "EJECUTANDO"))
    return gestor.resultados[cmd["id"]]


def test_normaliza_numeraciones_y_rechaza_alimentacion():
    assert pin_bcm(17) == pin_bcm("GPIO17") == pin_bcm("PIN11")
    for invalido in ("PIN2", "PIN6", True, 17.5, "GPIO99"):
        with pytest.raises(ValueError):
            pin_bcm(invalido)


@pytest.mark.parametrize("activo_alto", [True, False])
def test_adaptador_gpiozero_real_aplica_polaridad_y_cierra_recursos(activo_alto):
    from gpiozero.pins.mock import MockFactory

    from controller.drivers import GpioBackend
    from controller.factory import HardwareFactory

    backend = GpioBackend.__new__(GpioBackend)
    backend.factory = MockFactory()
    config = validar_configuracion(
        {
            "componentes": {
                "s": {
                    **componente(),
                    "parametros": {"activo_alto": activo_alto},
                }
            }
        }
    )["componentes"]["s"]
    driver = HardwareFactory.crear_componente("s", config, backend)
    try:
        driver.inicializar()
        pin = backend.factory.pin(17)
        assert pin.state == (not activo_alto)
        driver.ejecutar("armar", {})
        driver.ejecutar("abrir", {})
        assert pin.state == activo_alto
        driver.ejecutar("cerrar", {})
        assert pin.state == (not activo_alto)
    finally:
        driver.cerrar()
        backend.cerrar()


def test_el_json_del_usuario_requiere_corregir_lm35():
    config = {
        "componentes": {
            "shutter_principal": componente(),
            "Sensor de temperatura": {
                "tipo": "LM35",
                "estado": "Vigente",
                "pines": {"Datos": "GPIO17", "5V": "PIN2"},
                "parametros": {},
            },
        }
    }
    with pytest.raises(ValueError, match="MCP3008"):
        validar_configuracion(config)


def test_colisiones_y_bajas():
    config = {"componentes": {"uno": componente(), "dos": componente(pin="PIN11")}}
    with pytest.raises(ValueError, match="ya está utilizado"):
        validar_configuracion(config)
    config["componentes"]["dos"]["estado"] = "Eliminado"
    assert validar_configuracion(config)["componentes"]["dos"]["estado"] == "Eliminado"


def test_nombre_de_componente_no_permite_compartir_pines_reservados_spi():
    config = {
        "componentes": {
            "SPI0": componente("SALIDA_GPIO", 9),
            "temperatura": {
                "tipo": "LM35",
                "estado": "Vigente",
                "pines": {},
                "parametros": {"adc": "MCP3008", "canal": 0},
            },
        }
    }
    with pytest.raises(ValueError, match="ya está utilizado"):
        validar_configuracion(config)


@pytest.mark.parametrize("valor", [float("nan"), float("inf"), True, 0, -1])
def test_motor_rechaza_parametros_invalidos(valor):
    config = motor()
    config["parametros"]["pasos_por_mm"] = valor
    with pytest.raises(ValueError):
        validar_configuracion({"componentes": {"eje": config}})


@pytest.mark.asyncio
async def test_varios_shutters_nombres_arbitrarios_y_realimentacion(tmp_path):
    async with sistema(tmp_path, {"izquierdo": componente(), "derecho": componente(pin=27)}) as (
        g,
        b,
        _,
    ):
        assert (await ejecutar(g, "izquierdo", "abrir"))["estado"] == "RECHAZADO"
        assert (await ejecutar(g, "izquierdo", "armar"))["estado"] == "APLICADO"
        assert (await ejecutar(g, "izquierdo", "abrir"))["estado"] == "APLICADO"
        assert b.pines[17].value and not b.pines[27].value
        assert g.estado()["componentes"]["izquierdo"]["valores"]["realimentacion"] is False
        assert (await ejecutar(g, "izquierdo", "cerrar"))["estado"] == "APLICADO"
        assert not b.pines[17].value


@pytest.mark.asyncio
async def test_orden_y_deduplicacion(tmp_path):
    async with sistema(tmp_path, {"s": componente()}) as (g, b, _):
        comandos = [orden(g, "s", op) for op in ("armar", "abrir", "cerrar", "desarmar")]
        for cmd in comandos:
            g.encolar(cmd)
            g.encolar(cmd)
        await esperar(lambda: all(g.resultados[c["id"]]["estado"] == "APLICADO" for c in comandos))
        assert b.pines[17].historial.count(True) == 1
        assert g.todo_seguro()


@pytest.mark.asyncio
async def test_cola_acotada_revision_y_vencimiento(tmp_path):
    async with sistema(tmp_path, {"s": componente()}, capacidad=1) as (g, _, _):
        cmd = orden(g, "s", "armar")
        assert g.encolar(cmd)["estado"] == "EN_COLA"
        assert g.encolar(orden(g, "s", "abrir"))["estado"] == "RECHAZADO"
        await esperar(lambda: g.cola.empty() and not g.actual)
        vencido = {**orden(g, "s", "abrir"), "vence_en": 0}
        assert g.encolar(vencido)["estado"] == "VENCIDO"
        vieja = {**orden(g, "s", "abrir"), "revision": "anterior"}
        assert g.encolar(vieja)["estado"] == "RECHAZADO"


@pytest.mark.asyncio
async def test_baja_se_aplica_solo_despues_de_detener(tmp_path):
    async with sistema(tmp_path, {"s": componente()}) as (g, b, ruta):
        await ejecutar(g, "s", "armar")
        await ejecutar(g, "s", "abrir")
        anterior = g.revision_aplicada
        guardar_archivo({"componentes": {"s": {**componente(), "estado": "Eliminado"}}}, ruta)
        await esperar(lambda: g.revision_deseada != anterior)
        assert g.revision_aplicada == anterior
        assert b.pines[17].value
        g.detener()
        await esperar(lambda: g.revision_aplicada != anterior)
        assert not b.pines[17].value and b.pines[17].cerrado
        assert not g.drivers


@pytest.mark.asyncio
async def test_motor_exige_referencia_y_emergencia_cancela_reapertura(tmp_path):
    async with sistema(tmp_path, {"eje": motor(), "s": componente()}) as (g, b, _):
        assert (await ejecutar(g, "eje", "mover", posicion_mm=1))["estado"] == "RECHAZADO"
        await ejecutar(g, "eje", "referenciar", posicion_mm=0)
        await ejecutar(g, "s", "armar")
        mover = orden(g, "eje", "mover", posicion_mm=8)
        g.encolar(mover)
        await esperar(lambda: b.pines[24].value)
        abrir = orden(g, "s", "abrir")
        g.encolar(abrir)
        g.detener()
        await esperar(lambda: g.todo_seguro() and not g.actual)
        assert g.resultados[abrir["id"]]["estado"] == "CANCELADO"
        assert g.resultados[mover["id"]]["estado"] == "CANCELADO"
        assert True not in b.pines[17].historial
        assert b.pines[24].activo_alto is False
        assert not b.pines[24].value
        assert g.encolar(orden(g, "s", "abrir"))["estado"] == "RECHAZADO"


@pytest.mark.asyncio
async def test_lecturas_independientes_continuan_durante_movimiento(tmp_path):
    async with sistema(tmp_path, {"eje": motor(), "sensor": componente("ENTRADA_GPIO", 4)}) as (
        g,
        b,
        _,
    ):
        await ejecutar(g, "eje", "referenciar", posicion_mm=0)
        g.encolar(orden(g, "eje", "mover", posicion_mm=2))
        await esperar(lambda: b.pines[24].value)
        b.pines[4].value = True
        await esperar(lambda: g.muestras["sensor"]["valores"]["activo"])
        assert g.actual is not None
        g.detener()


@pytest.mark.asyncio
async def test_timeout_no_libera_recurso_fisico_ni_permite_otra_escritura(tmp_path):
    async with sistema(tmp_path, {"s": componente()}, timeout_io=0.05) as (g, b, _):
        liberar = threading.Event()
        entro = threading.Event()
        driver = g.drivers["s"]
        original = driver.ejecutar

        def bloqueado(op, args):
            entro.set()
            liberar.wait(1)
            original(op, args)

        driver.ejecutar = bloqueado
        cmd = orden(g, "s", "armar")
        g.encolar(cmd)
        await esperar(entro.is_set)
        await esperar(lambda: g.resultados[cmd["id"]]["estado"] == "FALLIDO")
        assert g.emergencia
        assert g.encolar(orden(g, "s", "abrir"))["estado"] == "RECHAZADO"
        with pytest.raises(ValueError):
            g.rearmar()
        liberar.set()
        await esperar(lambda: not g._io)
        assert not b.pines[17].value


@pytest.mark.asyncio
async def test_adc_comparte_bus_y_convierte_tension(tmp_path):
    lm35 = {
        "tipo": "LM35",
        "estado": "Vigente",
        "pines": {},
        "parametros": {"adc": "MCP3008", "canal": 0},
    }
    otro = {**lm35, "parametros": {"adc": "MCP3008", "canal": 1}}
    async with sistema(tmp_path, {"agua": lm35, "ambiente": otro}) as (g, _, _):
        assert g.muestras["agua"]["valores"]["temperatura_c"] == 23.5
        assert g.drivers["agua"].recurso == g.drivers["ambiente"].recurso


def test_guardado_invalido_no_destruye_json(tmp_path):
    ruta = tmp_path / "config.json"
    guardar_archivo({"componentes": {"s": componente()}}, ruta)
    anterior = ruta.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        guardar_archivo({"componentes": {"s": componente(pin="PIN2")}}, ruta)
    assert ruta.read_text(encoding="utf-8") == anterior
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_fallo_inicializacion_libera_recursos_y_permite_corregir(tmp_path):
    ruta = tmp_path / "config.json"
    guardar_archivo({"componentes": {"s": componente()}}, ruta)
    backend = BackendFalso()
    original = backend.salida
    backend.salida = lambda *args: (_ for _ in ()).throw(RuntimeError("Pin no disponible"))
    gestor = GestorHardware(ruta, backend_factory=lambda: backend)
    await gestor.aplicar()
    await gestor._parando
    assert gestor.revision_aplicada is None
    assert not gestor.drivers
    assert "Pin no disponible" in gestor.error_config
    backend.salida = original
    gestor.intentada = None
    try:
        await gestor.aplicar()
        assert gestor.revision_aplicada is not None
        gestor.rearmar()
        assert not gestor.emergencia
    finally:
        for driver in gestor.drivers.values():
            driver.cerrar()


@pytest.mark.asyncio
async def test_maquina_gpio_usa_gestor_y_no_publica_sensores_inventados(tmp_path):
    from controller import protocolo as p
    from controller.maquina import Maquina
    from controller.rpi import HalGpio

    async with sistema(tmp_path, {"otra salida": componente("SALIDA_GPIO")}) as (g, b, _):
        maquina = Maquina(HalGpio(g), emitir=lambda *args: None)
        cmd = {**orden(g, "otra salida", "encender"), "accion": p.COMPONENTE}
        await maquina.manejar(cmd)
        await esperar(lambda: g.resultados[cmd["id"]]["estado"] == "APLICADO")
        assert b.pines[17].value
        maquina.tick(0.01)
        telemetria = maquina.telemetria(1)
        assert telemetria["modo_hal"] == "gpio"
        assert telemetria["variables"]["TEMPERATURA_AGUA"] is None
        assert not telemetria["hardware"]["automatico_disponible"]
        await maquina.manejar({"accion": p.EMERGENCIA})
        await esperar(lambda: g.todo_seguro() and not g.actual and g._parando.done())
        assert not b.pines[17].value
        await maquina.manejar({"accion": p.REARMAR})
        assert not g.emergencia


@pytest.mark.asyncio
async def test_orden_vencida_en_la_cola_no_se_ejecuta(tmp_path):
    async with sistema(tmp_path, {"s": componente()}) as (g, b, _):
        cmd = orden(g, "s", "armar")
        cmd["vence_en"] = time.time() + 0.005
        g.encolar(cmd)
        # Expira después de aceptar pero antes de entregar al trabajador.
        time.sleep(0.01)
        await esperar(lambda: g.resultados[cmd["id"]]["estado"] == "VENCIDO")
        assert not g.drivers["s"].armado
        assert True not in b.pines[17].historial


@pytest.mark.asyncio
async def test_lecturas_mismo_bus_no_se_superponen(tmp_path):
    lm35 = {
        "tipo": "LM35",
        "estado": "Vigente",
        "pines": {},
        "parametros": {"adc": "MCP3008", "canal": 0},
    }
    otro = {**lm35, "parametros": {"adc": "MCP3008", "canal": 1}}
    async with sistema(tmp_path, {"uno": lm35, "dos": otro}) as (g, _, _):
        en_curso = 0
        maximo = 0
        leidos = 0

        def leer():
            nonlocal en_curso, maximo, leidos
            en_curso += 1
            leidos += 1
            maximo = max(maximo, en_curso)
            time.sleep(0.02)
            en_curso -= 1
            return {"temperatura_c": 23.5}

        for driver in g.drivers.values():
            driver.leer = leer
        await asyncio.gather(*(g._leer(n, d) for n, d in g.drivers.items()))
        assert maximo == 1
        assert leidos == 2
