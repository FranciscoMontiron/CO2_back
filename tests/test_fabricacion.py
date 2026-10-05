"""Pruebas del dominio de fabricacion y caracterizacion."""

import datetime

import pytest
from django.db import IntegrityError

from comun.enums import EstadoRed, TipoRed
from fabricacion.models import Espectro, Marca, PicoDeAtenuacion, Red


def _marcar(red, cantidad):
    """Graba `cantidad` marcas sobre una red."""
    for i in range(1, cantidad + 1):
        Marca.objects.create(
            red=red,
            numero=i,
            posicion=i * 500.0,
            cant_pulsos=10,
            ciclo_trabajo=50.0,
            tiempo_de_pulso=120,
        )


@pytest.mark.django_db
def test_el_codigo_de_la_red_se_deriva_del_lote_y_el_tipo(red):
    """El codigo no se tipea: se compone, asi no puede quedar desincronizado."""
    assert red.generar_codigo() == "L003-R012-LPG"
    assert red.codigo == str(red)


@pytest.mark.django_db
def test_cantidad_de_marcas_cuenta_las_marcas_reales(red):
    """`cantidadMarcas` es derivado del diagrama: refleja las marcas, no un contador."""
    assert red.cantidad_marcas == 0
    _marcar(red, 40)
    assert red.cantidad_marcas == 40


@pytest.mark.django_db
def test_longitud_cumple_el_invariante_marcas_por_periodo(red, registro):
    """Verifica `/longitud = marcas x periodo`, el invariante del diagrama.

    Con 40 marcas y periodo constante de 500 um, la red mide 20.000 um = 20 mm.
    """
    _marcar(red, 40)
    assert red.longitud == pytest.approx(20.0)


@pytest.mark.django_db
def test_longitud_es_desconocida_sin_fabricacion_asociada(red):
    """Sin corrida no hay programa, y sin programa no hay periodo con que derivar."""
    _marcar(red, 10)
    assert red.longitud is None


@pytest.mark.django_db
def test_una_red_recien_fabricada_no_tiene_espectro(red):
    """La multiplicidad es 0..1 a proposito: fabricar y caracterizar son momentos distintos."""
    assert not hasattr(red, "espectro") or Espectro.objects.filter(red=red).count() == 0


@pytest.mark.django_db
def test_el_espectro_reconstruye_las_longitudes_de_onda(red, procedimiento):
    """El espectro guarda el eje tal como lo exporto el OSA (ADR-0008)."""
    espectro = Espectro.objects.create(
        red=red,
        procedimiento=procedimiento,
        fecha_captura=datetime.datetime(2026, 10, 2, 14, 0, tzinfo=datetime.UTC),
        longitudes_onda=[1500.0, 1500.05, 1500.10, 1500.15],
        transmitancias=[-10.0, -12.0, -30.0, -11.0],
    )

    assert espectro.longitudes_de_onda() == pytest.approx([1500.0, 1500.05, 1500.10, 1500.15])
    assert espectro.cantidad_de_puntos == 4
    assert espectro.puntos()[2] == pytest.approx((1500.10, -30.0))


@pytest.mark.django_db
def test_la_receta_anticipa_el_tamano_del_espectro(procedimiento):
    """`span / resolucion` es lo que fija el volumen por red, y con el RNF008 encima."""
    assert procedimiento.cantidad_de_puntos == 2000


@pytest.mark.django_db
def test_una_red_tiene_a_lo_sumo_una_resonancia_principal(red):
    """Dos principales harian que `resonancia_principal()` devolviera cualquiera.

    Se bloquea en la base y no solo en el codigo: una segunda corrida de
    deteccion no puede dejar el dato ambiguo.
    """
    PicoDeAtenuacion.objects.create(
        red=red, longitud_onda=1550.0, transmitancia=-28.0, principal=True
    )
    with pytest.raises(IntegrityError):
        PicoDeAtenuacion.objects.create(
            red=red, longitud_onda=1560.0, transmitancia=-25.0, principal=True
        )


@pytest.mark.django_db
def test_resonancia_principal_devuelve_el_pico_marcado(red):
    """El metodo devuelve `PicoDeAtenuacion`, no el tipo `Resonancia` que no existia."""
    PicoDeAtenuacion.objects.create(red=red, longitud_onda=1540.0, transmitancia=-9.0)
    principal = PicoDeAtenuacion.objects.create(
        red=red, longitud_onda=1550.0, transmitancia=-28.0, principal=True
    )

    assert red.resonancia_principal() == principal


@pytest.mark.django_db
def test_los_promedios_del_lote_ignoran_las_redes_no_viables(lote, red):
    """Promediar redes rotas mezclaria mediciones de algo que no es el producto."""
    rota = Red.objects.create(
        numero=13,
        lote=lote,
        tipo=TipoRed.LPG,
        estado=EstadoRed.ROTA,
        fecha_fabricacion=datetime.date(2026, 10, 2),
    )
    PicoDeAtenuacion.objects.create(
        red=red, longitud_onda=1550.0, transmitancia=-28.0, principal=True
    )
    PicoDeAtenuacion.objects.create(
        red=rota, longitud_onda=1200.0, transmitancia=-3.0, principal=True
    )

    assert lote.promedio_longitud_onda() == pytest.approx(1550.0)
    assert lote.promedio_transmitancia() == pytest.approx(-28.0)


@pytest.mark.django_db
def test_los_promedios_son_none_sin_redes_caracterizadas(lote):
    """Sin datos el promedio es desconocido, no cero: cero seria una medicion."""
    assert lote.promedio_longitud_onda() is None
    assert lote.promedio_transmitancia() is None
