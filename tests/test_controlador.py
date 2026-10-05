"""Pruebas del proceso controlador: simulador fisico, maquina de estados y bus.

No tocan Django ni la base: el controlador no puede depender de ellos
(ADR-0002). La maquina se prueba con el tiempo acelerado; el bus, contra un
Redis falso en memoria.
"""

import asyncio
import json

import pytest

from controller import main
from controller import protocolo as p
from controller.maquina import (
    EMERGENCIA,
    GRABANDO,
    LISTO,
    PREPARANDO,
    REPOSO,
    Maquina,
    distancia_de_marca,
)
from controller.simulador import DELTA_N_PRINCIPAL, HalSimulado


@pytest.mark.asyncio
async def test_el_bus_publica_la_apertura_de_cada_pulso_corto(monkeypatch):
    """Un pulso menor a un segundo no queda oculto por el muestreo periodico."""
    monkeypatch.setattr(main, "TELEMETRY_HZ", 1.0)
    maquina, _ = _maquina(velocidad=1)
    await _armar(maquina)
    redis = RedisFalso()
    parar = asyncio.Event()
    publicador = asyncio.create_task(main.publicar_telemetria(redis, maquina, parar))
    try:
        async with _Reloj(maquina):
            await asyncio.sleep(0.06)
            await maquina.manejar(_programa(pulsos=2))
            await _esperar(lambda: maquina.ejecucion is None)
            await asyncio.sleep(0.06)
        abiertos = [
            json.loads(mensaje)
            for _, mensaje in redis.publicados
            if json.loads(mensaje)["actuadores"]["shutter_abierto"]
        ]
        assert {mensaje["ejecucion"]["pulso"] for mensaje in abiertos} == {0, 1}
        assert json.loads(redis.publicados[-1][1])["actuadores"]["shutter_abierto"] is False
    finally:
        parar.set()
        await asyncio.wait_for(publicador, timeout=3)


# ── Simulador fisico ─────────────────────────────────────────────────────────


def _avanzar(hal, segundos, paso=0.05):
    for _ in range(int(segundos / paso)):
        hal.avanzar(paso)


def test_el_equipo_arranca_apagado_a_temperatura_ambiente():
    """Antes de encender nada no hay caudal, ni tension, ni potencia."""
    lect = HalSimulado(semilla=1).lecturas()
    assert lect["CAUDAL_REFRIGERANTE"] == 0
    assert lect["TENSION_AT"] == 0
    assert lect["POTENCIA_LASER"] == 0
    assert 22 < lect["TEMPERATURA_AGUA"] < 24


def test_la_refrigeracion_tiene_inercia():
    """El caudal sube en un par de segundos; el agua se enfria de a poco."""
    hal = HalSimulado(semilla=1)
    hal.refrigeracion = True
    _avanzar(hal, 0.5)
    assert hal.caudal < 2.0  # todavia no
    _avanzar(hal, 5)
    assert hal.caudal > 4.0
    assert hal.temp_agua < 23.0  # enfriando hacia el setpoint del chiller


def test_la_alta_tension_sube_en_rampa():
    """Una fuente de alta tension no salta al nominal."""
    hal = HalSimulado(semilla=1)
    hal.alta_tension = True
    _avanzar(hal, 0.3)
    assert 0 < hal.tension_kv < 15
    _avanzar(hal, 4)
    assert hal.tension_kv == pytest.approx(18.0, abs=0.1)


def test_sin_alta_tension_el_laser_no_emite():
    """El laser es una descarga: sin tension no hay corriente ni potencia."""
    hal = HalSimulado(semilla=1)
    hal.laser = True
    _avanzar(hal, 2)
    assert hal.corriente_ma == pytest.approx(0, abs=1e-3)


def test_con_falla_de_sobretemperatura_el_agua_se_calienta_grabando():
    """El chiller bombea pero no enfria: con el laser encendido el agua cruza 28 C."""
    hal = HalSimulado(semilla=1)
    hal.refrigeracion = hal.alta_tension = hal.laser = True
    _avanzar(hal, 90)  # que el agua llegue al equilibrio con el chiller
    normal = hal.temp_agua
    hal.falla = p.FALLA_SOBRETEMPERATURA
    _avanzar(hal, 15)
    assert normal < 22
    assert hal.temp_agua > 28


