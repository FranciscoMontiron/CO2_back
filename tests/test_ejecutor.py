"""Ejecutor de programas por pasos: mismo árbol en simulado y en GPIO.

Toda salida que no sea el fin normal (falla, aborto) deja el shutter cerrado,
el láser apagado y los motores frenados, emite ``abortado`` y libera la
ejecución. Una emergencia cancela de inmediato y no emite ``fin``.
"""

import asyncio
import contextlib
import time

import pytest

from controller import protocolo as p
from controller.hal_base import OperacionRechazada
from controller.maquina import GRABANDO, LISTO, Maquina
from controller.programa import INVENTARIO_SIMULADO
from controller.rpi import HalGpio
from controller.simulador import HalSimulado
from tests.test_hardware import esperar, sistema

# ── Árbol ────────────────────────────────────────────────────────────────────


def accion(componente, comando, **argumentos):
    return {
        "tipo": "ACCION",
        "componente": componente,
        "comando": comando,
        "argumentos": argumentos,
    }


def bucle(repeticiones, *pasos):
    return {"tipo": "BUCLE", "repeticiones": repeticiones, "pasos": list(pasos)}


def paralelo(*pasos):
    return {"tipo": "PARALELO", "pasos": list(pasos)}


def iniciar(pasos, modo="REAL"):
    return {
        "accion": p.INICIAR,
        "registro": "ENS-T7",
        "modo": modo,
        "programa": {"codigo": "PRG-T7", "nombre": "Prueba T7", "pasos": pasos},
    }


def tipos(eventos, tipo):
    return [d for t, d in eventos if t == tipo]


# ── Máquinas en los dos modos ────────────────────────────────────────────────


async def _armar_simulado(m):
    await m.manejar({"accion": p.ENCENDER_REFRIGERACION})
    m.tick(5)
    await m.manejar({"accion": p.HABILITAR_AT})
    m.tick(5)
    await m.manejar({"accion": p.ALINEAR_FIBRA})
    m.tick(4)
    await m.manejar({"accion": p.ARMAR_SHUTTER})
    m.tick(0.1)
    assert m.estado == LISTO


@contextlib.asynccontextmanager
async def _latido(m, paso):
    async def latir():
        # Como ``main.py``: avanza lo que pasó de verdad, aunque el sueño se atrase.
        anterior = time.monotonic()
        while True:
            await asyncio.sleep(paso)
            ahora = time.monotonic()
            m.tick((ahora - anterior) * m.velocidad)
            anterior = ahora

    tarea = asyncio.create_task(latir())
    try:
        yield
    finally:
        tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea


@contextlib.asynccontextmanager
async def maquina_de(modo, tmp_path, velocidad=20.0, componentes=None):
    """Entrega ``(maquina, eventos, backend)``; el backend es None en simulado."""
    eventos = []

    def emitir(tipo, datos):
        eventos.append((tipo, datos))

    if modo == "simulado":
        m = Maquina(HalSimulado(semilla=1), emitir=emitir, velocidad=velocidad)
        await _armar_simulado(m)
        async with _latido(m, 0.005):
            yield m, eventos, None
        return
    comps = componentes if componentes is not None else INVENTARIO_SIMULADO["componentes"]
    async with sistema(tmp_path, comps) as (gestor, backend, _):
        m = Maquina(HalGpio(gestor), emitir=emitir)
        async with _latido(m, 0.02):
            yield m, eventos, backend


@pytest.fixture(params=["simulado", "gpio"])
def modo(request):
    return request.param


PIN_LASER, PIN_SHUTTER = 17, 27


# ── Pruebas ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_programa_secuencial_emite_marcas_y_fin(modo, tmp_path):
    pasos = [
        accion("shutter", "armar"),
        accion("motor_x", "referenciar", posicion=0.1),
        bucle(
            3,
            accion("shutter", "pulso", duracion_ms=20),
            accion("motor_x", "mover", delta=0.1, velocidad=5),
        ),
    ]
    async with maquina_de(modo, tmp_path) as (m, eventos, _):
        await m.manejar(iniciar(pasos))
        assert m.estado == GRABANDO
        await esperar(lambda: m.ejecucion is None, 10)

    marcas = tipos(eventos, p.EV_MARCA)
    assert [mk["numero"] for mk in marcas] == [1, 2, 3]
    assert [mk["posicion_um"] for mk in marcas] == [100.0, 200.0, 300.0]
    for mk in marcas:
        assert mk["registro"] == "ENS-T7"
        assert mk["cant_pulsos"] == 1
        assert mk["tiempo_de_pulso"] == pytest.approx(20, abs=5)
    (fin,) = tipos(eventos, p.EV_FIN)
    assert fin["marcas"] == 3
    assert [t for t, _ in eventos].index(p.EV_FIN) > max(
        i for i, (t, _) in enumerate(eventos) if t == p.EV_MARCA
    )
    if modo == "gpio":
        assert fin["espectro"] is None
    else:
        assert len(fin["espectro"]["longitudes_onda"]) == len(fin["espectro"]["transmitancias"])
    assert tipos(eventos, p.EV_ABORTADO) == []


