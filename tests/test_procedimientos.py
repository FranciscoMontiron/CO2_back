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


def test_paso_fuente_no_ofrece_habilitar_at():
    """La alta tensión se habilita solo con el botón físico del laboratorio."""
    paso = next(p for p in PASOS_ESTANDAR if p["titulo"] == "Fuente de alta tensión")
    assert all(a.get("accion") != "habilitar_at" for a in paso.get("acciones", []))
    validaciones = {v["texto"]: v for v in paso["validaciones"]}
    assert validaciones["Interlock físico cerrado"]["tipo"] == TipoValidacion.MANUAL
    hv = validaciones["Fuente HV habilitada"]
    assert hv["tipo"] == TipoValidacion.SENSOR
    assert hv["condicion"] == {"fuente": "variables.TENSION_AT", "op": ">=", "valor": 15}
    assert paso["condicion_avance"] == hv["condicion"]
    habilitada = next(i for i in paso["indicadores"] if i["nombre"] == "Fuente HV")["estados"][0]
    assert habilitada["cuando"] == hv["condicion"]


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


def test_asignar_procedimiento_a_un_programa(cliente, investigador, programa_arbol):
    """El programa expone y acepta el codigo de su procedimiento."""
    programa = programa_arbol
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


def test_no_se_borra_un_procedimiento_en_uso(cliente, investigador, programa_arbol):
    """Borrar un procedimiento que usa un programa dejaria al operador sin guia."""
    programa = programa_arbol
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
        ValidacionPaso.objects.filter(paso__procedimiento=primero).exclude(detalle="").count() == 26
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


MIGRACION_0005 = "procedimientos.migrations.0005_fuente_at_sin_boton_software"


def test_migracion_0005_corrige_una_base_con_el_paso_viejo(db):
    """Una base que ya tenia PROC-001 con el boton de software queda como la semilla nueva."""
    import importlib

    from django.apps import apps

    migracion = importlib.import_module(MIGRACION_0005)
    proc, _ = cargar_procedimiento_estandar()
    paso = proc.pasos.get(titulo="Fuente de alta tensión")
    # Deja el paso como lo dejaba la semilla anterior.
    paso.acciones = [migracion.BOTON]
    paso.condicion_avance = migracion.ENCENDIDA_SW
    paso.indicadores[1]["nombre"] = "Fuente HV (SW)"
    paso.indicadores[1]["estados"][0]["cuando"] = migracion.ENCENDIDA_SW
    paso.save()
    paso.validaciones.filter(texto=migracion.TEXTO_NUEVO).update(
        texto=migracion.TEXTO_VIEJO,
        detalle=migracion.DETALLE_VIEJO,
        condicion=migracion.ENCENDIDA_SW,
    )
    version = proc.version

    migracion.sin_boton(apps, None)

    proc.refresh_from_db()
    paso.refresh_from_db()
    assert proc.version == version + 1
    assert paso.acciones == []
    assert paso.condicion_avance == migracion.TENSION
    assert paso.indicadores[1]["nombre"] == "Fuente HV"
    assert paso.indicadores[1]["estados"][0]["cuando"] == migracion.TENSION
    hv = paso.validaciones.get(texto=migracion.TEXTO_NUEVO)
    assert hv.condicion == migracion.TENSION
    # Una segunda pasada no vuelve a tocar nada.
    migracion.sin_boton(apps, None)
    proc.refresh_from_db()
    assert proc.version == version + 1


def test_migracion_0005_sin_procedimiento_no_hace_nada(db):
    """Sin PROC-001 la migracion no falla."""
    import importlib

    from django.apps import apps

    migracion = importlib.import_module(MIGRACION_0005)
    migracion.sin_boton(apps, None)


MIGRACION_0006 = "procedimientos.migrations.0006_sin_criterio_de_fin"


def test_semilla_sin_criterio_de_fin(db):
    """La semilla nueva no exige un criterio de fin que el programa ya no tiene."""
    proc, _ = cargar_procedimiento_estandar()
    paso = proc.pasos.get(titulo="Parámetros base")
    assert not paso.validaciones.filter(texto="Criterio de finalización definido").exists()
    assert [v.orden for v in paso.validaciones.order_by("orden")] == [1, 2]
    assert paso.indicadores[1]["nombre"] == "Resumen del programa"


def test_migracion_0006_corrige_una_base_con_el_criterio_de_fin(db):
    """Una base con PROC-001 viejo queda igual que la semilla nueva, una sola vez."""
    import importlib

    from django.apps import apps

    migracion = importlib.import_module(MIGRACION_0006)
    proc, _ = cargar_procedimiento_estandar()
    paso = proc.pasos.get(titulo="Parámetros base")
    paso.indicadores[1]["nombre"] = migracion.INDICADOR_VIEJO
    paso.indicadores[1]["estados"][0]["texto"] = migracion.TEXTO_VIEJO
    paso.save()
    paso.validaciones.filter(texto__startswith="Parámetros dentro").update(
        detalle=migracion.DETALLE_VIEJO
    )
    paso.validaciones.create(
        orden=3,
        texto=migracion.VALIDACION_VIEJA,
        detalle="x",
        tipo="SENSOR",
        condicion={"fuente": "programa.tiene_criterio_fin", "op": "es_verdadero"},
    )
    version = proc.version

    migracion.sin_criterio(apps, None)

    proc.refresh_from_db()
    paso.refresh_from_db()
    assert proc.version == version + 1
    assert not paso.validaciones.filter(texto=migracion.VALIDACION_VIEJA).exists()
    assert paso.indicadores[1]["nombre"] == migracion.INDICADOR_NUEVO
    assert paso.indicadores[1]["estados"][0]["texto"] == migracion.TEXTO_NUEVO
    assert paso.validaciones.filter(detalle=migracion.DETALLE_NUEVO).exists()
    migracion.sin_criterio(apps, None)
    proc.refresh_from_db()
    assert proc.version == version + 1