def test_la_falla_de_caudal_hace_caer_el_caudal():
    """La bomba pierde caudal aunque la refrigeracion este encendida."""
    hal = HalSimulado(semilla=1)
    hal.refrigeracion = True
    _avanzar(hal, 5)
    hal.falla = p.FALLA_CAUDAL
    _avanzar(hal, 5)
    assert hal.caudal < 1.0


def test_el_motor_tarda_lo_que_marca_su_velocidad():
    """2 mm/s: ir a 1 mm lleva medio segundo."""
    hal = HalSimulado(semilla=1)
    assert hal.mover_a(1.0) == pytest.approx(0.5)
    _avanzar(hal, 0.25, paso=0.01)
    assert 0.3 < hal.posicion_mm < 0.7
    _avanzar(hal, 0.5, paso=0.01)
    assert hal.motor_en_destino


def test_la_alineacion_lleva_unos_segundos():
    """El sensor de sombra pasa de 2,4 mW a ~0,12 mW mientras se alinea."""
    hal = HalSimulado(semilla=1)
    hal.alinear()
    _avanzar(hal, 1)
    assert hal.alineando and not hal.fibra_alineada
    _avanzar(hal, 3)
    assert hal.fibra_alineada
    assert hal.sensor_sombra_mw == pytest.approx(0.12)


def test_la_resonancia_cae_donde_dice_la_fisica_de_la_lpg():
    """lambda = delta_n x periodo: con 550 um, en ~1606 nm."""
    lambdas, dbs = HalSimulado(semilla=1).espectro(periodo_um=550.0, marcas=18)
    minimo = lambdas[dbs.index(min(dbs))]
    assert minimo == pytest.approx(DELTA_N_PRINCIPAL * 550_000, abs=2.0)


def test_mas_marcas_dan_una_resonancia_mas_profunda():
    """Mas marcas, mas acoplamiento (hasta el sobreacople)."""
    hal = HalSimulado(semilla=1)
    _, pocas = hal.espectro(550.0, marcas=8)
    _, muchas = hal.espectro(550.0, marcas=22)
    assert min(muchas) < min(pocas)


# ── Maquina de estados ───────────────────────────────────────────────────────


def _maquina(velocidad=200.0):
    eventos = []
    m = Maquina(
        HalSimulado(semilla=1), emitir=lambda t, d: eventos.append((t, d)), velocidad=velocidad
    )
    return m, eventos


def _tipos(eventos, tipo):
    return [d for t, d in eventos if t == tipo]


async def _armar(m, con_laser=True):
    """Secuencia de armado completa, avanzando el tiempo simulado."""
    await m.manejar({"accion": p.ENCENDER_REFRIGERACION})
    m.tick(5)
    await m.manejar({"accion": p.HABILITAR_AT})
    m.tick(5)
    await m.manejar({"accion": p.ALINEAR_FIBRA})
    m.tick(4)
    await m.manejar({"accion": p.ARMAR_SHUTTER})
    m.tick(0.1)
    if con_laser:
        await m.manejar({"accion": p.ENCENDER_LASER})
        m.tick(1)


class _Reloj:
    """Avanza la fisica en segundo plano mientras corre un programa."""

    def __init__(self, maquina):
        self.m = maquina
        self.tarea = None

    async def __aenter__(self):
        async def latir():
            while True:
                self.m.tick(0.005 * self.m.velocidad)
                await asyncio.sleep(0.005)

        self.tarea = asyncio.create_task(latir())
        return self

    async def __aexit__(self, *exc):
        self.tarea.cancel()


async def _esperar(predicado, tope=5.0):
    t = 0.0
    while not predicado():
        await asyncio.sleep(0.01)
        t += 0.01
        if t > tope:
            raise AssertionError("tiempo agotado")