@pytest.mark.asyncio
async def test_paralelo_corre_a_la_vez(tmp_path):
    pasos = [
        accion("shutter", "armar"),
        accion("motor_x", "referenciar", posicion=0),
        paralelo(
            accion("shutter", "pulso", duracion_ms=200),
            accion("motor_x", "mover", delta=1, velocidad=5),  # 0,2 s
        ),
    ]
    async with maquina_de("simulado", tmp_path, velocidad=1.0) as (m, eventos, _):
        t0 = time.monotonic()
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 5)
        demora = time.monotonic() - t0
    assert 0.19 < demora < 0.35
    assert len(tipos(eventos, p.EV_FIN)) == 1


@pytest.mark.asyncio
async def test_fallo_a_mitad_cierra_todo_y_emite_abortado(modo, tmp_path):
    pasos = [
        accion("laser", "encender"),
        accion("shutter", "armar"),
        accion("motor_x", "referenciar", posicion=0),
        bucle(
            5,
            accion("shutter", "pulso", duracion_ms=20),
            accion("motor_x", "mover", delta=0.1, velocidad=5),
        ),
    ]
    async with maquina_de(modo, tmp_path) as (m, eventos, backend):
        original = m.hal.operar
        pulsos = []

        async def operar_con_falla(componente, comando, argumentos):
            if comando == "pulso":
                pulsos.append(1)
                if len(pulsos) == 2:
                    raise OperacionRechazada("shutter.pulso: falla simulada del shutter")
            return await original(componente, comando, argumentos)

        m.hal.operar = operar_con_falla
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 10)
        if modo == "gpio":
            gestor = m.hal.hardware
            await esperar(lambda: gestor._parando is not None and gestor._parando.done())
            assert backend.pines[PIN_LASER].value is False
            assert backend.pines[PIN_SHUTTER].value is False
            assert gestor.drivers["shutter"].armado is False
            assert gestor.drivers["motor_x"].referenciado is False
        else:
            assert m.hal.laser is False
            assert m.hal.shutter_abierto is False
            assert m.hal.shutter_armado is False
            assert m.hal.motor_en_destino

    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert abortado["registro"] == "ENS-T7"
    assert abortado["marcas"] == 1
    assert "falla simulada" in abortado["motivo"]
    assert tipos(eventos, p.EV_FIN) == []
    assert m.ejecucion is None
    assert m.estado != GRABANDO


@pytest.mark.asyncio
async def test_abortar_espera_la_accion_en_curso_y_no_sigue_el_bucle(modo, tmp_path):
    pasos = [accion("shutter", "armar"), bucle(10_000, accion("shutter", "pulso", duracion_ms=300))]
    async with maquina_de(modo, tmp_path, velocidad=1.0) as (m, eventos, backend):

        def abierto():
            if modo == "gpio":
                return backend.pines[PIN_SHUTTER].value
            return m.hal.shutter_abierto

        await m.manejar(iniciar(pasos))
        await esperar(abierto)
        ejecucion = m.telemetria(1.0)["ejecucion"]
        assert set(ejecucion) >= {
            "registro",
            "programa",
            "modo",
            "acciones",
            "total",
            "pulsos",
            "ubicacion",
            "ultimo_pulso_ms",
        }
        assert ejecucion["total"] == 10_001
        await m.manejar({"accion": p.ABORTAR})
        assert m.ejecucion is not None  # no corta el pulso: espera a que termine
        await esperar(lambda: m.ejecucion is None, 5)

    (marca,) = tipos(eventos, p.EV_MARCA)
    assert marca["tiempo_de_pulso"] == pytest.approx(300, abs=5)
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert abortado["marcas"] == 1
    assert "operador" in abortado["motivo"]
    assert tipos(eventos, p.EV_FIN) == []


