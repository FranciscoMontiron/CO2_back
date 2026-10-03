"""Pruebas de los programas de grabado y los perfiles de periodo."""

import pytest
from django.core.exceptions import ValidationError

from comun.enums import CriterioFin, TipoPeriodo
from programas.models import Paso, Periodo


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