def _programa(pulsos=3, modo="REAL", periodo=None):
    return {
        "accion": p.INICIAR,
        "registro": "ENS-TEST-1",
        "modo": modo,
        "programa": {
            "codigo": "PRG-T",
            "nombre": "Prueba",
            "potencia_objetivo_mw": 10.0,
            "duracion_pulso_ms": 100,
            "pulsos": pulsos,
            "criterio_fin": "CANT_MARCAS",
            "periodo": periodo
            or {"tipo": "CONSTANTE", "base_um": 550.0, "incremento_um": None, "factor": None},
        },
    }


@pytest.mark.asyncio
async def test_listo_llega_solo_cuando_se_cumplen_las_cuatro_condiciones():
    """LISTO no se comanda: se alcanza al cumplir la secuencia de armado."""
    m, _ = _maquina()
    assert m.estado == REPOSO
    await m.manejar({"accion": p.ENCENDER_REFRIGERACION})
    m.tick(5)
    assert m.estado == PREPARANDO
    await _armar(m, con_laser=False)
    assert m.estado == LISTO
    assert all(m.condiciones().values())


@pytest.mark.asyncio
async def test_la_alta_tension_exige_refrigeracion():
    """Sin caudal el tubo se dana: el controlador no habilita la AT."""
    m, _ = _maquina()
    await m.manejar({"accion": p.HABILITAR_AT})
    assert m.hal.alta_tension is False
    assert "refrigeracion" in m.mensaje["texto"]


@pytest.mark.asyncio
async def test_el_laser_solo_se_enciende_con_el_sistema_listo():
    """RN003: el lazo se cierra con el laser encendido, y el laser solo en LISTO."""
    m, _ = _maquina()
    await m.manejar({"accion": p.ENCENDER_LASER})
    assert m.hal.laser is False
    assert m.mensaje["nivel"] == "error"


@pytest.mark.asyncio
async def test_perder_una_condicion_apaga_el_laser():
    """No se deja emitiendo con el sistema fuera de condiciones."""
    m, _ = _maquina()
    await _armar(m)
    m.hal.desalinear()
    m.tick(0.1)
    assert m.hal.laser is False
    assert m.estado == PREPARANDO


@pytest.mark.asyncio
async def test_un_programa_completo_emite_marcas_checkpoints_y_fin():
    """El circuito entero: pulso, marca, desplazamiento, y el espectro al final."""
    m, eventos = _maquina()
    await _armar(m)
    async with _Reloj(m):
        await m.manejar(_programa(pulsos=4))
        assert m.estado == GRABANDO
        await _esperar(lambda: m.estado == LISTO)

    marcas = _tipos(eventos, p.EV_MARCA)
    assert [mk["numero"] for mk in marcas] == [1, 2, 3, 4]
    assert [mk["posicion_um"] for mk in marcas] == [550.0, 1100.0, 1650.0, 2200.0]
    assert len(_tipos(eventos, p.EV_CHECKPOINT)) >= 2
    (fin,) = _tipos(eventos, p.EV_FIN)
    assert fin["marcas"] == 4
    assert len(fin["espectro"]["longitudes_onda"]) == len(fin["espectro"]["transmitancias"])


@pytest.mark.asyncio
async def test_cada_red_arranca_en_el_origen_del_eje():
    """El desplazador vuelve a 0 antes de la primera marca."""
    m, eventos = _maquina()
    await _armar(m)
    m.hal.mover_a(5.0)
    m.tick(5)
    async with _Reloj(m):
        await m.manejar(_programa(pulsos=1))
        await _esperar(lambda: m.estado == LISTO)
    assert m.hal.posicion_mm == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_el_perfil_lineal_espacia_las_marcas_creciendo():
    """El periodo de cada marca sale del perfil del programa."""
    m, eventos = _maquina()
    await _armar(m)
    periodo = {"tipo": "LINEAL", "base_um": 500.0, "incremento_um": 10.0, "factor": None}
    async with _Reloj(m):
        await m.manejar(_programa(pulsos=3, periodo=periodo))
        await _esperar(lambda: m.estado == LISTO)
    assert [mk["posicion_um"] for mk in _tipos(eventos, p.EV_MARCA)] == [500.0, 1010.0, 1530.0]