def test_migracion_0006_sin_procedimiento_no_hace_nada(db):
    """Sin PROC-001 la migracion no falla."""
    import importlib

    from django.apps import apps

    importlib.import_module(MIGRACION_0006).sin_criterio(apps, None)


MIGRACION_0007 = "procedimientos.migrations.0007_progreso_por_acciones"


def _paso_grabado(proc):
    return proc.pasos.get(titulo="Loop de grabado")


def test_semilla_lee_el_progreso_que_publica_el_controlador(db):
    """El controlador publica ``pulsos`` hechos; ``pulso`` y ``distancia_mm`` ya no existen."""
    from procedimientos.catalogo import FUENTES

    proc, _ = cargar_procedimiento_estandar()
    paso = _paso_grabado(proc)
    (progreso,) = [i for i in paso.indicadores if i["nombre"] == "Progreso"]
    assert progreso["estados"][0]["texto"] == "{ejecucion.pulsos} / {programa.pulsos} pulsos"
    primer = paso.validaciones.get(texto="Primer pulso ejecutado correctamente")
    assert primer.condicion["alguna"][0] == {
        "fuente": "ejecucion.pulsos",
        "op": ">=",
        "valor": 1,
    }
    assert paso.validaciones.filter(texto="Programa completado").exists()
    for fuente in ("ejecucion.acciones", "ejecucion.total", "ejecucion.pulsos"):
        assert fuente in FUENTES
    assert "ejecucion.ubicacion" in FUENTES
    assert "ejecucion.ultimo_pulso_ms" in FUENTES
    assert "ejecucion.pulso" not in FUENTES
    assert "ejecucion.distancia_mm" not in FUENTES


def test_migracion_0007_corrige_una_base_con_el_progreso_viejo(db):
    """Una base con PROC-001 viejo pasa a leer ``ejecucion.pulsos``, una sola vez."""
    import importlib

    from django.apps import apps

    migracion = importlib.import_module(MIGRACION_0007)
    proc, _ = cargar_procedimiento_estandar()
    paso = _paso_grabado(proc)
    (progreso,) = [i for i in paso.indicadores if i["nombre"] == "Progreso"]
    progreso["estados"][0]["texto"] = migracion.INDICADOR_VIEJO
    paso.save()
    paso.validaciones.filter(texto=migracion.PRIMER_PULSO).update(
        detalle=migracion.DETALLE_PULSO_VIEJO, condicion=migracion.CONDICION_VIEJA
    )
    paso.validaciones.filter(texto=migracion.FIN_NUEVO).update(
        texto=migracion.FIN_VIEJO, detalle=migracion.DETALLE_FIN_VIEJO
    )
    version = proc.version

    migracion.progreso_por_acciones(apps, None)

    proc.refresh_from_db()
    paso.refresh_from_db()
    assert proc.version == version + 1
    (progreso,) = [i for i in paso.indicadores if i["nombre"] == "Progreso"]
    assert progreso["estados"][0]["texto"] == migracion.INDICADOR_NUEVO
    primer = paso.validaciones.get(texto=migracion.PRIMER_PULSO)
    assert primer.condicion == migracion.CONDICION_NUEVA
    assert primer.detalle == migracion.DETALLE_PULSO_NUEVO
    fin = paso.validaciones.get(texto=migracion.FIN_NUEVO)
    assert fin.detalle == migracion.DETALLE_FIN_NUEVO
    # Una segunda pasada no vuelve a tocar nada.
    migracion.progreso_por_acciones(apps, None)
    proc.refresh_from_db()
    assert proc.version == version + 1


def test_migracion_0007_no_pisa_un_texto_editado(db):
    """Si el laboratorio editó el detalle, la migración no lo pisa."""
    import importlib

    from django.apps import apps

    migracion = importlib.import_module(MIGRACION_0007)
    proc, _ = cargar_procedimiento_estandar()
    paso = _paso_grabado(proc)
    paso.validaciones.filter(texto=migracion.PRIMER_PULSO).update(
        detalle="Texto propio del laboratorio", condicion=migracion.CONDICION_VIEJA
    )
    migracion.progreso_por_acciones(apps, None)
    primer = paso.validaciones.get(texto=migracion.PRIMER_PULSO)
    assert primer.detalle == "Texto propio del laboratorio"
    assert primer.condicion == migracion.CONDICION_NUEVA


def test_migracion_0007_sin_procedimiento_no_hace_nada(db):
    """Sin PROC-001 la migracion no falla."""
    import importlib

    from django.apps import apps

    importlib.import_module(MIGRACION_0007).progreso_por_acciones(apps, None)


def test_el_catalogo_no_ofrece_habilitar_at():
    """Spec 5.8: un procedimiento editado no puede volver a poner el botón de software (m-3)."""
    from controller import protocolo as p
    from procedimientos.catalogo import ACCIONES, validar_accion

    assert p.HABILITAR_AT not in ACCIONES
    errores, _ = validar_accion({"etiqueta": "HV", "accion": p.HABILITAR_AT}, "a")
    assert errores