@pytest.mark.asyncio
async def test_emergencia_en_paralelo_cierra_el_shutter_y_frena_el_motor(tmp_path):
    pasos = [
        accion("shutter", "armar"),
        accion("motor_x", "referenciar", posicion=0),
        paralelo(
            accion("shutter", "pulso", duracion_ms=3000),
            accion("motor_x", "mover", delta=50, velocidad=5),
        ),
    ]
    async with maquina_de("gpio", tmp_path) as (m, eventos, backend):
        gestor = m.hal.hardware
        motor = gestor.drivers["motor_x"]
        await m.manejar(iniciar(pasos))
        # Los dos a la vez: el haz abierto y el eje avanzando cuando llega la emergencia.
        await esperar(lambda: backend.pines[PIN_SHUTTER].value and motor.en_movimiento)
        pasos_al_parar = motor.pasos
        assert pasos_al_parar > 0
        t0 = time.monotonic()
        await m.manejar({"accion": p.EMERGENCIA, "origen": "OPERADOR"})
        await esperar(lambda: not backend.pines[PIN_SHUTTER].value, 2)
        assert time.monotonic() - t0 < 1.0  # mucho antes de los 3 s del pulso
        await esperar(lambda: gestor._parando.done() and m.ejecucion is None)
        assert motor.referenciado is False
        assert motor.en_movimiento is False
        assert motor.pasos < 50 * 200  # frenó mucho antes del destino
        await asyncio.sleep(0.1)
    assert tipos(eventos, p.EV_FIN) == []
    assert tipos(eventos, p.EV_MARCA) == []  # el pulso cortado no es una marca
    assert m.ejecucion is None


@pytest.mark.asyncio
async def test_gpio_paralelo_corre_a_la_vez(tmp_path):
    async with maquina_de("gpio", tmp_path) as (m, eventos, _):
        await m.hal.operar("motor_x", "referenciar", {"posicion": 0})
        t0 = time.monotonic()
        await m.hal.operar("motor_x", "mover", {"delta": 0.2, "velocidad": 5})
        movimiento = time.monotonic() - t0
        # Un pulso que dura lo mismo que el movimiento: en serie tardarían el doble.
        pulso_ms = max(50, min(3000, round(movimiento * 1000)))
        pasos = [
            accion("shutter", "armar"),
            paralelo(
                accion("shutter", "pulso", duracion_ms=pulso_ms),
                accion("motor_x", "mover", delta=-0.2, velocidad=5),
            ),
        ]
        t0 = time.monotonic()
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 10)
        demora = time.monotonic() - t0
    assert len(tipos(eventos, p.EV_FIN)) == 1, tipos(eventos, p.EV_ABORTADO)
    assert demora < 1.5 * max(movimiento, pulso_ms / 1000) + 0.1


@pytest.mark.asyncio
async def test_falla_al_terminar_sale_por_la_ruta_unica(tmp_path):
    pasos = [
        accion("laser", "encender"),
        accion("motor_x", "referenciar", posicion=0.1),
        bucle(
            2,
            accion("shutter", "pulso", duracion_ms=20),
            accion("motor_x", "mover", delta=0.1, velocidad=5),
        ),
    ]
    async with maquina_de("simulado", tmp_path) as (m, eventos, _):

        def espectro_roto(*_args, **_kw):
            raise RuntimeError("OSA desconectado")

        m.hal.espectro = espectro_roto
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 5)
        assert m.hal.laser is False
        assert m.hal.shutter_armado is False
    assert tipos(eventos, p.EV_FIN) == []
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert "OSA desconectado" in abortado["motivo"]
    assert abortado["marcas"] == 2


@pytest.mark.asyncio
async def test_iniciar_es_atomico_si_falla_al_preparar(tmp_path):
    pasos = [accion("shutter", "pulso", duracion_ms=20)]
    async with maquina_de("simulado", tmp_path) as (m, eventos, _):

        def checkpoint_roto(*_args):
            raise RuntimeError("bus caído")

        m._checkpoint = checkpoint_roto
        await m.manejar(iniciar(pasos))
        assert m.ejecucion is None
        assert m._tarea is None
        assert m.estado == LISTO
        assert m.telemetria(1.0)["ejecucion"] is None
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert abortado["registro"] == "ENS-T7"
    assert abortado["motivo"].startswith("Rechazado")
    assert "bus caído" in abortado["motivo"]


