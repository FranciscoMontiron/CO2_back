"""Simulador de Pi 5: gpiozero y drivers reales sobre un doble de lgpio."""

import asyncio
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

from controller.hardware import GestorHardware
from controller.hardware_config import guardar_archivo
from pi_sim.servidor import Servidor
from pi_sim.tablero import LgpioError, Tablero

sys.path.insert(0, str(Path(__file__).parents[1] / "pi_sim" / "shim"))

SALIDA = {"tipo": "SALIDA_GPIO", "pines": {"pin_señal": 17}, "estado": "Vigente"}
SHUTTER = {"tipo": "SHUTTER_GPIO", "pines": {"pin_señal": 27}, "estado": "Vigente"}
ENTRADA = {"tipo": "ENTRADA_GPIO", "pines": {"pin_señal": 22}, "estado": "Vigente"}
LM35 = {"tipo": "LM35", "pines": {}, "parametros": {"adc": "MCP3008"}, "estado": "Vigente"}
MOTOR = {
    "tipo": "STEPPER_DRV8825",
    "pines": {"pin_step": 5, "pin_dir": 6, "pin_enable": 13},
    "parametros": {"pasos_por_unidad": 100, "velocidad_maxima": 10},
    "estado": "Vigente",
}


def config(**componentes):
    """Normaliza un inventario como lo haría la API al guardarlo."""
    from controller.hardware_config import validar_configuracion

    return validar_configuracion({"componentes": componentes})


def fisico(tablero, nombre):
    """Devuelve el estado físico modelado de un componente."""
    return next(c for c in tablero.estado()["componentes"] if c["nombre"] == nombre)["fisico"]


def esperar(condicion, plazo=5):
    """Espera una condición sin dormir un tiempo fijo."""
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.01)
    return False


# ── Tablero ───────────────────────────────────────────────────────────────


def test_otro_proceso_no_puede_reservar_una_linea_ocupada():
    t = Tablero()
    a, b = t.abrir_chip(4), t.abrir_chip(4)
    t.reclamar_salida(a, 17)
    with pytest.raises(LgpioError, match="GPIO busy"):
        t.reclamar_entrada(b, 17)
    t.reclamar_entrada(a, 17)  # el mismo dueño puede reconfigurarla


def test_chip_inexistente_y_linea_sin_reservar_fallan_como_lgpio():
    t = Tablero()
    with pytest.raises(LgpioError, match="can not open gpiochip"):
        t.abrir_chip(9)
    h = t.abrir_chip(0)
    with pytest.raises(LgpioError, match="not allocated"):
        t.leer(h, 17)
    t.reclamar_entrada(h, 17)
    with pytest.raises(LgpioError, match="output"):
        t.escribir(h, 17, 1)


def test_cerrar_el_chip_libera_la_linea_y_la_salida_conserva_su_nivel():
    t = Tablero()
    t.sincronizar(config(rele=SALIDA))
    t.conectar("rele", True)
    a = t.abrir_chip(4)
    t.reclamar_salida(a, 17)
    t.escribir(a, 17, 1)
    t.cerrar_chip(a)
    pin = t.estado()["pines"][17]
    assert pin["dueno"] is None and pin["sin_dueno"] and pin["nivel"] == 1
    assert fisico(t, "rele")["carga_activa"] is True
    b = t.abrir_chip(4)
    t.reclamar_salida(b, 17)  # otro proceso ya puede tomarla


def test_salida_desconectada_cambia_el_pin_pero_no_la_carga():
    t = Tablero()
    t.sincronizar(config(rele=SALIDA))
    h = t.abrir_chip(4)
    t.reclamar_salida(h, 17)
    t.escribir(h, 17, 1)
    assert t.estado()["pines"][17]["nivel"] == 1
    assert fisico(t, "rele")["carga_activa"] is False
    t.conectar("rele", True)
    assert fisico(t, "rele")["carga_activa"] is True


def test_polaridad_invertida_activa_la_carga_con_nivel_bajo():
    t = Tablero()
    t.sincronizar(config(rele={**SALIDA, "parametros": {"activo_alto": False}}))
    t.conectar("rele", True)
    assert fisico(t, "rele")["carga_activa"] is False  # línea flotante no acciona
    h = t.abrir_chip(4)
    t.reclamar_salida(h, 17, 1)
    assert fisico(t, "rele")["carga_activa"] is False
    t.escribir(h, 17, 0)
    assert fisico(t, "rele")["carga_activa"] is True


