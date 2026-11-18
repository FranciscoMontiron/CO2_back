"""Pruebas de comportamiento sin GPIO: configuración, arbitraje y cancelación."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from controller.factory import REGISTRO_COMPONENTES, registrar_componente
from controller.drivers import SalidaGPIO
from controller.hal_base import OperacionRechazada
from controller.hardware import GestorHardware
from controller.hardware_config import (
    catalogo,
    guardar_archivo,
    pin_bcm,
    validar_configuracion,
)


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
        "parametros": {"pasos_por_unidad": 200, "minimo": 0, "maximo": 10, "velocidad_maxima": 1},
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
    config["parametros"]["pasos_por_unidad"] = valor
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
        assert (await ejecutar(g, "eje", "mover_a", posicion=1, velocidad=1))[
            "estado"
        ] == "RECHAZADO"
        await ejecutar(g, "eje", "referenciar", posicion=0)
        await ejecutar(g, "s", "armar")
        mover = orden(g, "eje", "mover_a", posicion=8, velocidad=1)
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
        await ejecutar(g, "eje", "referenciar", posicion=0)
        g.encolar(orden(g, "eje", "mover_a", posicion=2, velocidad=1))
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
        driver.plazo = lambda op, args: 0.05

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


def test_catalogo_sale_de_las_fichas_de_los_drivers():
    cat = catalogo()
    assert cat["SHUTTER_GPIO"]["pines"] == ["pin_señal"]
    assert cat["LM35"]["campos"] == {"temperatura_c": "numero", "tension_v": "numero"}
    assert set(cat) == set(REGISTRO_COMPONENTES)


def test_cada_ficha_esta_completa_y_usa_tipos_conocidos():
    catalogo()
    for tipo, clase in REGISTRO_COMPONENTES.items():
        assert clase.CAMPOS, tipo
        for args in clase.COMANDOS.values():
            assert set(args.values()) <= {"numero", "entero", "booleano"}, tipo
        assert set(clase.CAMPOS.values()) <= {"numero", "booleano"}, tipo


def test_driver_nuevo_se_agrega_en_un_solo_lugar(tmp_path):
    from controller.drivers import SalidaGPIO

    @registrar_componente("PRUEBA_RELE")
    class Rele(SalidaGPIO):
        COMANDOS = {"encender": {}, "apagar": {}, "pulsar": {"ms": "entero"}}

    try:
        cfg = validar_configuracion({"componentes": {"r": componente("PRUEBA_RELE", 5)}})
        assert cfg["componentes"]["r"]["tipo"] == "PRUEBA_RELE"
        assert "pulsar" in catalogo()["PRUEBA_RELE"]["operaciones"]
    finally:
        REGISTRO_COMPONENTES.pop("PRUEBA_RELE")


class DriverConSalida(SalidaGPIO):
    """Driver de prueba que devuelve un dato y define su propio plazo."""

    COMANDOS = {"medir": {}, "lento": {}}

    def ejecutar(self, operacion, argumentos):
        if operacion == "lento":
            time.sleep(0.5)
        return {"x": 1}

    def plazo(self, comando, argumentos):
        return 0.1 if comando == "lento" else 5.0


@pytest.fixture(autouse=True)
def _registrar_driver_con_salida():
    registrar_componente("PRUEBA_SALIDA")(DriverConSalida)
    yield
    REGISTRO_COMPONENTES.pop("PRUEBA_SALIDA", None)


@pytest.mark.asyncio
async def test_resultado_aplicado_trae_la_salida_del_driver(tmp_path):
    async with sistema(tmp_path, {"d": componente("PRUEBA_SALIDA", 5)}) as (g, _, _):
        res = await ejecutar(g, "d", "medir")
        assert res["estado"] == "APLICADO"
        assert res["salida"] == {"x": 1}
        assert res["detalle"]


@pytest.mark.asyncio
async def test_ejecutar_y_esperar_devuelve_la_salida_cuando_aplica(tmp_path):
    componentes = {"d": componente("PRUEBA_SALIDA", 5), "s": componente()}
    async with sistema(tmp_path, componentes) as (g, _, _):
        assert await g.ejecutar_y_esperar("d", "medir", {}) == {"x": 1}
        assert await g.ejecutar_y_esperar("s", "armar", {}) == {}
        assert not g._esperas


@pytest.mark.asyncio
async def test_si_emitir_falla_la_espera_igual_se_resuelve(tmp_path):
    """El resultado llega a quien espera antes de emitir el evento (m-7)."""
    async with sistema(tmp_path, {"d": componente("PRUEBA_SALIDA", 5)}) as (g, _, _):

        def emitir_roto(*_args):
            raise RuntimeError("bus caído")

        g.emitir = emitir_roto
        salida = await asyncio.wait_for(g.ejecutar_y_esperar("d", "medir", {}), 2)
        assert salida == {"x": 1}
        g.emitir = lambda *_args: None  # el cierre del sistema también emite


@pytest.mark.asyncio
async def test_ejecutar_y_esperar_lanza_con_el_motivo_del_driver(tmp_path):
    async with sistema(tmp_path, {"s": componente()}) as (g, _, _):
        with pytest.raises(OperacionRechazada) as exc:
            await g.ejecutar_y_esperar("s", "abrir", {})
        assert "Arme el shutter" in str(exc.value)


@pytest.mark.asyncio
async def test_ejecutar_y_esperar_lanza_si_hay_emergencia(tmp_path):
    async with sistema(tmp_path, {"s": componente()}) as (g, _, _):
        g.detener()
        with pytest.raises(OperacionRechazada):
            await g.ejecutar_y_esperar("s", "armar", {})


@pytest.mark.asyncio
async def test_ejecutar_y_esperar_se_resuelve_si_detener_vacia_la_cola(tmp_path):
    componentes = {"s": componente(), "d": componente("PRUEBA_SALIDA", 5)}
    async with sistema(tmp_path, componentes) as (g, _, _):
        g.encolar(orden(g, "d", "medir"))  # ocupa el escritor
        tarea = asyncio.create_task(g.ejecutar_y_esperar("s", "armar", {}))
        await asyncio.sleep(0)
        g.detener()
        with pytest.raises(OperacionRechazada):
            await asyncio.wait_for(tarea, 2)
        assert not g._esperas


@pytest.mark.asyncio
async def test_el_plazo_lo_define_el_driver(tmp_path):
    async with sistema(tmp_path, {"d": componente("PRUEBA_SALIDA", 5)}) as (g, _, _):
        inicio = time.monotonic()
        res = await ejecutar(g, "d", "lento")
        assert res["estado"] == "FALLIDO"
        assert time.monotonic() - inicio < 0.4


@pytest.mark.asyncio
async def test_la_parada_propia_guarda_la_causa_hasta_rearmar(tmp_path):
    async with sistema(tmp_path, {"d": componente("PRUEBA_SALIDA", 5)}) as (g, _, _):
        assert g.causa_parada is None
        res = await ejecutar(g, "d", "lento")
        assert res["estado"] == "FALLIDO"
        assert g.causa_parada == f"d.lento: {res['detalle']}"
        g.detener("otra causa")  # conserva la primera
        assert g.causa_parada.startswith("d.lento")
        await esperar(lambda: not g._io and g._parando.done())
        with pytest.raises(ValueError):
            g.rearmar()  # el recurso quedó en cuarentena por el timeout
        assert g.causa_parada.startswith("d.lento")
    # Una parada que sí se puede rearmar: rearmar borra la causa.
    (tmp_path / "b").mkdir()
    async with sistema(tmp_path / "b", {"d": componente("PRUEBA_SALIDA", 5)}) as (g, _, _):
        g.detener("lectura de d: TimeoutError")
        assert g.causa_parada == "lectura de d: TimeoutError"
        await esperar(lambda: not g._io and not g._ordenes and g._parando.done())
        g.rearmar()
        assert g.emergencia is False
        assert g.causa_parada is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("nombre", "comando", "argumentos"),
    [
        ("eje", "mover_a", {}),
        ("eje", "mover_a", {"posicion": "mucho", "velocidad": 1}),
        ("fantasma", "armar", {}),
    ],
)
async def test_ejecutar_y_esperar_rechaza_argumentos_o_componente_invalidos(
    tmp_path, nombre, comando, argumentos
):
    async with sistema(tmp_path, {"eje": motor()}) as (g, _, _):
        with pytest.raises(OperacionRechazada):
            await g.ejecutar_y_esperar(nombre, comando, argumentos)
        assert not g._esperas


@pytest.mark.asyncio
async def test_pulso_dura_lo_pedido_y_deja_el_shutter_cerrado(tmp_path):
    async with sistema(tmp_path, {"s": componente()}) as (g, b, _):
        await ejecutar(g, "s", "armar")
        salida = await g.ejecutar_y_esperar("s", "pulso", {"duracion_ms": 50})
        assert abs(salida["duracion_real_ms"] - 50) < 5
        assert salida["cancelado"] is False
        assert b.pines[17].value is False
        assert b.pines[17].historial[-2:] == [True, False]


@pytest.mark.asyncio
async def test_pulso_sin_armar_se_rechaza(tmp_path):
    async with sistema(tmp_path, {"s": componente()}) as (g, b, _):
        resultado = await ejecutar(g, "s", "pulso", duracion_ms=50)
        assert resultado["estado"] == "RECHAZADO"
        assert True not in b.pines[17].historial


@pytest.mark.asyncio
async def test_emergencia_corta_el_pulso_y_cierra(tmp_path):
    async with sistema(tmp_path, {"s": componente()}) as (g, b, _):
        await ejecutar(g, "s", "armar")
        cmd = orden(g, "s", "pulso", duracion_ms=2000)
        g.encolar(cmd)
        await esperar(lambda: b.pines[17].value)
        await asyncio.sleep(0.1)
        t0 = time.monotonic()
        g.detener()
        await esperar(lambda: not b.pines[17].value, 0.3)
        assert time.monotonic() - t0 < 0.3
        await esperar(lambda: g.resultados[cmd["id"]]["estado"] not in ("EN_COLA", "EJECUTANDO"))
        assert g.resultados[cmd["id"]]["estado"] == "CANCELADO"


@pytest.mark.asyncio
@pytest.mark.parametrize("duracion", [0, 10001])
async def test_pulso_fuera_de_rango_se_rechaza(tmp_path, duracion):
    async with sistema(tmp_path, {"s": componente()}) as (g, b, _):
        await ejecutar(g, "s", "armar")
        resultado = await ejecutar(g, "s", "pulso", duracion_ms=duracion)
        assert resultado["estado"] == "RECHAZADO"
        assert "entre 1 y 10000" in str(resultado)
        assert True not in b.pines[17].historial


# ── Escrituras concurrentes por recurso ──────────────────────────────────────


@pytest.mark.asyncio
async def test_recursos_distintos_se_escriben_a_la_vez(tmp_path):
    """Un pulso largo y un movimiento se superponen; ninguno vence esperando al otro."""
    async with sistema(tmp_path, {"eje": motor(), "s": componente()}) as (g, b, _):
        await g.ejecutar_y_esperar("eje", "referenciar", {"posicion": 0})
        await g.ejecutar_y_esperar("s", "armar", {})
        eje = g.drivers["eje"]
        superpuestos = []

        async def observar():
            while True:
                if b.pines[17].value and eje.en_movimiento:
                    superpuestos.append(time.monotonic())
                await asyncio.sleep(0.002)

        observador = asyncio.create_task(observar())
        try:
            pulso, movimiento = await asyncio.gather(
                g.ejecutar_y_esperar("s", "pulso", {"duracion_ms": 600}),
                g.ejecutar_y_esperar("eje", "mover", {"delta": 0.2, "velocidad": 1}),
            )
        finally:
            observador.cancel()
        assert superpuestos, "el pulso y el movimiento no se superpusieron"
        assert pulso["duracion_real_ms"] == pytest.approx(600, abs=10)
        assert eje.pasos == 40
        assert [r["estado"] for r in g.resultados.values()].count("VENCIDO") == 0


@pytest.mark.asyncio
async def test_un_mismo_recurso_se_escribe_en_orden_y_de_a_una(tmp_path):
    async with sistema(tmp_path, {"s": componente()}) as (g, _, _):
        driver = g.drivers["s"]
        original = driver.ejecutar
        tramos = []

        def registrar(operacion, argumentos):
            inicio = time.monotonic()
            salida = original(operacion, argumentos)
            tramos.append((operacion, inicio, time.monotonic()))
            return salida

        driver.ejecutar = registrar
        await asyncio.gather(
            g.ejecutar_y_esperar("s", "armar", {}),
            g.ejecutar_y_esperar("s", "pulso", {"duracion_ms": 100}),
            g.ejecutar_y_esperar("s", "abrir", {}),
            g.ejecutar_y_esperar("s", "cerrar", {}),
        )
        assert [t[0] for t in tramos] == ["armar", "pulso", "abrir", "cerrar"]
        for anterior, siguiente in zip(tramos, tramos[1:], strict=False):
            assert anterior[2] <= siguiente[1]


@pytest.mark.asyncio
async def test_detener_con_escrituras_concurrentes_cancela_todo_y_resuelve_esperas(tmp_path):
    async with sistema(tmp_path, {"eje": motor(), "s": componente()}) as (g, b, _):
        await g.ejecutar_y_esperar("eje", "referenciar", {"posicion": 0})
        await g.ejecutar_y_esperar("s", "armar", {})
        esperas = [
            asyncio.create_task(g.ejecutar_y_esperar("s", "pulso", {"duracion_ms": 5000})),
            asyncio.create_task(g.ejecutar_y_esperar("eje", "mover", {"delta": 8, "velocidad": 1})),
            # Detrás del pulso, en el mismo recurso: espera su turno.
            asyncio.create_task(g.ejecutar_y_esperar("s", "abrir", {})),
        ]
        await esperar(lambda: b.pines[17].value and g.drivers["eje"].en_movimiento)
        assert len(g.estado()["en_ejecucion"]) == 2
        t0 = time.monotonic()
        g.detener()
        resultados = await asyncio.wait_for(asyncio.gather(*esperas, return_exceptions=True), 2)
        assert time.monotonic() - t0 < 1.5
        assert all(isinstance(r, OperacionRechazada) for r in resultados), resultados
        await esperar(lambda: g.todo_seguro() and not g.actual and g._parando.done())
        assert not b.pines[17].value
        assert g.drivers["eje"].referenciado is False
        assert not g._esperas and not g._ordenes
        # La emergencia queda enclavada: no se aceptan órdenes nuevas hasta rearmar.
        assert g.encolar(orden(g, "s", "abrir"))["estado"] == "RECHAZADO"
