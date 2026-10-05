"""Pruebas del lado Django del circuito: API de control, eventos y WebSocket.

El controlador y Redis se reemplazan por un bus falso en memoria: lo que se
verifica es que la API valide y encole lo correcto, y que los eventos del
controlador terminen bien guardados.
"""

import json

import pytest
from asgiref.sync import async_to_sync
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from comun.enums import EstadoEjecucion, EstadoRed, ModoEjecucion
from controller import protocolo as p
from fabricacion.models import Marca
from operacion import control
from operacion.consumers import autenticar
from operacion.management.commands.escuchar_controlador import procesar
from operacion.models import Alerta, EventoEmergencia
from trazabilidad.models import RegistroDeFabricacion
from usuarios.models import RegistroAuditoria


class BusFalso:
    """Lo minimo de redis.Redis que usa ``operacion.control``."""

    def __init__(self):
        self.claves, self.comandos = {}, []

    def get(self, clave):
        valor = self.claves.get(clave)
        return valor.encode() if isinstance(valor, str) else valor

    def set(self, clave, valor):
        self.claves[clave] = valor

    def xadd(self, stream, campos, **kw):
        assert stream == p.STREAM_COMANDOS
        self.comandos.append(json.loads(campos["datos"]))


def _telemetria(estado="LISTO", laser=True, bloqueado=False):
    return json.dumps(
        {"estado": estado, "actuadores": {"laser": laser}, "bloqueado": bloqueado, "variables": {}}
    )


@pytest.fixture
def bus(monkeypatch):
    """Bus falso con un controlador vivo, LISTO y con el laser encendido."""
    falso = BusFalso()
    falso.claves[p.CLAVE_TELEMETRIA] = _telemetria()
    monkeypatch.setattr(control, "bus", lambda timeout=2: falso)
    monkeypatch.setattr(control, "controlador_vivo", lambda: True)
    return falso


@pytest.fixture
def operador(usuario):
    """Cliente autenticado como el operador de las fixtures."""
    c = APIClient()
    c.force_authenticate(usuario)
    return c


# ── Comandos ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_un_comando_se_encola_con_quien_y_cuando(operador, bus):
    """El controlador mide RNF001 desde t_envio: tiene que viajar en el comando."""
    r = operador.post("/api/control/comandos/", {"accion": p.HABILITAR_AT}, format="json")

    assert r.status_code == 202
    (cmd,) = bus.comandos
    assert cmd["accion"] == p.HABILITAR_AT
    assert cmd["usuario"] == "mcurie"
    assert cmd["t_envio"] > 0


@pytest.mark.django_db
def test_los_comandos_quedan_auditados_y_los_que_energizan_son_criticos(operador, bus):
    """RN010: habilitar la alta tension se audita como critico."""
    operador.post("/api/control/comandos/", {"accion": p.HABILITAR_AT}, format="json")
    operador.post("/api/control/comandos/", {"accion": p.ALINEAR_FIBRA}, format="json")

    asientos = RegistroAuditoria.objects.filter(accion="COMANDAR").order_by("pk")
    assert [(a.codigo_entidad, a.critica) for a in asientos] == [
        (p.HABILITAR_AT, True),
        (p.ALINEAR_FIBRA, False),
    ]


@pytest.mark.django_db
def test_sin_controlador_los_comandos_se_bloquean(operador, bus, monkeypatch):
    """Un comando a un controlador caido se perderia: se rechaza en el momento."""
    monkeypatch.setattr(control, "controlador_vivo", lambda: False)
    r = operador.post("/api/control/comandos/", {"accion": p.EMERGENCIA}, format="json")
    assert r.status_code == 503
    assert bus.comandos == []