def test_shutter_tarda_en_abrir_y_vuelve_a_cerrar():
    t = Tablero(tiempo_shutter=0.05)
    t.sincronizar(config(sh=SHUTTER))
    t.conectar("sh", True)
    h = t.abrir_chip(4)
    t.reclamar_salida(h, 27)
    assert fisico(t, "sh")["posicion"] == "CERRADO"
    t.escribir(h, 27, 1)
    assert fisico(t, "sh")["posicion"] == "ABRIENDO"
    assert esperar(lambda: fisico(t, "sh")["posicion"] == "ABIERTO")
    t.escribir(h, 27, 0)
    assert fisico(t, "sh")["posicion"] == "CERRANDO"
    assert esperar(lambda: fisico(t, "sh")["posicion"] == "CERRADO")


def test_shutter_desconectado_no_se_mueve():
    t = Tablero(tiempo_shutter=0)
    t.sincronizar(config(sh=SHUTTER))
    h = t.abrir_chip(4)
    t.reclamar_salida(h, 27)
    t.escribir(h, 27, 1)
    assert fisico(t, "sh")["posicion"] == "CERRADO"


def test_contacto_lleva_la_entrada_al_nivel_contrario_del_pull():
    t = Tablero()
    t.sincronizar(config(sensor=ENTRADA))
    h = t.abrir_chip(4)
    t.reclamar_entrada(h, 22, t.SET_PULL_UP)
    assert t.leer(h, 22) == 1
    t.accionar_contacto("sensor", True)
    assert t.leer(h, 22) == 1  # con el cable suelto solo manda el pull-up
    t.conectar("sensor", True)
    assert t.leer(h, 22) == 0
    t.accionar_contacto("sensor", False)
    assert t.leer(h, 22) == 1


def test_motor_solo_cuenta_pasos_conectado_y_habilitado():
    t = Tablero()
    t.sincronizar(config(eje=MOTOR))
    h = t.abrir_chip(4)
    for pin, nivel in ((5, 0), (6, 1), (13, 1)):
        t.reclamar_salida(h, pin, nivel)

    def pulso():
        t.escribir(h, 5, 1)
        t.escribir(h, 5, 0)

    pulso()  # deshabilitado y desconectado
    t.conectar("eje", True)
    pulso()  # conectado, sin habilitar
    assert fisico(t, "eje")["pasos"] == 0
    t.escribir(h, 13, 0)
    pulso()
    t.escribir(h, 6, 0)
    pulso()
    pulso()
    assert fisico(t, "eje")["pasos"] == -1
    assert fisico(t, "eje")["posicion"] == -0.01


def test_falla_ocupado_y_liberacion():
    t = Tablero()
    h = t.abrir_chip(4)
    t.fijar_falla("ocupado", True, 17)
    with pytest.raises(LgpioError, match="GPIO busy"):
        t.reclamar_salida(h, 17)
    t.fijar_falla("ocupado", False, 17)
    t.reclamar_salida(h, 17)


def test_falla_colgado_bloquea_solo_esa_linea_hasta_quitarla():
    t = Tablero()
    h = t.abrir_chip(4)
    t.reclamar_salida(h, 17)
    t.reclamar_salida(h, 27)
    t.fijar_falla("colgado", True, 17)
    resultado = []
    hilo = threading.Thread(target=lambda: resultado.append(t.leer(h, 17)), daemon=True)
    hilo.start()
    hilo.join(0.1)
    assert hilo.is_alive()
    t.escribir(h, 27, 1)  # otra línea sigue respondiendo
    t.fijar_falla("colgado", False, 17)
    hilo.join(2)
    assert resultado == [0]


def test_falla_desconocida_y_componente_inexistente_se_rechazan():
    t = Tablero()
    with pytest.raises(ValueError):
        t.fijar_falla("rayo", True, 17)
    with pytest.raises(ValueError):
        t.fijar_falla("ocupado", True, 99)
    with pytest.raises(ValueError):
        t.conectar("nada", True)
    t.sincronizar(config(rele=SALIDA))
    with pytest.raises(ValueError):
        t.fijar_temperatura("rele", 20)
    with pytest.raises(ValueError):
        t.accionar_contacto("rele", True)