@pytest.mark.asyncio
async def test_componente_dado_de_baja_rechaza_al_iniciar(tmp_path):
    componentes = dict(INVENTARIO_SIMULADO["componentes"])
    componentes["motor_x"] = {**componentes["motor_x"], "estado": "Eliminado"}
    del componentes["motor_x"]["rol"]
    pasos = [accion("shutter", "armar"), accion("motor_x", "mover", delta=1, velocidad=1)]
    async with maquina_de("gpio", tmp_path, componentes=componentes) as (m, eventos, _):
        await m.manejar(iniciar(pasos))
        assert m.ejecucion is None
        assert m.estado != GRABANDO
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert abortado["registro"] == "ENS-T7"
    assert abortado["marcas"] == 0
    assert "motor_x" in abortado["motivo"]


@pytest.mark.asyncio
async def test_modo_prueba_omite_el_laser(modo, tmp_path):
    pasos = [
        accion("laser", "encender"),
        accion("shutter", "armar"),
        accion("motor_x", "referenciar", posicion=0),
        bucle(
            2,
            accion("shutter", "pulso", duracion_ms=20),
            accion("motor_x", "mover", delta=0.1, velocidad=5),
        ),
        accion("laser", "apagar"),
    ]
    async with maquina_de(modo, tmp_path) as (m, eventos, backend):
        original = m.hal.operar
        llamadas = []

        async def espiar(componente, comando, argumentos):
            llamadas.append((componente, comando))
            return await original(componente, comando, argumentos)

        m.hal.operar = espiar
        await m.manejar(iniciar(pasos, modo="PRUEBA"))
        await esperar(lambda: m.ejecucion is None, 10)
        if modo == "gpio":
            assert True not in backend.pines[PIN_LASER].historial
        else:
            assert m.hal.laser is False
    assert not [c for c in llamadas if c[0] == "laser"]
    assert ("shutter", "pulso") in llamadas
    (fin,) = tipos(eventos, p.EV_FIN)
    assert fin["espectro"] is None
    assert tipos(eventos, p.EV_ABORTADO) == []


def _lm35(rol="TEMPERATURA_AGUA"):
    return {
        "tipo": "LM35",
        "pines": {},
        "parametros": {"adc": "MCP3008", "canal": 0, "dispositivo_spi": 0, "vref": 3.3},
        "estado": "Vigente",
        "rol": rol,
    }


@pytest.mark.asyncio
async def test_gpio_no_arranca_con_rol_critico_fuera_de_umbral(tmp_path):
    componentes = {**INVENTARIO_SIMULADO["componentes"], "agua": _lm35()}  # lee 23,5 °C
    pasos = [accion("shutter", "armar")]
    async with maquina_de("gpio", tmp_path, componentes=componentes) as (m, eventos, _):
        m.umbrales = [
            {"variable": "TEMPERATURA_AGUA", "valor_min": 15, "valor_max": 20, "accion": "ALERTA"}
        ]
        await m.manejar(iniciar(pasos))
        assert m.ejecucion is None
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert "TEMPERATURA_AGUA" in abortado["motivo"]
    assert tipos(eventos, p.EV_FIN) == []


@pytest.mark.asyncio
async def test_gpio_no_arranca_con_rol_critico_sin_dato(tmp_path):
    componentes = {**INVENTARIO_SIMULADO["componentes"], "agua": _lm35()}
    pasos = [accion("shutter", "armar")]
    async with maquina_de("gpio", tmp_path, componentes=componentes) as (m, eventos, _):
        m.hal.hardware.muestras["agua"]["calidad"] = "ERROR"
        await m.manejar(iniciar(pasos))
        assert m.ejecucion is None
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert "TEMPERATURA_AGUA" in abortado["motivo"]


@pytest.mark.asyncio
async def test_gpio_arranca_sin_roles_criticos_con_aviso(tmp_path):
    pasos = [accion("shutter", "armar"), accion("shutter", "pulso", duracion_ms=20)]
    async with maquina_de("gpio", tmp_path) as (m, eventos, _):
        avisos = []
        original = m._avisar

        def registrar(texto, nivel="info"):
            avisos.append((nivel, texto))
            original(texto, nivel)

        m._avisar = registrar
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 5)
    advertencias = [t for n, t in avisos if n == "warn"]
    assert any("TEMPERATURA_AGUA" in t for t in advertencias)
    # No da a entender que lo asignado se vigila durante la corrida.
    assert not any("sin vigilar esas lecturas" in t for t in advertencias)
    assert any("solo se verifican al iniciar" in t for t in advertencias)
    assert len(tipos(eventos, p.EV_FIN)) == 1
    assert len(tipos(eventos, p.EV_MARCA)) == 1