@pytest.mark.django_db
def test_una_accion_desconocida_se_rechaza(operador, bus):
    """La API no reenvia cualquier cosa al hardware."""
    r = operador.post("/api/control/comandos/", {"accion": "formatear_disco"}, format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_las_fallas_solo_se_inyectan_con_el_hal_simulado(operador, bus, settings):
    """Con hardware real esa puerta queda cerrada."""
    settings.CONTROLLER_HAL = "gpio"
    r = operador.post(
        "/api/control/comandos/", {"accion": p.INYECTAR_FALLA, "falla": "caudal"}, format="json"
    )
    assert r.status_code == 409

    settings.CONTROLLER_HAL = "simulado"
    r = operador.post(
        "/api/control/comandos/", {"accion": p.INYECTAR_FALLA, "falla": "caudal"}, format="json"
    )
    assert r.status_code == 202
    assert bus.comandos[-1]["falla"] == "caudal"


@pytest.mark.django_db
def test_mover_el_motor_exige_la_posicion(operador, bus):
    """Sin destino no hay movimiento."""
    assert (
        operador.post(
            "/api/control/comandos/", {"accion": p.MOVER_MOTOR}, format="json"
        ).status_code
        == 400
    )
    r = operador.post(
        "/api/control/comandos/", {"accion": p.MOVER_MOTOR, "posicion_mm": 4.5}, format="json"
    )
    assert r.status_code == 202
    assert bus.comandos[-1]["posicion_mm"] == 4.5


@pytest.mark.django_db
def test_el_rearme_deja_asentado_quien_lo_hizo(operador, bus, usuario):
    """La emergencia la registro el sistema; el rearme lo hace una persona."""
    evento = EventoEmergencia.objects.create(origen="UMBRAL", tiempo_respuesta_ms=60)
    operador.post("/api/control/comandos/", {"accion": p.REARMAR}, format="json")
    evento.refresh_from_db()
    assert evento.rearmado_por == usuario


@pytest.mark.django_db
def test_el_estado_devuelve_la_ultima_telemetria(operador, bus):
    """Y 503 si vencio: un estado viejo no se muestra como actual."""
    assert operador.get("/api/control/estado/").data["estado"] == "LISTO"
    del bus.claves[p.CLAVE_TELEMETRIA]
    assert operador.get("/api/control/estado/").status_code == 503


# ── Iniciar un programa ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_iniciar_crea_el_ensayo_y_la_red_y_manda_el_programa(
    operador, bus, programa, lote, configuracion
):
    """El registro existe antes de que el controlador empiece a emitir marcas."""
    programa.validar()
    configuracion.activar()

    r = operador.post("/api/control/iniciar/", {"programa": "PRG-001"}, format="json")

    assert r.status_code == 201, r.data
    registro = RegistroDeFabricacion.objects.get(codigo=r.data["registro"])
    assert registro.estado == EstadoEjecucion.EN_CURSO
    assert registro.red.lote == lote
    assert registro.configuracion == configuracion
    (cmd,) = bus.comandos
    assert cmd["accion"] == p.INICIAR
    assert cmd["registro"] == registro.codigo
    assert cmd["programa"]["periodo"] == {
        "tipo": "CONSTANTE",
        "base_um": 500.0,
        "incremento_um": None,
        "factor": None,
    }
    assert cmd["programa"]["pulsos"] == 40


@pytest.mark.django_db
def test_en_modo_prueba_no_se_crea_red(operador, bus, programa):
    """Restriccion del diagrama: {modo = REAL => 1 Red}."""
    programa.validar()
    r = operador.post(
        "/api/control/iniciar/", {"programa": "PRG-001", "modo": "PRUEBA"}, format="json"
    )
    assert r.data["red"] is None
    assert RegistroDeFabricacion.objects.get(codigo=r.data["registro"]).modo == ModoEjecucion.PRUEBA