def test_baja_quita_el_componente_y_el_alta_arranca_desconectado():
    t = Tablero()
    t.sincronizar(config(rele=SALIDA))
    t.conectar("rele", True)
    t.sincronizar(config(rele=SALIDA))
    assert t.estado()["componentes"][0]["conectado"] is True  # sin cambios: se conserva
    t.sincronizar(config(rele={**SALIDA, "pines": {"pin_señal": 23}}))
    assert t.estado()["componentes"][0]["conectado"] is False  # se recableó
    t.sincronizar(config(rele={**SALIDA, "estado": "Eliminado"}))
    assert t.estado()["componentes"] == []


# ── gpiozero real contra el simulador ─────────────────────────────────────


@pytest.fixture
def sim(tmp_path, monkeypatch):
    """Levanta el servidor en puertos libres y apunta el doble hacia él."""
    ruta = tmp_path / "hardware_config.json"
    tablero = Tablero(tiempo_shutter=0)
    servidor = Servidor(tablero, "127.0.0.1", 0, 0, ruta)
    servidor.iniciar()
    monkeypatch.setenv("PI_SIM_HOST", "127.0.0.1")
    monkeypatch.setenv("PI_SIM_PUERTO", str(servidor.puerto_gpio))
    yield servidor
    for pin in range(28):
        tablero.fijar_falla("colgado", False, pin)
    tablero.fijar_falla("chip_colgado", False)
    servidor.detener()


@pytest.fixture(autouse=True)
def reservas_limpias():
    """gpiozero guarda pines y reservas a nivel de proceso: cada test es un proceso nuevo."""
    from gpiozero.pins.local import LocalPiFactory

    yield
    LocalPiFactory.pins.clear()
    LocalPiFactory._reservations.clear()


@pytest.fixture
def backend(sim):
    """El adaptador real del controlador, sin cambios."""
    from gpiozero import Device

    from controller.drivers import GpioBackend

    instancia = GpioBackend()
    yield instancia
    instancia.cerrar()
    # El bus SPI por software abre además la fábrica global de gpiozero.
    if Device.pin_factory is not None:
        Device.pin_factory.close()
        Device.pin_factory = None


def crear(nombre, definicion, backend, sim):
    """Inicializa un driver real tal como lo hace el gestor."""
    from controller.factory import HardwareFactory

    datos = config(**{nombre: definicion})
    sim.tablero.sincronizar(datos)
    driver = HardwareFactory.crear_componente(nombre, datos["componentes"][nombre], backend)
    driver.inicializar()
    return driver


def test_factory_real_abre_el_chip_de_la_pi_5(backend):
    assert backend.factory.chip == 4
    assert backend.factory.board_info.model == "5B"


def test_driver_salida_real_acciona_la_carga(backend, sim):
    driver = crear("rele", SALIDA, backend, sim)
    sim.tablero.conectar("rele", True)
    assert fisico(sim.tablero, "rele")["carga_activa"] is False
    driver.ejecutar("encender", {})
    assert fisico(sim.tablero, "rele")["carga_activa"] is True
    assert driver.leer()["salida_activa"] is True
    driver.ejecutar("apagar", {})
    assert fisico(sim.tablero, "rele")["carga_activa"] is False
    driver.cerrar()


def test_driver_salida_real_activo_bajo_arranca_apagado(backend, sim):
    driver = crear("rele", {**SALIDA, "parametros": {"activo_alto": False}}, backend, sim)
    sim.tablero.conectar("rele", True)
    assert sim.tablero.estado()["pines"][17]["nivel"] == 1
    assert fisico(sim.tablero, "rele")["carga_activa"] is False
    driver.ejecutar("encender", {})
    assert sim.tablero.estado()["pines"][17]["nivel"] == 0
    assert fisico(sim.tablero, "rele")["carga_activa"] is True
    driver.cerrar()