@pytest.mark.asyncio
async def test_en_modo_prueba_no_se_graba():
    """PRUEBA recorre el programa con el laser inhibido: ni marcas ni espectro."""
    m, eventos = _maquina()
    await _armar(m)
    async with _Reloj(m):
        await m.manejar(_programa(pulsos=3, modo="PRUEBA"))
        await _esperar(lambda: m.estado == LISTO)
    assert _tipos(eventos, p.EV_MARCA) == []
    assert _tipos(eventos, p.EV_FIN)[0]["espectro"] is None


@pytest.mark.asyncio
async def test_abortar_corta_el_programa():
    """El operador lo corta al terminar el pulso en curso."""
    m, eventos = _maquina(velocidad=20.0)
    await _armar(m)
    async with _Reloj(m):
        await m.manejar(_programa(pulsos=50))
        await _esperar(lambda: m.ejecucion and m.ejecucion["pulso"] >= 1)
        await m.manejar({"accion": p.ABORTAR})
        await _esperar(lambda: m.estado == LISTO)
    (abortado,) = _tipos(eventos, p.EV_ABORTADO)
    assert 1 <= abortado["marcas"] < 50
    assert _tipos(eventos, p.EV_FIN) == []


@pytest.mark.asyncio
async def test_un_iniciar_rechazado_avisa_para_no_dejar_el_registro_huerfano():
    """Django ya creo el registro EN_CURSO: si el controlador no arranca, lo dice."""
    m, eventos = _maquina()
    await m.manejar(_programa())
    (abortado,) = _tipos(eventos, p.EV_ABORTADO)
    assert abortado["registro"] == "ENS-TEST-1"
    assert abortado["marcas"] == 0
    assert abortado["motivo"].startswith("Rechazado")


@pytest.mark.asyncio
async def test_el_e_stop_apaga_en_orden_y_mide_la_respuesta():
    """Shutter, laser y alta tension; la refrigeracion queda encendida."""
    m, eventos = _maquina()
    await _armar(m)
    async with _Reloj(m):
        await m.manejar(_programa(pulsos=50))
        await _esperar(lambda: m.ejecucion and m.ejecucion["pulso"] >= 1)
        await m.manejar({"accion": p.EMERGENCIA, "origen": "OPERADOR"})
        await asyncio.sleep(0.05)

    assert m.estado == EMERGENCIA
    assert not (m.hal.shutter_abierto or m.hal.laser or m.hal.alta_tension)
    assert m.hal.refrigeracion is True  # el tubo sigue caliente
    (emg,) = _tipos(eventos, p.EV_EMERGENCIA)
    assert emg["registro"] == "ENS-TEST-1"
    assert emg["origen"] == "OPERADOR"
    assert 0 < emg["tiempo_respuesta_ms"] < 500  # RNF001
    assert _tipos(eventos, p.EV_FIN) == []  # el programa no termino


@pytest.mark.asyncio
async def test_la_respuesta_se_mide_desde_el_envio_del_comando():
    """Incluye API y Redis: es la medicion de punta a punta de RNF001."""
    import time

    m, eventos = _maquina()
    enviado_hace_200_ms = int(time.time() * 1000) - 200
    await m.manejar({"accion": p.EMERGENCIA, "t_envio": enviado_hace_200_ms})
    assert _tipos(eventos, p.EV_EMERGENCIA)[0]["tiempo_respuesta_ms"] >= 200


@pytest.mark.asyncio
async def test_un_umbral_de_parada_dispara_la_emergencia_solo():
    """Caudal fuera de rango con alta tension: parada sin intervencion."""
    m, eventos = _maquina()
    await _armar(m, con_laser=False)
    await m.manejar({"accion": p.INYECTAR_FALLA, "falla": p.FALLA_CAUDAL})
    for _ in range(100):
        m.tick(0.05)
        if m.estado == EMERGENCIA:
            break
    assert m.estado == EMERGENCIA
    assert _tipos(eventos, p.EV_ALERTA)[0]["variable"] == "CAUDAL_REFRIGERANTE"
    assert _tipos(eventos, p.EV_EMERGENCIA)[0]["origen"] == "UMBRAL"


