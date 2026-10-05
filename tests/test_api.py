"""Pruebas de la API REST que consume el front.

Verifican el contrato (que forma tiene cada respuesta) y las reglas que la API
hace cumplir: quien puede hacer que, y la traduccion entre la representacion de
laboratorio de un programa y el modelo.
"""

import datetime

import pytest
from rest_framework.test import APIClient

from comun.enums import CriterioFin, EstadoEjecucion, TipoRol
from operacion.models import Configuracion, Umbral
from programas.models import Programa
from trazabilidad.models import RegistroDeFabricacion
from usuarios.models import RegistroAuditoria, Rol, Usuario


def _usuario_con_rol(codigo, username):
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
def como(cliente):
    """Devuelve una funcion que autentica al cliente como un usuario dado."""

    def _como(usuario):
        cliente.force_authenticate(usuario)
        return cliente

    return _como


@pytest.fixture
def investigador(db):
    """Usuario con rol de investigador."""
    return _usuario_con_rol(TipoRol.INVESTIGADOR, "investigador")


@pytest.fixture
def administrador(db):
    """Usuario con rol de administrador."""
    return _usuario_con_rol(TipoRol.ADMINISTRADOR, "administrador")


def _datos_programa(**cambios):
    """Datos validos de un programa en representacion de laboratorio."""
    datos = {
        "nombre": "Programa de prueba",
        "potencia_objetivo_mw": 10.0,
        "duracion_pulso_ms": 400,
        "desplazamiento_um": 550.0,
        "criterio_fin": CriterioFin.LONGITUD,
        "distancia_mm": 10.0,
    }
    datos.update(cambios)
    return datos


# ── Sesion ───────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_el_login_devuelve_tokens_y_quien_entro(cliente, usuario):
    """El front necesita saber el rol sin una segunda peticion."""
    r = cliente.post(
        "/api/auth/token/", {"username": "mcurie", "password": "radio-1898"}, format="json"
    )

    assert r.status_code == 200
    assert {"access", "refresh"} <= set(r.data)
    assert r.data["usuario"]["username"] == "mcurie"
    assert r.data["usuario"]["rol"] == TipoRol.OPERADOR


@pytest.mark.django_db
def test_credenciales_incorrectas_dan_401(cliente, usuario):
    """Y no un 400: el front distingue clave mala de servidor caido por el codigo."""
    r = cliente.post("/api/auth/token/", {"username": "mcurie", "password": "no"}, format="json")
    assert r.status_code == 401


@pytest.mark.django_db
def test_el_login_por_jwt_se_audita_y_actualiza_el_ultimo_acceso(cliente, usuario):
    """simplejwt no dispara user_logged_in: sin el arreglo, el login no se auditaba (RN010)."""
    cliente.post(
        "/api/auth/token/",
        {"username": "mcurie", "password": "radio-1898"},
        format="json",
        REMOTE_ADDR="10.0.0.9",
    )

    usuario.refresh_from_db()
    assert usuario.last_login is not None
    login = RegistroAuditoria.objects.get(accion="LOGIN")
    assert login.usuario == usuario
    assert login.direccion_ip == "10.0.0.9"


@pytest.mark.django_db
def test_yo_devuelve_el_usuario_de_la_sesion(como, usuario):
    """Es lo que usa el front para restaurar la sesion al recargar la pagina."""
    r = como(usuario).get("/api/auth/yo/")
    assert r.status_code == 200
    assert r.data["nombre"] == "Marie Curie"


@pytest.mark.django_db
def test_sin_sesion_la_api_responde_401(cliente):
    """Toda la API de dominio exige sesion; solo el healthcheck es publico."""
    for ruta in ("/api/ensayos/", "/api/programas/", "/api/alertas/", "/api/auth/yo/"):
        assert cliente.get(ruta).status_code == 401, ruta


@pytest.mark.django_db
def test_un_superusuario_sin_rol_actua_como_administrador(como):
    """Es la cuenta de rescate: no puede quedar bloqueada por no tener rol."""
    raiz = Usuario.objects.create_superuser(username="raiz", password="x", email="r@x.x")
    assert como(raiz).get("/api/auditoria/").status_code == 200