def test_driver_shutter_real_arma_abre_y_cierra(backend, sim):
    driver = crear("sh", SHUTTER, backend, sim)
    sim.tablero.conectar("sh", True)
    driver.ejecutar("armar", {})
    assert driver.leer()["armado"] is True
    assert sim.tablero.estado()["pines"][27]["nivel"] == 0  # armar no toca el pin
    driver.ejecutar("abrir", {})
    assert esperar(lambda: fisico(sim.tablero, "sh")["posicion"] == "ABIERTO")
    driver.ejecutar("cerrar", {})
    assert esperar(lambda: fisico(sim.tablero, "sh")["posicion"] == "CERRADO")
    driver.cerrar()


def test_driver_entrada_real_lee_el_contacto_y_recibe_el_evento(backend, sim):
    driver = crear("sensor", ENTRADA, backend, sim)
    assert driver.leer()["activo"] is False
    cambios = []
    driver.pin.when_activated = lambda: cambios.append(True)
    sim.tablero.conectar("sensor", True)
    sim.tablero.accionar_contacto("sensor", True)
    assert driver.leer()["activo"] is True
    assert esperar(lambda: cambios == [True])
    driver.cerrar()


def test_driver_motor_real_avanza_lo_pedido(backend, sim):
    driver = crear("eje", MOTOR, backend, sim)
    sim.tablero.conectar("eje", True)
    driver.ejecutar("referenciar", {"posicion": 0})
    driver.ejecutar("mover_a", {"posicion": 0.5, "velocidad": 10})
    assert driver.leer()["posicion"] == 0.5
    assert fisico(sim.tablero, "eje")["pasos"] == 50
    driver.ejecutar("mover_a", {"posicion": 0.2, "velocidad": 10})
    assert fisico(sim.tablero, "eje")["posicion"] == 0.2
    driver.cerrar()


def test_driver_motor_desconectado_estima_pero_no_se_mueve(backend, sim):
    driver = crear("eje", MOTOR, backend, sim)
    driver.ejecutar("referenciar", {"posicion": 0})
    driver.ejecutar("mover_a", {"posicion": 0.1, "velocidad": 10})
    assert driver.leer()["posicion"] == 0.1
    assert fisico(sim.tablero, "eje")["pasos"] == 0
    driver.cerrar()


@pytest.mark.filterwarnings("ignore::gpiozero.exc.SPISoftwareFallback")
def test_driver_lm35_real_lee_la_temperatura_por_spi_software(backend, sim):
    driver = crear("agua", LM35, backend, sim)
    # Con el canal en 0, gpiozero entrega 0,16 °C por su propia escala.
    assert driver.leer()["temperatura_c"] == pytest.approx(0, abs=0.33)  # cable suelto
    sim.tablero.conectar("agua", True)
    sim.tablero.fijar_temperatura("agua", 25)
    assert driver.leer()["temperatura_c"] == pytest.approx(25, abs=0.33)
    sim.tablero.fijar_temperatura("agua", 80)
    assert driver.leer()["temperatura_c"] == pytest.approx(80, abs=0.33)
    driver.cerrar()


@pytest.mark.filterwarnings("ignore::gpiozero.exc.SPISoftwareFallback")
def test_dos_lm35_comparten_el_bus_sin_mezclar_canales(backend, sim):
    from controller.factory import HardwareFactory

    datos = config(
        a={**LM35, "parametros": {"adc": "MCP3008", "canal": 0}},
        b={**LM35, "parametros": {"adc": "MCP3008", "canal": 3}},
    )
    sim.tablero.sincronizar(datos)
    drivers = {
        n: HardwareFactory.crear_componente(n, datos["componentes"][n], backend) for n in "ab"
    }
    for nombre, temperatura in (("a", 20), ("b", 60)):
        drivers[nombre].inicializar()
        sim.tablero.conectar(nombre, True)
        sim.tablero.fijar_temperatura(nombre, temperatura)
    assert drivers["a"].leer()["temperatura_c"] == pytest.approx(20, abs=0.33)
    assert drivers["b"].leer()["temperatura_c"] == pytest.approx(60, abs=0.33)
    for driver in drivers.values():
        driver.cerrar()


