"""Pruebas de los programas de grabado y los perfiles de periodo."""

import pytest
from django.core.exceptions import ValidationError

from comun.enums import CriterioFin, TipoPeriodo
from programas.models import Paso, Periodo, Programa


@pytest.mark.django_db
def test_periodo_constante_devuelve_siempre_el_mismo_valor(periodo_constante):
    """El perfil constante es el caso base: la red tiene paso uniforme."""
    assert periodo_constante.calcular_distancia(0) == 500.0
    assert periodo_constante.calcular_distancia(99) == 500.0


@pytest.mark.django_db
def test_periodo_constante_acumula_marcas_por_periodo(periodo_constante):
    """Es el invariante `/longitud = marcas x periodo` del diagrama."""
    assert periodo_constante.distancia_acumulada(40) == pytest.approx(20_000.0)


@pytest.mark.django_db
def test_periodo_lineal_crece_de_a_pasos_fijos():
    """El perfil lineal es el chirp mas simple: el periodo crece parejo."""
    periodo = Periodo.objects.create(tipo=TipoPeriodo.LINEAL, periodo_base=500.0, incremento=10.0)

    assert periodo.calcular_distancia(0) == 500.0
    assert periodo.calcular_distancia(3) == 530.0
    # 500 + 510 + 520 = 1530
    assert periodo.distancia_acumulada(3) == pytest.approx(1530.0)


@pytest.mark.django_db
def test_periodo_exponencial_escala_por_el_factor():
    """El perfil exponencial multiplica el periodo marca a marca."""
    periodo = Periodo.objects.create(tipo=TipoPeriodo.EXPONENCIAL, periodo_base=500.0, factor=1.1)

    assert periodo.calcular_distancia(0) == pytest.approx(500.0)
    assert periodo.calcular_distancia(2) == pytest.approx(605.0)


@pytest.mark.django_db
def test_el_perfil_lineal_exige_su_incremento():
    """Sin incremento el perfil lineal es indistinguible del constante.

    Dejarlo pasar significaria grabar una red uniforme creyendo que es chirpeada,
    y el error recien aparece al caracterizarla.
    """
    periodo = Periodo(tipo=TipoPeriodo.LINEAL, periodo_base=500.0)
    with pytest.raises(ValidationError) as exc:
        periodo.clean()
    assert "incremento" in str(exc.value)


@pytest.mark.django_db
def test_el_perfil_exponencial_exige_su_factor():
    """Mismo motivo que el lineal: sin factor no hay perfil."""
    periodo = Periodo(tipo=TipoPeriodo.EXPONENCIAL, periodo_base=500.0)
    with pytest.raises(ValidationError) as exc:
        periodo.clean()
    assert "factor" in str(exc.value)


@pytest.mark.django_db
def test_el_perfil_constante_rechaza_parametros_que_no_usa():
    """Un constante con incremento cargado delata un cambio de tipo a medias."""
    periodo = Periodo(tipo=TipoPeriodo.CONSTANTE, periodo_base=500.0, incremento=10.0)
    with pytest.raises(ValidationError):
        periodo.clean()


@pytest.mark.django_db
def test_un_programa_completo_se_valida(programa):
    """El camino feliz: con pasos, periodo y criterio con valor, valida."""
    assert programa.validar() is True
    programa.refresh_from_db()
    assert programa.validado is True


@pytest.mark.django_db
def test_un_programa_sin_pasos_no_valida(programa):
    """Un programa vacio que llegara al controlador seria una parada en seco."""
    programa.pasos.all().delete()
    with pytest.raises(ValidationError) as exc:
        programa.validar()
    assert "no tiene pasos" in str(exc.value)


@pytest.mark.django_db
def test_un_criterio_de_fin_sin_valor_no_valida(programa):
    """Salvo MANUAL, todo criterio necesita contra que compararse."""
    programa.valor_criterio_fin = None
    programa.save()
    with pytest.raises(ValidationError) as exc:
        programa.validar()
    assert "necesita un valor" in str(exc.value)


