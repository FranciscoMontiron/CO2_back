"""Pruebas de los procedimientos guiados.

Cubren el vocabulario cerrado (que no se pueda guardar una condicion o un
comando fuera del catalogo), la API anidada, la asignacion a programas y la
semilla del procedimiento estandar.
"""

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from comun.enums import TipoRol, TipoValidacion
from procedimientos.catalogo import validar_accion, validar_condicion, validar_procedimiento
from procedimientos.models import ProcedimientoGuiado, ValidacionPaso
from procedimientos.semilla import CODIGO_ESTANDAR, PASOS_ESTANDAR, cargar_procedimiento_estandar
from programas.models import Programa
from usuarios.models import RegistroAuditoria, Rol, Usuario

URL = "/api/procedimientos/"


def _usuario(codigo, username):
    """Crea un usuario con el rol indicado."""
    rol, _ = Rol.objects.get_or_create(codigo=codigo, defaults={"name": codigo.title()})
    u = Usuario(username=username, rol=rol)
    u.set_password(username)
    u.save()
    return u


@pytest.fixture
def cliente():
    """Cliente HTTP de DRF sin autenticar."""
    return APIClient()


@pytest.fixture
def investigador(db):
    """Usuario con rol de investigador."""
    return _usuario(TipoRol.INVESTIGADOR, "investigador")


@pytest.fixture
def operador(db):
    """Usuario con rol de operador."""
    return _usuario(TipoRol.OPERADOR, "operador")


def _hlpg(**cambios):
    """Un procedimiento al estilo del PEHLPG del CIOp: manual y con bifurcaciones."""
    datos = {
        "nombre": "Fabricación de PHLPG por arco",
        "tipo_red": "pHLPG",
        "pasos": [
            {
                "titulo": "Encendido y preparación",
                "fase": "Preparación",
                "validaciones": [
                    {
                        "texto": "OSA encendido 1 hora antes",
                        "detalle": "Encender el OSA 1 hora antes de comenzar la fabricación.",
                    },
                    {
                        "texto": "Control de temperatura en 12,5 K",
                        "detalle": "PRESET en 12,5 K, OUTPUT, DISPLAY en SENSOR mostrando 12,5 K.",
                    },
                ],
            },
            {
                "titulo": "Fabricación",
                "fase": "Fabricación",
                "validaciones": [{"texto": "Programa HLPG MARCA N°46 corrido"}],
                "salidas": [
                    {"etiqueta": "Fabricar otra", "destino": 3},
                    {"etiqueta": "Apagar", "destino": 4},
                ],
            },
            {
                "titulo": "Cambio de fibra",
                "fase": "Fabricación",
                "validaciones": [{"texto": "Motor en HOME"}],
                "salidas": [{"etiqueta": "Volver a fabricación", "destino": 2}],
            },
            {
                "titulo": "Apagado",
                "fase": "Apagado",
                "validaciones": [{"texto": "Fuente SLD apagada"}],
            },
        ],
    }
    datos.update(cambios)
    return datos


# ── Catalogo ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "condicion",
    [
        {"fuente": "actuadores.laser", "op": "es_verdadero"},
        {"fuente": "variables.CAUDAL_REFRIGERANTE", "op": ">=", "valor": 2},
        {"fuente": "estado", "op": "==", "valor": "LISTO"},
        {"todas": [{"fuente": "actuadores.laser", "op": "es_falso"}]},
        {"alguna": [{"fuente": "ensayo.terminado", "op": "es_verdadero"}]},
    ],
)
def test_condiciones_validas(condicion):
    """Las condiciones bien formadas pasan."""
    assert validar_condicion(condicion, "c") == []