def test_pin_ocupado_llega_como_error_de_lgpio(sim):
    import lgpio

    h = lgpio.gpiochip_open(4)
    sim.tablero.fijar_falla("ocupado", True, 17)
    with pytest.raises(lgpio.error, match="GPIO busy"):
        lgpio.gpio_claim_output(h, 17)
    lgpio.gpiochip_close(h)


def test_morir_sin_cerrar_libera_las_lineas(sim):
    import lgpio

    h = lgpio.gpiochip_open(4)
    lgpio.gpio_claim_output(h, 17)
    lgpio.gpio_write(h, 17, 1)
    lgpio._concesiones[h].shutdown(2)  # el proceso desaparece sin avisar
    assert esperar(lambda: sim.tablero.estado()["pines"][17]["sin_dueno"])
    assert sim.tablero.estado()["pines"][17]["nivel"] == 1


def test_sin_simulador_el_chip_no_abre(monkeypatch):
    import lgpio

    monkeypatch.setenv("PI_SIM_HOST", "127.0.0.1")
    monkeypatch.setenv("PI_SIM_PUERTO", "1")
    with pytest.raises(lgpio.error, match="can not open gpiochip"):
        lgpio.gpiochip_open(4)


def test_el_doble_se_niega_a_correr_con_un_chip_real(sim, monkeypatch):
    import lgpio

    monkeypatch.setattr(lgpio.os.path, "exists", lambda ruta: ruta == "/dev/gpiochip0")
    with pytest.raises(lgpio.error, match="chip real"):
        lgpio.gpiochip_open(4)


def test_llamada_colgada_no_frena_a_otro_hilo(sim):
    import lgpio

    h = lgpio.gpiochip_open(4)
    lgpio.gpio_claim_output(h, 17)
    lgpio.gpio_claim_output(h, 27)
    sim.tablero.fijar_falla("colgado", True, 17)
    hilo = threading.Thread(target=lgpio.gpio_read, args=(h, 17), daemon=True)
    hilo.start()
    hilo.join(0.1)
    assert hilo.is_alive()
    lgpio.gpio_write(h, 27, 1)
    assert sim.tablero.estado()["pines"][27]["nivel"] == 1
    sim.tablero.fijar_falla("colgado", False, 17)
    hilo.join(2)
    assert not hilo.is_alive()
    lgpio.gpiochip_close(h)


# ── Gestor real: lo que ve el SCADA ───────────────────────────────────────


def orden(gestor, componente, operacion, argumentos=None):
    """Arma una orden con el formato que publica la API."""
    return {
        "id": str(uuid4()),
        "componente": componente,
        "operacion": operacion,
        "argumentos": argumentos or {},
        "revision": gestor.revision_aplicada,
        "vence_en": time.time() + 10,
    }


async def hasta(condicion, plazo=5):
    """Espera una condición sin frenar al gestor."""
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        if condicion():
            return True
        await asyncio.sleep(0.02)
    return False


def correr_gestor(sim, inventario, escenario):
    """Corre el gestor real y un escenario, y detiene todo al terminar."""
    from controller.drivers import GpioBackend

    guardar_archivo(inventario, sim.ruta_config)
    sim.sincronizar_archivo()

    async def principal():
        gestor = GestorHardware(ruta=sim.ruta_config, backend_factory=GpioBackend)
        parar = asyncio.Event()
        tarea = asyncio.create_task(gestor.correr(parar))
        try:
            await escenario(gestor)
        finally:
            for pin in range(28):
                sim.tablero.fijar_falla("colgado", False, pin)
            parar.set()
            await asyncio.wait_for(tarea, 15)

    asyncio.run(principal())
    from gpiozero import Device

    if Device.pin_factory is not None:
        Device.pin_factory.close()
        Device.pin_factory = None


def test_gestor_aplica_el_alta_y_acciona_la_carga(sim):
    async def escenario(gestor):
        assert await hasta(lambda: gestor.revision_aplicada)
        sim.tablero.conectar("rele", True)
        cmd = orden(gestor, "rele", "encender")
        gestor.encolar(cmd)
        assert await hasta(lambda: gestor.resultados[cmd["id"]]["estado"] == "APLICADO")
        assert fisico(sim.tablero, "rele")["carga_activa"] is True
        assert gestor.estado()["componentes"]["rele"]["valores"]["salida_activa"] is True

    correr_gestor(sim, config(rele=SALIDA), escenario)
    # Un apagado ordenado deja la carga sin accionar.
    assert fisico(sim.tablero, "rele")["carga_activa"] is False