@pytest.mark.asyncio
async def test_sin_alta_tension_un_caudal_nulo_no_es_emergencia():
    """Es el estado normal de un equipo apagado."""
    m, eventos = _maquina()
    for _ in range(20):
        m.tick(0.5)
    assert m.estado == REPOSO
    assert eventos == []


@pytest.mark.asyncio
async def test_una_excursion_genera_una_sola_alerta():
    """Una alerta por evento, no una por ciclo del lazo."""
    m, eventos = _maquina()
    m.umbrales = [{"variable": "TENSION_AT", "valor_min": 0, "valor_max": 10, "accion": "ALERTA"}]
    await _armar(m, con_laser=False)
    for _ in range(50):
        m.tick(0.1)
    assert len(_tipos(eventos, p.EV_ALERTA)) == 1
    assert m.estado != EMERGENCIA


@pytest.mark.asyncio
async def test_un_umbral_de_bloqueo_impide_encender_el_laser():
    """BLOQUEAR_COMANDO no para la maquina: impide seguir."""
    m, _ = _maquina()
    m.umbrales = [
        {"variable": "TENSION_AT", "valor_min": 0, "valor_max": 10, "accion": "BLOQUEAR_COMANDO"}
    ]
    await _armar(m, con_laser=False)
    m.tick(0.1)
    assert m.bloqueado is True
    await m.manejar({"accion": p.ENCENDER_LASER})
    assert m.hal.laser is False


@pytest.mark.asyncio
async def test_el_rearme_es_explicito():
    """Despues de una emergencia el sistema no vuelve solo, y no se rearma sin una."""
    m, _ = _maquina()
    await m.manejar({"accion": p.REARMAR})
    assert "solo se rearma" in m.mensaje["texto"]
    await m.manejar({"accion": p.EMERGENCIA})
    m.tick(5)
    assert m.estado == EMERGENCIA
    await m.manejar({"accion": p.REARMAR})
    assert m.estado != EMERGENCIA


@pytest.mark.asyncio
async def test_apagar_deja_el_equipo_en_reposo():
    """Orden inverso al armado, refrigeracion incluida."""
    m, _ = _maquina()
    await _armar(m)
    await m.manejar({"accion": p.APAGAR})
    m.tick(0.1)
    assert m.estado == REPOSO


@pytest.mark.asyncio
async def test_el_motor_respeta_su_carrera():
    """Fuera de los 300 mm del desplazador, el comando se rechaza."""
    m, _ = _maquina()
    await m.manejar({"accion": p.MOVER_MOTOR, "posicion_mm": 500})
    assert m.mensaje["nivel"] == "error"
    await m.manejar({"accion": p.MOVER_MOTOR, "posicion_mm": 12.5})
    m.tick(10)
    assert m.hal.posicion_mm == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_un_comando_desconocido_no_rompe_nada():
    """Se avisa y se sigue."""
    m, _ = _maquina()
    await m.manejar({"accion": "despegar"})
    assert "desconocido" in m.mensaje["texto"]
    await m.manejar({"accion": p.INYECTAR_FALLA, "falla": "meteorito"})
    assert m.hal.falla == p.FALLA_NINGUNA


def test_la_telemetria_lleva_todo_lo_que_usa_el_front():
    """El contrato del mensaje que publica el controlador."""
    m, _ = _maquina()
    t = m.telemetria(1.0)
    assert set(t) >= {
        "estado",
        "condiciones",
        "variables",
        "senales",
        "actuadores",
        "extra",
        "ejecucion",
        "mensaje",
    }
    assert len(t["variables"]) == 7
    assert json.dumps(t)  # serializable


@pytest.mark.django_db
@pytest.mark.parametrize(
    "tipo,base,incremento,factor",
    [
        ("CONSTANTE", 550.0, None, None),
        ("LINEAL", 500.0, 12.5, None),
        ("EXPONENCIAL", 500.0, None, 1.02),
    ],
)
def test_el_controlador_calcula_el_periodo_igual_que_django(tipo, base, incremento, factor):
    """La formula esta duplicada (el controlador no importa Django): tienen que coincidir."""
    from programas.models import Periodo

    periodo = Periodo(tipo=tipo, periodo_base=base, incremento=incremento, factor=factor)
    dic = {"tipo": tipo, "base_um": base, "incremento_um": incremento, "factor": factor}
    for n in range(20):
        assert distancia_de_marca(dic, n) == pytest.approx(periodo.calcular_distancia(n))