@pytest.mark.django_db
def test_el_criterio_manual_no_exige_valor(programa):
    """MANUAL es la excepcion: termina cuando el operador lo decide."""
    programa.criterio_fin = CriterioFin.MANUAL
    programa.valor_criterio_fin = None
    programa.save()

    assert programa.validar() is True


@pytest.mark.django_db
def test_un_programa_sin_periodo_no_valida(programa):
    """Sin perfil de periodo no se sabe donde va cada marca."""
    programa.periodo = None
    programa.save()
    with pytest.raises(ValidationError) as exc:
        programa.validar()
    assert "periodo" in str(exc.value)


@pytest.mark.django_db
def test_la_duracion_estimada_suma_pulsos_por_repeticiones(programa):
    """Es una cota inferior: no contempla el desplazamiento del motor."""
    Paso.objects.create(
        programa=programa, orden=2, etiqueta="G02", tiempo_de_pulso=50, repeticiones=4
    )
    # 120 x 1 + 50 x 4 = 320
    assert programa.duracion_estimada() == 320


@pytest.mark.django_db
def test_los_pasos_salen_ordenados(programa):
    """La composicion del diagrama es {ordered}: el orden es parte del dato."""
    Paso.objects.create(programa=programa, orden=3, etiqueta="G03")
    Paso.objects.create(programa=programa, orden=2, etiqueta="G02")

    assert [p.orden for p in programa.pasos.all()] == [1, 2, 3]


# ── Programa como arbol de pasos (API) ───────────────────────────────────────


def _cliente(rol=None):
    from rest_framework.test import APIClient

    from comun.enums import TipoRol
    from tests.test_api import _usuario_con_rol

    c = APIClient()
    c.force_authenticate(_usuario_con_rol(rol or TipoRol.INVESTIGADOR, "investigador"))
    return c


@pytest.mark.django_db
def test_guardar_programa_con_arbol_y_leerlo_igual():
    from tests.conftest import arbol_de_ejemplo

    c = _cliente()
    pasos = arbol_de_ejemplo()
    r = c.post(
        "/api/programas/",
        {"nombre": "Helicoidal", "descripcion": "d", "pasos": pasos},
        format="json",
    )
    assert r.status_code == 201, r.data
    assert r.data["pasos"] == pasos
    assert r.data["validado"] is True
    assert r.data["creado_por"] == "investigador"
    leido = c.get(f"/api/programas/{r.data['codigo']}/").data
    assert leido["pasos"] == pasos
    assert leido["nombre"] == "Helicoidal"
    # El arbol se guarda en filas de Paso con padre.
    programa = Programa.objects.get(codigo=r.data["codigo"])
    assert programa.pasos.filter(padre__isnull=True).count() == 2
    assert programa.pasos.filter(tipo="BUCLE").get().hijos.count() == 2
    assert programa.potencia_objetivo is None and programa.periodo is None


@pytest.mark.django_db
def test_guardar_programa_invalido_devuelve_400_con_la_ubicacion():
    from tests.conftest import arbol_de_ejemplo

    pasos = arbol_de_ejemplo()
    pasos[1]["pasos"][0]["pasos"][0]["componente"] = "fantasma"
    r = _cliente().post("/api/programas/", {"nombre": "X", "pasos": pasos}, format="json")
    assert r.status_code == 400
    assert "Paso 2.1.1" in str(r.data["pasos"])
    assert Programa.objects.count() == 0


@pytest.mark.django_db
def test_resumen_en_la_respuesta():
    from tests.conftest import arbol_de_ejemplo

    r = _cliente().post(
        "/api/programas/", {"nombre": "X", "pasos": arbol_de_ejemplo()}, format="json"
    )
    assert r.data["resumen"] == {
        "acciones": 7,
        "pulsos": 3,
        "recorrido": {"motor_x": pytest.approx(1.65)},
        "duracion_minima_ms": 1800,  # 3 × (max(400 ms, 0,55 mm a 1 mm/s) + 50 ms)
    }