# ── Ensayos ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_el_listado_de_ensayos_trae_lo_que_muestra_la_tabla(como, usuario, registro):
    """El listado no arrastra la caracterizacion: es para la tabla del historial."""
    r = como(usuario).get("/api/ensayos/")

    assert r.status_code == 200
    fila = r.data[0]
    assert fila["codigo"] == "ENS-0001"
    assert fila["programa"] == {"codigo": "PRG-001", "nombre": "Grabado LPG estandar"}
    assert fila["red"]["codigo"] == "L003-R012-LPG"
    assert fila["operadores"][0]["username"] == "mcurie"
    assert "resonancias" not in fila


@pytest.mark.django_db
def test_los_filtros_del_historial(como, usuario, registro, programa, configuracion):
    """Estado, lote, operador y busqueda se resuelven en el backend."""
    otro = RegistroDeFabricacion.objects.create(
        codigo="ENS-0002",
        programa=programa,
        configuracion=configuracion,
        estado=EstadoEjecucion.ABORTADO,
        observaciones="Fallo lazo laser",
    )
    c = como(usuario)

    def codigos(query):
        return [e["codigo"] for e in c.get(f"/api/ensayos/?{query}").data]

    assert codigos("estado=ABORTADO") == ["ENS-0002"]
    assert codigos("lote=LOT-003") == ["ENS-0001"]
    assert codigos("q=lazo") == ["ENS-0002"]
    assert codigos("operador=mcurie") == ["ENS-0001"]
    otro.operadores.add(usuario)
    assert sorted(codigos("operador=mcurie")) == ["ENS-0001", "ENS-0002"]


@pytest.mark.django_db
def test_el_filtro_por_fecha(como, usuario, registro):
    """`desde` y `hasta` son fechas locales (Argentina), inclusivas.

    Se compara contra la fecha local y no la UTC: entre las 21 y las 24 h de
    Argentina ya es el dia siguiente en UTC, y el front manda la fecha que
    eligio el operador en su calendario.
    """
    from django.utils import timezone

    hoy = timezone.localdate(registro.inicio)
    manana = hoy + datetime.timedelta(days=1)
    c = como(usuario)

    assert len(c.get(f"/api/ensayos/?desde={hoy}&hasta={hoy}").data) == 1
    assert len(c.get(f"/api/ensayos/?desde={manana}").data) == 0


@pytest.mark.django_db
def test_el_detalle_ordena_la_principal_primero(como, usuario, registro, red):
    """El front toma la primera como lambda y la segunda como lambda secundario."""
    from fabricacion.models import PicoDeAtenuacion

    PicoDeAtenuacion.objects.create(red=red, longitud_onda=1200.0, transmitancia=-6.7)
    PicoDeAtenuacion.objects.create(
        red=red, longitud_onda=1610.0, transmitancia=-13.9, principal=True
    )

    r = como(usuario).get("/api/ensayos/ENS-0001/")

    assert r.status_code == 200
    assert [p["longitud_onda_nm"] for p in r.data["resonancias"]] == [1610.0, 1200.0]
    assert r.data["periodo_um"] == 500.0
    assert r.data["tiene_espectro"] is False


@pytest.mark.django_db
def test_la_curva_de_una_red_sin_caracterizar_da_404(como, usuario, registro):
    """El front lo interpreta como "sin datos de curva", no como un error."""
    assert como(usuario).get("/api/ensayos/ENS-0001/curva/").status_code == 404


@pytest.mark.django_db
def test_la_curva_devuelve_los_dos_ejes(como, usuario, registro, red, procedimiento):
    """Arreglos paralelos: el eje de lambda tal como lo exporto el OSA (ADR-0008)."""
    procedimiento.importar(red, "1500.0,-1.0\n1510.0,-9.0\n1511.0,-2.0")

    r = como(usuario).get("/api/ensayos/ENS-0001/curva/")

    assert r.status_code == 200
    assert r.data["longitudes_onda_nm"] == [1500.0, 1510.0, 1511.0]
    assert r.data["transmitancias_db"] == [-1.0, -9.0, -2.0]


@pytest.mark.django_db
def test_los_ensayos_no_se_crean_por_la_api(como, administrador, programa):
    """Las corridas las crea el controlador al ejecutar un programa, no un formulario."""
    r = como(administrador).post("/api/ensayos/", {"codigo": "X"}, format="json")
    assert r.status_code == 405