@pytest.mark.parametrize(
    ("condicion", "fragmento"),
    [
        ({"fuente": "os.system", "op": "es_verdadero"}, "no existe"),
        ({"fuente": "actuadores.laser", "op": ">"}, "admite los operadores"),
        ({"fuente": "variables.TENSION_AT", "op": ">", "valor": "alto"}, "numerica"),
        ({"fuente": "variables.TENSION_AT", "op": ">"}, "necesita un valor"),
        ({"fuente": "actuadores.laser", "op": "es_falso", "valor": True}, "no lleva valor"),
        ({"todas": []}, "lista no vacia"),
        ("actuadores.laser", "objeto"),
    ],
)
def test_condiciones_invalidas(condicion, fragmento):
    """Una condicion fuera del catalogo se rechaza con un mensaje que dice por que."""
    errores = validar_condicion(condicion, "c")
    assert errores and fragmento in errores[0]


def test_grabar_sin_apagado_posterior_no_valida():
    """Si un paso inicia un grabado real, uno posterior tiene que apagar el equipo."""
    pasos = [
        {
            "validaciones": [{"texto": "a"}],
            "acciones": [{"etiqueta": "Apagar", "accion": "apagar"}],
        },
        {
            "validaciones": [{"texto": "b"}],
            "acciones": [{"etiqueta": "Ir", "accion": "iniciar_real"}],
        },
    ]
    assert any("Apagar en orden inverso" in e for e in validar_procedimiento(pasos))


def test_marcador_desconocido_en_indicador():
    """Un chip no puede mostrar una fuente que no existe."""
    pasos = [
        {
            "validaciones": [{"texto": "a"}],
            "indicadores": [{"nombre": "X", "estados": [{"texto": "{nada.x} W", "tono": "ok"}]}],
        }
    ]
    assert any("marcador" in e for e in validar_procedimiento(pasos))


def test_procedimiento_estandar_cumple_el_catalogo():
    """La semilla pasa por las mismas reglas que un procedimiento del front."""
    assert validar_procedimiento(PASOS_ESTANDAR) == []


# ── API ─────────────────────────────────────────────────────────────────────


def test_crea_procedimiento_anidado_y_numera_por_posicion(cliente, investigador):
    """Pasos y validaciones se numeran por su posicion; el tipo por defecto es manual."""
    cliente.force_authenticate(investigador)
    r = cliente.post(URL, _hlpg(), format="json")
    assert r.status_code == 201, r.data
    assert r.data["codigo"] == "PROC-001"
    assert [p["orden"] for p in r.data["pasos"]] == [1, 2, 3, 4]
    primera = r.data["pasos"][0]["validaciones"][1]
    assert primera["orden"] == 2
    assert primera["tipo"] == TipoValidacion.MANUAL
    assert "12,5 K" in primera["detalle"]
    assert r.data["pasos"][1]["salidas"][0] == {"etiqueta": "Fabricar otra", "destino": 3}
    assert r.data["creado_por"] == str(investigador)


def test_salida_a_un_paso_inexistente(cliente, investigador):
    """Una bifurcacion tiene que apuntar a un paso del procedimiento."""
    cliente.force_authenticate(investigador)
    datos = _hlpg()
    datos["pasos"][1]["salidas"] = [{"etiqueta": "A ningun lado", "destino": 9}]
    r = cliente.post(URL, datos, format="json")
    assert r.status_code == 400
    assert "entre 1 y 4" in str(r.data["pasos"])


def test_validacion_por_sensor_sin_condicion(cliente, investigador):
    """Una validacion por sensor sin condicion no tendria como tildarse."""
    cliente.force_authenticate(investigador)
    datos = _hlpg()
    datos["pasos"][0]["validaciones"][0]["tipo"] = TipoValidacion.SENSOR
    r = cliente.post(URL, datos, format="json")
    assert r.status_code == 400
    assert "necesita su condicion" in str(r.data["pasos"])


def test_paso_sin_validaciones(cliente, investigador):
    """Cada paso necesita al menos una validacion."""
    cliente.force_authenticate(investigador)
    datos = _hlpg()
    datos["pasos"][3]["validaciones"] = []
    r = cliente.post(URL, datos, format="json")
    assert r.status_code == 400


def test_operador_lee_pero_no_escribe(cliente, operador):
    """El operador sigue los procedimientos pero no los disena."""
    cliente.force_authenticate(operador)
    assert cliente.get(URL).status_code == 200
    assert cliente.post(URL, _hlpg(), format="json").status_code == 403