# ── Cableado con Redis (main.py) ─────────────────────────────────────────────


class RedisFalso:
    """Lo minimo de redis.asyncio que usa el controlador, en memoria."""

    def __init__(self):
        self.claves, self.publicados, self.streams = {}, [], {}
        self.lecturas = []

    async def publish(self, canal, mensaje):
        self.publicados.append((canal, mensaje))

    async def setex(self, clave, ttl, valor):
        self.claves[clave] = valor

    async def get(self, clave):
        return self.claves.get(clave)

    async def xadd(self, stream, campos, **kw):
        self.streams.setdefault(stream, []).append(campos)

    async def xread(self, streams, block=None, count=None):
        self.lecturas.append(dict(streams))
        if self.streams.get("__entrada__"):
            return [(p.STREAM_COMANDOS, self.streams.pop("__entrada__"))]
        await asyncio.sleep(0.01)
        return []


async def _correr(corrutina, segundos=0.2):
    parar = asyncio.Event()
    tarea = asyncio.create_task(corrutina(parar))
    await asyncio.sleep(segundos)
    parar.set()
    await asyncio.wait_for(tarea, timeout=3)


@pytest.mark.asyncio
async def test_los_eventos_van_al_stream():
    """Lo que emite la maquina termina en co2:eventos, con su tipo."""
    r, cola = RedisFalso(), asyncio.Queue()
    cola.put_nowait((p.EV_MARCA, {"numero": 1}))
    await _correr(lambda parar: main.despachar_eventos(r, cola, parar))
    (evento,) = r.streams[p.STREAM_EVENTOS]
    assert evento["tipo"] == p.EV_MARCA
    assert json.loads(evento["datos"]) == {"numero": 1}


@pytest.mark.asyncio
async def test_la_telemetria_se_publica_y_queda_la_ultima():
    """Pub/sub para el front, y una clave con TTL para la API."""
    r = RedisFalso()
    m, _ = _maquina()
    await _correr(lambda parar: main.publicar_telemetria(r, m, parar))
    assert r.publicados and r.publicados[0][0] == p.CANAL_TELEMETRIA
    assert json.loads(r.claves[p.CLAVE_TELEMETRIA])["estado"] == REPOSO


@pytest.mark.asyncio
async def test_los_comandos_se_leen_solo_desde_el_arranque():
    """Arranca en "$": un controlador reiniciado no ejecuta ordenes viejas."""
    r = RedisFalso()
    m, _ = _maquina()
    r.streams["__entrada__"] = [
        (b"1-0", {b"datos": json.dumps({"accion": p.ENCENDER_REFRIGERACION}).encode()})
    ]
    await _correr(lambda parar: main.escuchar_comandos(r, m, parar))
    assert r.lecturas[0][p.STREAM_COMANDOS] == "$"
    assert r.lecturas[1][p.STREAM_COMANDOS] == b"1-0"  # sigue desde el ultimo leido
    assert m.hal.refrigeracion is True


@pytest.mark.asyncio
async def test_la_configuracion_publicada_reemplaza_los_umbrales_por_defecto():
    """Los umbrales inactivos no se aplican."""
    r = RedisFalso()
    m, _ = _maquina()
    r.claves[p.CLAVE_CONFIGURACION] = json.dumps(
        [
            {
                "variable": "TEMPERATURA_AGUA",
                "valor_min": 10,
                "valor_max": 30,
                "accion": "ALERTA",
                "activo": True,
            },
            {
                "variable": "CAUDAL_REFRIGERANTE",
                "valor_min": 1,
                "valor_max": 9,
                "accion": "ALERTA",
                "activo": False,
            },
        ]
    )
    await _correr(lambda parar: main.leer_configuracion(r, m, parar), segundos=0.1)
    assert [u["variable"] for u in m.umbrales] == ["TEMPERATURA_AGUA"]