# ── Programas ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_crear_por_distancia_deriva_los_pulsos(como, investigador):
    """10 mm a 550 um son 18 pulsos: longitud = marcas x periodo."""
    r = como(investigador).post("/api/programas/", _datos_programa(), format="json")

    assert r.status_code == 201, r.data
    assert r.data["pulsos"] == 18
    assert r.data["distancia_mm"] == 10.0
    assert r.data["codigo"] == "PRG-001"
    assert r.data["creado_por"] == "investigador"


@pytest.mark.django_db
def test_crear_por_pulsos_deriva_la_distancia(como, investigador):
    """25 pulsos a 600 um son 15 mm."""
    datos = _datos_programa(
        desplazamiento_um=600.0, criterio_fin=CriterioFin.CANT_MARCAS, distancia_mm=None, pulsos=25
    )
    r = como(investigador).post("/api/programas/", datos, format="json")

    assert r.status_code == 201, r.data
    assert r.data["distancia_mm"] == pytest.approx(15.0)


@pytest.mark.django_db
def test_el_programa_queda_modelado_por_dentro(como, investigador):
    """La representacion de laboratorio se reparte en Programa, Periodo y Paso."""
    r = como(investigador).post("/api/programas/", _datos_programa(), format="json")
    programa = Programa.objects.get(codigo=r.data["codigo"])
    paso = programa.pasos.get()

    assert programa.validado is True
    assert programa.periodo.periodo_base == 550.0
    assert programa.potencia_objetivo == pytest.approx(0.010)  # se guarda en W
    assert (paso.tiempo_de_pulso, paso.repeticiones) == (400, 18)


@pytest.mark.django_db
def test_la_potencia_va_y_vuelve_en_mw(como, investigador):
    """Se guarda en W pero la API habla en mW, como el formulario del front."""
    r = como(investigador).post(
        "/api/programas/", _datos_programa(potencia_objetivo_mw=250.0), format="json"
    )
    assert r.data["potencia_objetivo_mw"] == 250.0


@pytest.mark.django_db
def test_el_criterio_por_distancia_exige_la_distancia(como, investigador):
    """Sin el valor que el criterio necesita, el programa no es ejecutable."""
    r = como(investigador).post(
        "/api/programas/", _datos_programa(distancia_mm=None), format="json"
    )
    assert r.status_code == 400
    assert "distancia_mm" in r.data


@pytest.mark.django_db
def test_los_criterios_que_necesitan_el_lazo_no_se_aceptan(como, investigador):
    """TIEMPO y ATENUACION_OBJETIVO llegan con el lazo de control (M3)."""
    r = como(investigador).post(
        "/api/programas/", _datos_programa(criterio_fin=CriterioFin.TIEMPO), format="json"
    )
    assert r.status_code == 400


@pytest.mark.django_db
def test_los_codigos_son_correlativos(como, investigador):
    """PRG-001, PRG-002... sin pisarse."""
    c = como(investigador)
    c.post("/api/programas/", _datos_programa(nombre="A"), format="json")
    r = c.post("/api/programas/", _datos_programa(nombre="B"), format="json")
    assert r.data["codigo"] == "PRG-002"


@pytest.mark.django_db
def test_editar_un_programa(como, investigador):
    """La edicion actualiza los tres objetos y vuelve a validar."""
    c = como(investigador)
    codigo = c.post("/api/programas/", _datos_programa(), format="json").data["codigo"]

    r = c.put(
        f"/api/programas/{codigo}/",
        _datos_programa(nombre="Editado", desplazamiento_um=500.0),
        format="json",
    )

    assert r.status_code == 200, r.data
    assert r.data["nombre"] == "Editado"
    assert r.data["pulsos"] == 20  # 10 mm / 500 um


@pytest.mark.django_db
def test_el_operador_lee_programas_pero_no_los_crea(como, usuario, programa):
    """Los disenan el investigador y el administrador; el operador los usa."""
    c = como(usuario)
    assert c.get("/api/programas/").status_code == 200
    assert c.post("/api/programas/", _datos_programa(), format="json").status_code == 403


@pytest.mark.django_db
def test_un_programa_sin_uso_se_borra(como, investigador):
    """Si nunca se ejecuto, borrarlo no le quita trazabilidad a nadie."""
    c = como(investigador)
    codigo = c.post("/api/programas/", _datos_programa(), format="json").data["codigo"]

    assert c.delete(f"/api/programas/{codigo}/").status_code == 204
    assert not Programa.objects.filter(codigo=codigo).exists()