def test_editar_reemplaza_pasos_y_sube_version(cliente, investigador):
    """Editar reemplaza la secuencia entera y deja asentada una version nueva."""
    cliente.force_authenticate(investigador)
    codigo = cliente.post(URL, _hlpg(), format="json").data["codigo"]
    datos = _hlpg(nombre="PHLPG v2")
    datos["pasos"] = datos["pasos"][:1]
    r = cliente.put(f"{URL}{codigo}/", datos, format="json")
    assert r.status_code == 200, r.data
    assert r.data["version"] == 2
    assert len(r.data["pasos"]) == 1
    assert ValidacionPaso.objects.filter(paso__procedimiento__codigo=codigo).count() == 2


def test_auditoria_de_procedimientos(cliente, investigador):
    """Crear un procedimiento queda en la bitacora (RN010)."""
    cliente.force_authenticate(investigador)
    cliente.post(URL, _hlpg(), format="json")
    assert RegistroAuditoria.objects.filter(entidad="procedimientos.ProcedimientoGuiado").exists()


def test_duplicar(cliente, investigador):
    """Duplicar copia pasos y validaciones, sin programas asignados."""
    cliente.force_authenticate(investigador)
    codigo = cliente.post(URL, _hlpg(), format="json").data["codigo"]
    r = cliente.post(f"{URL}{codigo}/duplicar/")
    assert r.status_code == 201, r.data
    assert r.data["codigo"] == "PROC-002"
    assert r.data["nombre"].endswith("(copia)")
    assert len(r.data["pasos"]) == 4
    assert r.data["programas"] == []


def test_catalogo(cliente, operador):
    """El editor recibe fuentes, operadores y comandos permitidos."""
    cliente.force_authenticate(operador)
    r = cliente.get(f"{URL}catalogo/")
    assert r.status_code == 200
    fuentes = {f["fuente"] for f in r.data["fuentes"]}
    assert {"estado", "actuadores.laser", "variables.TENSION_AT", "ensayo.terminado"} <= fuentes
    acciones = {a["accion"] for a in r.data["acciones"]}
    assert "iniciar_real" in acciones
    assert "emergencia" not in acciones and "inyectar_falla" not in acciones


# ── Asignacion a programas ──────────────────────────────────────────────────


def test_asignar_procedimiento_a_un_programa(cliente, investigador, programa):
    """El programa expone y acepta el codigo de su procedimiento."""
    cliente.force_authenticate(investigador)
    proc = cliente.post(URL, _hlpg(), format="json").data["codigo"]
    datos = cliente.get(f"/api/programas/{programa.codigo}/").data
    assert datos["procedimiento"] is None
    datos["procedimiento"] = proc
    r = cliente.put(f"/api/programas/{programa.codigo}/", datos, format="json")
    assert r.status_code == 200, r.data
    assert r.data["procedimiento"] == proc
    assert r.data["procedimiento_nombre"] == "Fabricación de PHLPG por arco"
    usados = cliente.get(f"{URL}{proc}/").data["programas"]
    assert usados == [{"codigo": programa.codigo, "nombre": programa.nombre}]


def test_no_se_borra_un_procedimiento_en_uso(cliente, investigador, programa):
    """Borrar un procedimiento que usa un programa dejaria al operador sin guia."""
    cliente.force_authenticate(investigador)
    proc = cliente.post(URL, _hlpg(), format="json").data["codigo"]
    Programa.objects.filter(pk=programa.pk).update(
        procedimiento=ProcedimientoGuiado.objects.get(codigo=proc)
    )
    r = cliente.delete(f"{URL}{proc}/")
    assert r.status_code == 400
    assert programa.codigo in str(r.data)
    Programa.objects.filter(pk=programa.pk).update(procedimiento=None)
    assert cliente.delete(f"{URL}{proc}/").status_code == 204


# ── Semilla ─────────────────────────────────────────────────────────────────