@pytest.mark.django_db
def test_los_codigos_de_ensayo_son_correlativos(operador, bus, programa):
    """ENS-AAAA-00001, ENS-AAAA-00002..."""
    programa.validar()
    a = operador.post("/api/control/iniciar/", {"programa": "PRG-001"}, format="json").data[
        "registro"
    ]
    b = operador.post("/api/control/iniciar/", {"programa": "PRG-001"}, format="json").data[
        "registro"
    ]
    assert int(b.rsplit("-", 1)[1]) == int(a.rsplit("-", 1)[1]) + 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "telemetria",
    [_telemetria(estado="PREPARANDO"), _telemetria(laser=False), _telemetria(bloqueado=True)],
)
def test_no_se_inicia_si_el_sistema_no_esta_listo(operador, bus, programa, telemetria):
    """Si arrancara, el controlador lo rechazaria y quedaria un registro huerfano."""
    programa.validar()
    bus.claves[p.CLAVE_TELEMETRIA] = telemetria
    r = operador.post("/api/control/iniciar/", {"programa": "PRG-001"}, format="json")
    assert r.status_code == 409
    assert not RegistroDeFabricacion.objects.exists()


@pytest.mark.django_db
def test_no_se_inicia_un_programa_sin_validar(operador, bus, programa):
    """Un programa sin validar puede tener huecos que paren la maquina a la mitad."""
    r = operador.post("/api/control/iniciar/", {"programa": "PRG-001"}, format="json")
    assert r.status_code == 400


# ── Servicio de eventos ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_las_marcas_se_guardan_y_una_entrega_repetida_no_duplica(registro):
    """El grupo de consumo puede reentregar un evento tras un reinicio."""
    datos = {
        "registro": registro.codigo,
        "numero": 1,
        "posicion_um": 500.0,
        "cant_pulsos": 1,
        "ciclo_trabajo": 59.3,
        "tiempo_de_pulso": 120,
    }
    procesar(p.EV_MARCA, datos)
    procesar(p.EV_MARCA, datos)
    assert Marca.objects.filter(red=registro.red).count() == 1


@pytest.mark.django_db
def test_los_checkpoints_guardan_las_lecturas(registro):
    """Es la trazabilidad que reemplaza a la telemetria persistida (ADR-0007)."""
    procesar(
        p.EV_CHECKPOINT,
        {
            "registro": registro.codigo,
            "tipo": "MARCA",
            "estado_sistema": "GRABANDO",
            "valores": {"TEMPERATURA_AGUA": 20.4},
        },
    )
    assert registro.checkpoints.get().valores == {"TEMPERATURA_AGUA": 20.4}


@pytest.mark.django_db
def test_una_alerta_del_controlador_usa_el_umbral_vigente(configuracion):
    """La alerta trae la sugerencia de que revisar."""
    configuracion.activar()
    procesar(p.EV_ALERTA, {"variable": "TEMPERATURA_AGUA", "valor": 29.1})
    alerta = Alerta.objects.get()
    assert alerta.umbral.variable == "TEMPERATURA_AGUA"
    assert "chiller" in alerta.sugerencia


@pytest.mark.django_db
def test_una_alerta_sin_umbral_vigente_se_asienta_igual():
    """El controlador uso sus umbrales por defecto: la alerta no se pierde."""
    procesar(p.EV_ALERTA, {"variable": "CAUDAL_REFRIGERANTE", "valor": 0.8})
    assert Alerta.objects.get().valor_medido == 0.8


@pytest.mark.django_db
def test_una_emergencia_interrumpe_el_ensayo(registro):
    """La red grabada a medias no es viable."""
    procesar(
        p.EV_EMERGENCIA,
        {
            "origen": "UMBRAL",
            "descripcion": "Caudal bajo",
            "tiempo_respuesta_ms": 55,
            "registro": registro.codigo,
        },
    )
    registro.refresh_from_db()
    registro.red.refresh_from_db()
    evento = EventoEmergencia.objects.get()
    assert (evento.registro, evento.tiempo_respuesta_ms, evento.cumple_rnf001) == (
        registro,
        55,
        True,
    )
    assert registro.estado == EstadoEjecucion.INTERRUMPIDO
    assert registro.red.estado == EstadoRed.INVIABLE