@pytest.mark.django_db
def test_un_programa_ya_usado_se_da_de_baja_en_vez_de_borrarse(como, administrador, registro):
    """Sus corridas tienen que poder seguir diciendo con que se grabo (RF006/RF007)."""
    c = como(administrador)

    assert c.delete("/api/programas/PRG-001/").status_code == 204
    programa = Programa.objects.get(codigo="PRG-001")
    assert programa.activo is False
    assert [p["codigo"] for p in c.get("/api/programas/").data] == []
    assert RegistroDeFabricacion.objects.get(codigo="ENS-0001").programa == programa


# ── Alertas, emergencias y umbrales ──────────────────────────────────────────


@pytest.mark.django_db
def test_reconocer_una_alerta_por_la_api(como, usuario, configuracion):
    """Queda asentado quien la reconocio; la segunda vez es un conflicto."""
    alerta = configuracion.umbrales.get().disparar(31.0)
    c = como(usuario)

    r = c.post(f"/api/alertas/{alerta.pk}/reconocer/")
    assert r.status_code == 200
    assert r.data["reconocida"] is True
    assert r.data["reconocida_por"] == "Marie Curie"
    assert r.data["origen"] == "Temperatura del agua"

    assert c.post(f"/api/alertas/{alerta.pk}/reconocer/").status_code == 409


@pytest.mark.django_db
def test_las_emergencias_informan_si_cumplieron_rnf001(como, usuario):
    """El front lo muestra junto a cada parada."""
    from comun.enums import OrigenEmergencia
    from operacion.models import EventoEmergencia

    EventoEmergencia.objects.create(origen=OrigenEmergencia.UMBRAL, tiempo_respuesta_ms=620)
    r = como(usuario).get("/api/emergencias/")

    assert r.data[0]["cumple_rnf001"] is False
    assert r.data[0]["origen_display"] == "Umbral excedido"


@pytest.mark.django_db
def test_solo_se_listan_los_umbrales_vigentes(como, usuario, configuracion):
    """Mostrar umbrales de configuraciones viejas confundiria sobre que rige."""
    vieja = Configuracion.objects.create(nombre="Vieja")
    Umbral.objects.create(configuracion=vieja, variable="CORRIENTE_AT", valor_min=0, valor_max=1)
    configuracion.activar()

    r = como(usuario).get("/api/umbrales/")

    assert len(r.data) == 1
    umbral = r.data[0]
    assert (umbral["parametro"], umbral["unidad"], umbral["critico"]) == (
        "Temperatura del agua",
        "°C",
        True,
    )


@pytest.mark.django_db
def test_sin_configuracion_vigente_no_hay_umbrales(como, usuario, configuracion):
    """No hay una configuracion activa por defecto: se lista vacio, no la primera."""
    assert como(usuario).get("/api/umbrales/").data == []


# ── Usuarios y auditoria ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_la_auditoria_es_solo_del_administrador(como, usuario, administrador):
    """RN010: el operador no puede ver ni, por lo tanto, revisar la bitacora."""
    assert como(usuario).get("/api/auditoria/").status_code == 403
    r = como(administrador).get("/api/auditoria/")
    assert r.status_code == 200
    assert all("detalle" in a for a in r.data)


@pytest.mark.django_db
def test_el_detalle_de_auditoria_nombra_los_campos_cambiados(como, administrador, programa):
    """La bitacora guarda solo lo que cambio; el resumen lo hace legible."""
    programa.nombre = "Otro nombre"
    programa.save()

    r = como(administrador).get("/api/auditoria/")
    modificacion = next(a for a in r.data if a["accion"] == "MODIFICAR")
    assert modificacion["detalle"].startswith(f"Programa #{programa.pk}: cambio")
    assert "nombre" in modificacion["detalle"]


@pytest.mark.django_db
def test_el_listado_de_usuarios_no_expone_la_contrasena(como, usuario):
    """Lo lee cualquier rol, asi que no puede llevar nada sensible."""
    r = como(usuario).get("/api/usuarios/")
    assert r.status_code == 200
    assert all("password" not in u for u in r.data)
    assert r.data[0]["rol"] == TipoRol.OPERADOR