def test_semilla_es_idempotente(db):
    """Cargar dos veces no duplica el procedimiento estandar."""
    primero, creado = cargar_procedimiento_estandar()
    segundo, de_nuevo = cargar_procedimiento_estandar()
    assert creado and not de_nuevo
    assert primero.pk == segundo.pk
    assert primero.codigo == CODIGO_ESTANDAR
    assert primero.pasos.count() == 9
    assert (
        ValidacionPaso.objects.filter(paso__procedimiento=primero).exclude(detalle="").count() == 27
    )


def test_carga_inicial_asigna_el_estandar_a_a_b_y_c(db):
    """La carga de ejemplo deja a los programas A, B y C con el procedimiento estandar."""
    call_command("cargar_datos_iniciales", "--con-ejemplo")
    estandar = ProcedimientoGuiado.objects.get(codigo=CODIGO_ESTANDAR)
    nombres = set(estandar.programas.values_list("nombre", flat=True))
    assert nombres == {"Programa A", "Programa B", "Programa C"}


def test_carga_inicial_no_pisa_una_asignacion(db):
    """Si un programa ya tiene otro procedimiento, la carga lo respeta."""
    call_command("cargar_datos_iniciales", "--con-ejemplo")
    otro = ProcedimientoGuiado.objects.create(codigo="PROC-099", nombre="Otro")
    Programa.objects.filter(nombre="Programa B").update(procedimiento=otro)
    call_command("cargar_datos_iniciales", "--con-ejemplo")
    assert Programa.objects.get(nombre="Programa B").procedimiento == otro


def test_paso_1_estandar_se_tilda_con_el_controlador(db):
    """Interrogador y comunicacion se confirman solos con el controlador conectado."""
    proc, _ = cargar_procedimiento_estandar()
    validaciones = proc.pasos.get(orden=1).validaciones.order_by("orden")
    tipos = [(v.texto, v.tipo, v.condicion) for v in validaciones]
    conectado = {"fuente": "controlador.conectado", "op": "es_verdadero"}
    assert tipos[0][1] == TipoValidacion.MANUAL
    assert tipos[1][1:] == (TipoValidacion.SENSOR, conectado)
    assert tipos[2][1:] == (TipoValidacion.SENSOR, conectado)


def _interruptor(**cambios):
    """Interruptor de refrigeracion valido."""
    datos = {
        "tipo": "interruptor",
        "estado": {"fuente": "actuadores.refrigeracion", "op": "es_verdadero"},
        "encender": {"etiqueta": "Encender", "accion": "encender_refrigeracion"},
        "apagar": {"etiqueta": "Apagar", "accion": "apagar_refrigeracion"},
    }
    datos.update(cambios)
    return datos


def test_interruptor_valido():
    """Un interruptor declara los dos comandos que puede mandar."""
    errores, comandos = validar_accion(_interruptor(), "a")
    assert errores == []
    assert comandos == {"encender_refrigeracion", "apagar_refrigeracion"}


@pytest.mark.parametrize(
    ("cambios", "fragmento"),
    [
        ({"estado": None}, "necesita la condicion"),
        ({"apagar": {"etiqueta": "X", "accion": "emergencia"}}, "no esta permitido"),
        ({"encender": {"etiqueta": "Ir", "accion": "iniciar_real"}}, "iniciar un grabado"),
        ({"tipo": "perilla"}, "no existe"),
    ],
)
def test_interruptor_invalido(cambios, fragmento):
    """Un interruptor sin estado, con un comando prohibido o que inicie un grabado no pasa."""
    errores, _ = validar_accion(_interruptor(**cambios), "a")
    assert any(fragmento in e for e in errores), errores


def test_paso_2_estandar_usa_interruptor(db):
    """La refrigeracion del procedimiento estandar se enciende y apaga con un solo boton."""
    proc, _ = cargar_procedimiento_estandar()
    (accion,) = proc.pasos.get(orden=2).acciones
    assert accion["tipo"] == "interruptor"
    assert accion["apagar"]["accion"] == "apagar_refrigeracion"