@pytest.mark.asyncio
async def test_gpio_avisa_siempre_que_no_vigila_umbrales_durante_el_programa(tmp_path):
    def lm35(rol, canal):
        return {**_lm35(rol), "parametros": {**_lm35()["parametros"], "canal": canal}}

    componentes = {
        **INVENTARIO_SIMULADO["componentes"],
        "agua": lm35("TEMPERATURA_AGUA", 0),
        "caudal": lm35("CAUDAL_REFRIGERANTE", 1),
        "at": lm35("TENSION_AT", 2),
    }
    pasos = [accion("shutter", "armar")]
    async with maquina_de("gpio", tmp_path, componentes=componentes) as (m, eventos, _):
        avisos = []
        original = m._avisar

        def registrar(texto, nivel="info"):
            avisos.append((nivel, texto))
            original(texto, nivel)

        m._avisar = registrar
        m.umbrales = []  # las tensiones del LM35 no son caudales ni kV reales
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 5)
    assert len(tipos(eventos, p.EV_FIN)) == 1, tipos(eventos, p.EV_ABORTADO)
    advertencias = [t for n, t in avisos if n == "warn"]
    assert any("solo se verifican al iniciar" in t for t in advertencias)
    assert not any("Sin rol asignado" in t for t in advertencias)


@pytest.mark.asyncio
async def test_gpio_no_arranca_con_el_gestor_en_emergencia(tmp_path):
    pasos = [accion("shutter", "armar")]
    async with maquina_de("gpio", tmp_path) as (m, eventos, _):
        m.hal.hardware.detener()
        await m.manejar(iniciar(pasos))
        assert m.ejecucion is None
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert abortado["motivo"].startswith("Rechazado")


@pytest.mark.asyncio
async def test_simulado_arranca_sin_el_laser_encendido(tmp_path):
    pasos = [accion("shutter", "pulso", duracion_ms=20)]
    async with maquina_de("simulado", tmp_path) as (m, eventos, _):
        assert m.hal.laser is False
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 5)
    assert len(tipos(eventos, p.EV_FIN)) == 1


@pytest.mark.asyncio
async def test_gpio_parada_del_gestor_durante_una_espera_termina_el_programa(tmp_path):
    pasos = [accion("shutter", "armar"), {"tipo": "ESPERA", "duracion_ms": 60_000}]
    async with maquina_de("gpio", tmp_path) as (m, eventos, _):
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.telemetria(1.0)["ejecucion"]["acciones"] == 1)
        m.hal.hardware.detener()  # p. ej. una lectura fallida
        await esperar(lambda: m.ejecucion is None, 2)
        await asyncio.sleep(0.05)
        assert m.estado != GRABANDO
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert "Hardware detenido" in abortado["motivo"]
    assert tipos(eventos, p.EV_FIN) == []


@pytest.mark.asyncio
async def test_gpio_parada_del_gestor_nombra_el_componente_y_avisa(tmp_path):
    """Visto en el simulador de Pi 5: con un pin colgado el motivo solo decía
    «Hardware detenido» y la telemetría seguía mostrando «Grabando…»."""
    pasos = [accion("shutter", "armar"), {"tipo": "ESPERA", "duracion_ms": 60_000}]
    async with maquina_de("gpio", tmp_path) as (m, eventos, _):
        avisos = []
        original = m._avisar

        def registrar(texto, nivel="info"):
            avisos.append((nivel, texto))
            original(texto, nivel)

        m._avisar = registrar
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.telemetria(1.0)["ejecucion"]["acciones"] == 1)
        m.hal.hardware.detener("lectura de shutter: TimeoutError")
        await esperar(lambda: m.ejecucion is None, 2)
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert "Hardware detenido" in abortado["motivo"]
    assert "lectura de shutter: TimeoutError" in abortado["motivo"]
    assert any(n == "warn" and abortado["motivo"] in t for n, t in avisos)


@pytest.mark.asyncio
async def test_abortar_corta_una_espera(tmp_path):
    pasos = [accion("shutter", "armar"), {"tipo": "ESPERA", "duracion_ms": 60_000}]
    async with maquina_de("simulado", tmp_path, velocidad=1.0) as (m, eventos, _):
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.telemetria(1.0)["ejecucion"]["acciones"] == 1)
        await m.manejar({"accion": p.ABORTAR})
        await esperar(lambda: m.ejecucion is None, 1)
    (abortado,) = tipos(eventos, p.EV_ABORTADO)
    assert "operador" in abortado["motivo"]