@pytest.mark.django_db
def test_editar_reemplaza_el_arbol_y_sube_la_version():
    from tests.conftest import arbol_de_ejemplo

    c = _cliente()
    codigo = c.post(
        "/api/programas/", {"nombre": "X", "pasos": arbol_de_ejemplo()}, format="json"
    ).data["codigo"]
    nuevos = [{"tipo": "ACCION", "componente": "laser", "comando": "encender", "argumentos": {}}]
    r = c.put(f"/api/programas/{codigo}/", {"nombre": "Y", "pasos": nuevos}, format="json")
    assert r.status_code == 200, r.data
    assert r.data["version"] == 2 and r.data["pasos"] == nuevos
    assert Programa.objects.get(codigo=codigo).pasos.count() == 1


@pytest.mark.django_db
def test_en_gpio_se_valida_contra_el_archivo_de_hardware(settings, tmp_path, monkeypatch):
    from tests.conftest import arbol_de_ejemplo

    monkeypatch.setenv("HARDWARE_CONFIG_PATH", str(tmp_path / "hw.json"))  # inventario vacio
    settings.CONTROLLER_HAL = "gpio"
    r = _cliente().post(
        "/api/programas/", {"nombre": "X", "pasos": arbol_de_ejemplo()}, format="json"
    )
    assert r.status_code == 400
    assert "shutter" in str(r.data["pasos"])


@pytest.mark.django_db(transaction=True)
def test_programas_viejos_quedan_sin_validar():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    previa = [("programas", "0004_programa_procedimiento")]
    ejecutor = MigrationExecutor(connection)
    ejecutor.migrate(previa)
    try:
        apps = ejecutor.loader.project_state(previa).apps
        programa_m, paso_m, periodo_m = (
            apps.get_model("programas", "Programa"),
            apps.get_model("programas", "Paso"),
            apps.get_model("programas", "Periodo"),
        )
        periodo = periodo_m.objects.create(tipo="CONSTANTE", periodo_base=500.0)
        viejo = programa_m.objects.create(
            codigo="PRG-V",
            nombre="Viejo",
            tipo_red="LPG",
            potencia_objetivo=0.01,
            criterio_fin="MANUAL",
            periodo=periodo,
            validado=True,
        )
        paso_m.objects.create(programa=viejo, orden=1, etiqueta="G01", tiempo_de_pulso=400)

        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())
        programa = Programa.objects.get(codigo="PRG-V")
        assert programa.validado is False
        paso = programa.pasos.get()
        assert paso.tipo == "ACCION" and paso.padre is None and paso.argumentos == {}
    finally:
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())


@pytest.mark.django_db
def test_no_se_repite_el_orden_entre_pasos_raiz(programa_arbol):
    """MySQL no tiene indices parciales: la unicidad de las raices la hace clean()."""
    from django.core.exceptions import ValidationError

    duplicado = Paso(programa=programa_arbol, padre=None, orden=1, tipo="ESPERA")
    with pytest.raises(ValidationError):
        duplicado.clean()
    hijo = Paso(programa=programa_arbol, padre=programa_arbol.pasos.get(tipo="BUCLE"), orden=1)
    with pytest.raises(ValidationError):
        hijo.clean()
    Paso(programa=programa_arbol, padre=None, orden=99).clean()  # libre: no falla


@pytest.mark.django_db
def test_el_orden_de_los_nodos_sale_de_su_posicion(programa_arbol):
    """El cliente no manda el orden, asi que no puede repetirlo."""
    raices = list(programa_arbol.pasos.filter(padre__isnull=True))
    assert [p.orden for p in raices] == [1, 2]


@pytest.mark.django_db
def test_inventario_para_el_editor(settings):
    from controller.programa import INVENTARIO_SIMULADO

    c = _cliente()
    r = c.get("/api/programas/inventario/")
    assert r.status_code == 200
    assert r.json() == INVENTARIO_SIMULADO
    from rest_framework.test import APIClient

    assert APIClient().get("/api/programas/inventario/").status_code in (401, 403)