def test_gestor_con_cable_suelto_informa_aplicado_igual(sim):
    async def escenario(gestor):
        assert await hasta(lambda: gestor.revision_aplicada)
        cmd = orden(gestor, "rele", "encender")
        gestor.encolar(cmd)
        assert await hasta(lambda: gestor.resultados[cmd["id"]]["estado"] == "APLICADO")
        assert fisico(sim.tablero, "rele")["carga_activa"] is False

    correr_gestor(sim, config(rele=SALIDA), escenario)


def test_gestor_con_pin_ocupado_entra_en_emergencia_y_lo_informa(sim):
    sim.tablero.fijar_falla("ocupado", True, 17)

    async def escenario(gestor):
        assert await hasta(lambda: gestor.error_config)
        assert "GPIO busy" in gestor.error_config
        assert gestor.emergencia is True
        assert gestor.revision_aplicada is None

    correr_gestor(sim, config(rele=SALIDA), escenario)


@pytest.mark.xfail(
    strict=True,
    reason="gpiozero reserva el pin antes de abrirlo: tras GPIO busy la reserva sigue "
    "viva hasta que corre el recolector de basura, y reintentar falla.",
)
def test_gestor_se_recupera_al_reintentar_con_el_pin_liberado(sim):
    sim.tablero.fijar_falla("ocupado", True, 17)

    async def escenario(gestor):
        assert await hasta(lambda: gestor.error_config)
        sim.tablero.fijar_falla("ocupado", False, 17)
        gestor.intentada = None  # lo que hace POST /api/hardware/aplicar/
        assert await hasta(lambda: gestor.revision_aplicada), gestor.error_config

    correr_gestor(sim, config(rele=SALIDA), escenario)


def test_gestor_con_pin_colgado_no_se_congela(sim):
    async def escenario(gestor):
        assert await hasta(lambda: gestor.revision_aplicada)
        sim.tablero.fijar_falla("colgado", True, 17)
        assert await hasta(lambda: gestor.emergencia, plazo=8)
        assert "rele" in gestor.estado()["componentes"]
        # El ciclo sigue avanzando: el SCADA conserva el estado del hardware.
        ciclo = gestor._ciclo
        assert await hasta(lambda: gestor._ciclo != ciclo)

    correr_gestor(sim, config(rele=SALIDA), escenario)


# ── Página ────────────────────────────────────────────────────────────────


def http(sim, ruta, datos=None, tipo="application/json"):
    """Hace una petición a la página y devuelve (código, cuerpo)."""
    pedido = urllib.request.Request(
        f"http://127.0.0.1:{sim.puerto_http}{ruta}",
        data=None if datos is None else json.dumps(datos).encode(),
        headers={"Content-Type": tipo},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=5) as respuesta:
            return respuesta.status, respuesta.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_pagina_responde_y_lista_lo_dado_de_alta(sim):
    guardar_archivo(config(rele=SALIDA), sim.ruta_config)
    codigo, cuerpo = http(sim, "/")
    assert codigo == 200 and b"<title>" in cuerpo
    codigo, cuerpo = http(sim, "/api/estado")
    assert codigo == 200
    assert [c["nombre"] for c in json.loads(cuerpo)["componentes"]] == ["rele"]


def test_pagina_conecta_acciona_y_fija_fallas(sim):
    guardar_archivo(config(sensor=ENTRADA, agua=LM35), sim.ruta_config)
    sim.sincronizar_archivo()
    assert http(sim, "/api/conexion", {"componente": "sensor", "conectado": True})[0] == 200
    assert http(sim, "/api/contacto", {"componente": "sensor", "activo": True})[0] == 200
    assert fisico(sim.tablero, "sensor")["contacto"] is True
    assert http(sim, "/api/temperatura", {"componente": "agua", "valor": 31.5})[0] == 200
    assert fisico(sim.tablero, "agua")["temperatura_c"] == 31.5
    assert http(sim, "/api/falla", {"tipo": "ocupado", "activo": True, "gpio": 17})[0] == 200
    assert json.loads(http(sim, "/api/estado")[1])["pines"]["17"]["falla"] == "ocupado"