@pytest.mark.asyncio
async def test_fin_normal_cierra_y_desarma_el_shutter_y_avisa_por_el_laser(modo, tmp_path):
    """Un programa que deja el shutter abierto no deja el haz sobre la fibra al terminar.

    El láser no se apaga solo (encenderlo es un paso del programa), pero se avisa.
    """
    pasos = [
        accion("laser", "encender"),
        accion("shutter", "armar"),
        accion("shutter", "abrir"),
    ]
    async with maquina_de(modo, tmp_path) as (m, eventos, backend):
        avisos = []
        original = m._avisar

        def registrar(texto, nivel="info"):
            avisos.append((nivel, texto))
            original(texto, nivel)

        m._avisar = registrar
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 5)
        if modo == "gpio":
            gestor = m.hal.hardware
            assert backend.pines[PIN_SHUTTER].value is False
            assert gestor.drivers["shutter"].armado is False
            assert backend.pines[PIN_LASER].value is True  # el ejecutor no lo apaga
        else:
            assert m.hal.shutter_abierto is False
            assert m.hal.shutter_armado is False
    assert len(tipos(eventos, p.EV_FIN)) == 1
    assert tipos(eventos, p.EV_ABORTADO) == []
    assert any(n == "warn" and "con el láser encendido" in t for n, t in avisos)


@pytest.mark.asyncio
async def test_fin_normal_sin_laser_no_avisa(tmp_path):
    pasos = [accion("shutter", "armar"), accion("shutter", "pulso", duracion_ms=20)]
    async with maquina_de("gpio", tmp_path) as (m, eventos, _):
        avisos = []
        original = m._avisar

        def registrar(texto, nivel="info"):
            avisos.append((nivel, texto))
            original(texto, nivel)

        m._avisar = registrar
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 5)
        assert m.hal.hardware.drivers["shutter"].armado is False
    assert len(tipos(eventos, p.EV_FIN)) == 1
    assert not any("láser" in t for _, t in avisos)


@pytest.mark.asyncio
async def test_gpio_sin_rol_laser_avisa_lo_que_quedo_encendido(tmp_path):
    componentes = dict(INVENTARIO_SIMULADO["componentes"])
    componentes["laser"] = {k: v for k, v in componentes["laser"].items() if k != "rol"}
    pasos = [accion("laser", "encender"), accion("shutter", "armar")]
    async with maquina_de("gpio", tmp_path, componentes=componentes) as (m, eventos, _):
        avisos = []
        original = m._avisar

        def registrar(texto, nivel="info"):
            avisos.append((nivel, texto))
            original(texto, nivel)

        m._avisar = registrar
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 5)
    assert len(tipos(eventos, p.EV_FIN)) == 1
    assert any(n == "warn" and "Quedaron encendidos: laser" in t for n, t in avisos)


@pytest.mark.asyncio
async def test_gpio_el_aviso_de_umbrales_queda_publicado_al_arrancar(tmp_path):
    """El «Grabando…» no pisa el aviso: el operador lo ve en la telemetría."""
    pasos = [accion("shutter", "armar"), {"tipo": "ESPERA", "duracion_ms": 60_000}]
    async with maquina_de("gpio", tmp_path) as (m, _eventos, _):
        await m.manejar(iniciar(pasos))
        mensaje = m.telemetria(1.0)["mensaje"]
        await m.manejar({"accion": p.ABORTAR})
        await esperar(lambda: m.ejecucion is None, 5)
    assert mensaje["nivel"] == "warn"
    assert "Grabando" in mensaje["texto"]
    assert "solo se verifican al iniciar" in mensaje["texto"]


@pytest.mark.asyncio
async def test_simulado_el_fin_conserva_el_mensaje_y_dice_que_el_enclavamiento_apago_el_laser(
    tmp_path,
):
    pasos = [accion("laser", "encender"), accion("shutter", "pulso", duracion_ms=20)]
    async with maquina_de("simulado", tmp_path) as (m, _eventos, _):
        await m.manejar(iniciar(pasos))
        await esperar(lambda: m.ejecucion is None, 5)
        mensaje = m.telemetria(1.0)["mensaje"]
        assert m.hal.laser is False
    assert mensaje["nivel"] == "warn"
    assert "Programa terminado: 1 marcas" in mensaje["texto"]
    assert "el enclavamiento apagó el láser" in mensaje["texto"]