def _espectro(profundidad):
    lambdas = [1500.0 + i for i in range(200)]
    return {
        "longitudes_onda": lambdas,
        "transmitancias": [-2.0 - profundidad / (1 + ((nm - 1600) / 4) ** 2) for nm in lambdas],
    }


@pytest.mark.django_db
def test_el_fin_cierra_el_ensayo_y_caracteriza_la_red(registro):
    """Con una resonancia profunda, la red es viable."""
    procesar(p.EV_FIN, {"registro": registro.codigo, "marcas": 18, "espectro": _espectro(12.0)})
    registro.refresh_from_db()
    registro.red.refresh_from_db()
    assert registro.estado == EstadoEjecucion.COMPLETADO
    assert registro.red.espectro.cantidad_de_puntos == 200
    assert registro.red.resonancia_principal().longitud_onda == 1600.0
    assert registro.red.estado == EstadoRed.VIABLE


@pytest.mark.django_db
def test_una_resonancia_poco_profunda_da_una_red_inviable(registro):
    """El criterio: al menos 5 dB debajo de la linea de base."""
    procesar(p.EV_FIN, {"registro": registro.codigo, "marcas": 3, "espectro": _espectro(3.5)})
    registro.red.refresh_from_db()
    assert registro.red.estado == EstadoRed.INVIABLE


@pytest.mark.django_db
def test_un_ensayo_ya_cerrado_no_se_reabre(registro):
    """Un fin que llega tarde despues de una emergencia no pisa el estado."""
    registro.interrumpir("Emergencia")
    procesar(p.EV_FIN, {"registro": registro.codigo, "marcas": 18, "espectro": _espectro(12.0)})
    registro.refresh_from_db()
    assert registro.estado == EstadoEjecucion.INTERRUMPIDO


@pytest.mark.django_db
def test_abortar_deja_el_motivo(registro):
    """Abortado no es interrumpido: fue una decision, no una falla."""
    procesar(
        p.EV_ABORTADO, {"registro": registro.codigo, "marcas": 0, "motivo": "Rechazado: no LISTO"}
    )
    registro.refresh_from_db()
    assert registro.estado == EstadoEjecucion.ABORTADO
    assert "Rechazado" in registro.observaciones


@pytest.mark.django_db
def test_un_evento_de_un_registro_inexistente_se_ignora():
    """No rompe el servicio: se registra y se sigue con el siguiente."""
    procesar(p.EV_MARCA, {"registro": "NO-EXISTE", "numero": 1})
    procesar("evento_raro", {})
    assert not Marca.objects.exists()


@pytest.mark.django_db
def test_la_configuracion_vigente_se_publica_para_el_controlador(bus, configuracion):
    """El controlador lee los umbrales de Redis, no de MySQL (ADR-0002)."""
    configuracion.activar()
    assert control.publicar_configuracion() == 1
    (umbral,) = json.loads(bus.claves[p.CLAVE_CONFIGURACION])
    assert umbral["variable"] == "TEMPERATURA_AGUA"
    assert umbral["accion"] == "PARADA_EMERGENCIA"


# ── Autenticacion del WebSocket ──────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_el_websocket_acepta_un_token_valido(usuario):
    """El JWT viaja en la query string: el navegador no manda headers en el handshake."""
    token = str(AccessToken.for_user(usuario))
    assert async_to_sync(autenticar)(f"token={token}".encode()) == usuario.pk


@pytest.mark.django_db(transaction=True)
def test_el_websocket_rechaza_sin_token_o_con_uno_invalido(usuario):
    """Sin sesion valida no hay telemetria."""
    assert async_to_sync(autenticar)(b"") is None
    assert async_to_sync(autenticar)(b"token=no-es-un-jwt") is None


@pytest.mark.django_db(transaction=True)
def test_el_websocket_rechaza_a_un_usuario_dado_de_baja(usuario):
    """Un token emitido antes de la baja no sirve despues."""
    token = str(AccessToken.for_user(usuario))
    usuario.dar_de_baja()
    assert async_to_sync(autenticar)(f"token={token}".encode()) is None