def test_pagina_rechaza_datos_invalidos_sin_caerse(sim):
    assert http(sim, "/api/conexion", {"componente": "nada", "conectado": True})[0] == 400
    assert http(sim, "/api/falla", {"tipo": "rayo", "activo": True})[0] == 400
    assert http(sim, "/api/temperatura", {"componente": "x", "valor": "mucho"})[0] == 400
    assert http(sim, "/api/inventada", {})[0] == 404
    assert http(sim, "/api/estado")[0] == 200


def test_pagina_rechaza_post_que_no_sea_json(sim):
    # Un formulario de otro sitio no puede accionar el simulador.
    datos = {"componente": "x", "conectado": True}
    assert http(sim, "/api/conexion", datos, tipo="text/plain")[0] == 415


def test_config_invalida_avisa_y_conserva_el_ultimo_inventario(sim):
    guardar_archivo(config(rele=SALIDA), sim.ruta_config)
    sim.sincronizar_archivo()
    Path(sim.ruta_config).write_text("{roto", encoding="utf-8")
    estado = json.loads(http(sim, "/api/estado")[1])
    assert estado["error_config"]
    assert [c["nombre"] for c in estado["componentes"]] == ["rele"]


def test_la_pagina_muestra_los_nombres_como_texto():
    pagina = (Path(__file__).parents[1] / "pi_sim" / "pagina.html").read_text(encoding="utf-8")
    assert "innerHTML" not in pagina


def test_pulso_real_por_gpiozero_abre_y_cierra_en_el_tablero(sim):
    flancos = []
    original = sim.tablero.escribir

    def registrar(handle, gpio, nivel):
        resultado = original(handle, gpio, nivel)
        if gpio == 27:
            flancos.append((time.perf_counter(), 1 if nivel else 0))
        return resultado

    sim.tablero.escribir = registrar

    async def escenario(gestor):
        assert await hasta(lambda: gestor.revision_aplicada)
        await gestor.ejecutar_y_esperar("sh", "armar", {})
        salida = await gestor.ejecutar_y_esperar("sh", "pulso", {"duracion_ms": 400})
        assert salida["cancelado"] is False

    correr_gestor(sim, config(sh=SHUTTER), escenario)
    subida = next(t for t, n in flancos if n == 1)
    bajada = next(t for t, n in flancos if n == 0 and t > subida)
    assert abs((bajada - subida) * 1000 - 400) < 20


def test_la_imagen_del_simulador_copia_lo_que_valida_roles(tmp_path):
    """La imagen de pi-sim copia solo algunos módulos de ``controller``.

    Con esos solos tiene que poder validar un inventario con rol: antes faltaba
    ``roles.py`` y la página se caía al dar de alta un componente con rol.
    """
    import os
    import re
    import subprocess

    raiz = Path(__file__).parents[1]
    dockerfile = (raiz / "docker" / "pi_sim" / "Dockerfile").read_text(encoding="utf-8")
    for linea in re.findall(r"^COPY .*$", dockerfile, flags=re.M):
        partes = [p for p in linea.split()[1:] if not p.startswith("--")]
        destino = tmp_path / partes[-1].strip("/").removeprefix("app").strip("/")
        destino.mkdir(parents=True, exist_ok=True)
        for origen in partes[:-1]:
            (destino / Path(origen).name).write_bytes((raiz / origen).read_bytes())
    prueba = tmp_path / "prueba.py"
    prueba.write_text(
        "from controller.hardware_config import validar_configuracion\n"
        "sh = {'tipo': 'SHUTTER_GPIO', 'pines': {'pin_se\u00f1al': 17},\n"
        "      'estado': 'Vigente', 'rol': 'SHUTTER'}\n"
        "validar_configuracion({'componentes': {'sh': sh}})\n",
        encoding="utf-8",
    )
    entorno = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    resultado = subprocess.run(
        [sys.executable, "-E", "-s", "-X", "utf8", str(prueba)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=entorno,
    )
    assert resultado.returncode == 0, resultado.stderr
