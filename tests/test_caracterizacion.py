"""Pruebas de la lectura del CSV del OSA y la deteccion de resonancias.

Las funciones de `fabricacion.caracterizacion` son puras: la mayoria de estas
pruebas no tocan la base, a proposito. Es la parte que mas se va a ajustar con
el laboratorio y tiene que poder iterarse rapido.
"""

from pathlib import Path

import pytest

from fabricacion.caracterizacion import (
    EspectroInvalidoError,
    detectar_minimos,
    leer_csv_osa,
    verificar_orden,
)
from fabricacion.models import PicoDeAtenuacion


def _csv(puntos, sep=",", decimal="."):
    """Arma el texto de un CSV a partir de pares (nm, dB)."""
    filas = [
        f"{nm}{sep}{db}".replace(".", decimal) if decimal != "." else f"{nm}{sep}{db}"
        for nm, db in puntos
    ]
    return "\n".join(filas)


def _valle(n=200, centro=100, ancho=20, profundidad=20.0, base=-1.0):
    """Espectro sintetico: base plana con un valle triangular."""
    datos = [base] * n
    for i in range(centro - ancho, centro + ancho + 1):
        datos[i] = base - profundidad * (1 - abs(i - centro) / ancho)
    return datos


# ── Lectura del CSV ─────────────────────────────────────────────────────────


def test_lee_un_csv_simple():
    """El formato basico: longitud de onda y dB separados por coma."""
    puntos = leer_csv_osa("1500.00,-1.0\n1500.05,-1.2\n1500.10,-1.1")
    assert puntos == [(1500.00, -1.0), (1500.05, -1.2), (1500.10, -1.1)]


def test_ignora_encabezados_y_metadatos():
    """Los OSA exportan encabezados; no son datos y no deben romper la lectura."""
    contenido = (
        "Instrumento,Anritsu MS9740A\n"
        "Fecha,2026-10-02\n"
        "\n"
        "Wavelength(nm),Level(dBm)\n"
        "1500.00,-1.0\n"
        "1500.05,-1.2\n"
    )
    assert leer_csv_osa(contenido) == [(1500.00, -1.0), (1500.05, -1.2)]


def test_acepta_coma_decimal_con_punto_y_coma():
    """Configuracion regional argentina: un CSV que paso por Excel viene asi."""
    puntos = leer_csv_osa("1500,00;-1,5\n1500,05;-2,25")
    assert puntos == [(1500.00, -1.5), (1500.05, -2.25)]


def test_acepta_tabulador_como_separador():
    """Otros equipos exportan separado por tabulaciones."""
    assert leer_csv_osa("1500.0\t-1.0\n1500.05\t-2.0") == [(1500.0, -1.0), (1500.05, -2.0)]


def test_rechaza_un_archivo_sin_datos():
    """Un archivo con solo encabezados no es un espectro."""
    with pytest.raises(EspectroInvalidoError, match="al menos dos puntos"):
        leer_csv_osa("Wavelength,Level\nsin,datos")


# ── Verificacion del orden ──────────────────────────────────────────────────


def test_acepta_un_barrido_con_paso_variable():
    """El OSA exporta diezmado: paso grueso en lo plano, fino en la resonancia.

    En M1 esto se rechazaba por no coincidir con la resolucion. Es exactamente
    el formato del primer CSV real del laboratorio (ADR-0008).
    """
    verificar_orden([(1170.0, -3), (1180.0, -4), (1600.0, -2), (1601.0, -5), (1602.0, -7)])


def test_rechaza_longitudes_de_onda_desordenadas():
    """Un barrido que retrocede no es un barrido."""
    with pytest.raises(EspectroInvalidoError, match="crecientes"):
        verificar_orden([(1500.0, -1), (1500.1, -1), (1500.05, -1)])


def test_rechaza_longitudes_de_onda_repetidas():
    """Dos lecturas en la misma longitud de onda no se pueden graficar ni comparar."""
    with pytest.raises(EspectroInvalidoError):
        verificar_orden([(1500.0, -1), (1500.0, -2)])


# ── El archivo real del laboratorio ─────────────────────────────────────────

CSV_REAL = Path(__file__).resolve().parent.parent / "comun" / "datos_ejemplo" / "L05LPG01.csv"


def _leer_real():
    """Lee el CSV real exportado por el Yokogawa AQ6370B."""
    return leer_csv_osa(CSV_REAL.read_text(encoding="latin-1"))


def test_el_csv_real_del_aq6370b_se_lee_completo():
    """Es la prueba que importa: el archivo que exporta el equipo del laboratorio.

    Trae un encabezado de 28 lineas con metadatos (CTRWL, SPAN, RESLN...) que no
    tienen que colarse como puntos.
    """
    puntos = _leer_real()
    assert len(puntos) == 117
    assert puntos[0] == (1170.0, -3.188)
    assert puntos[-1] == (1670.0, -2.253)


def test_el_csv_real_tiene_paso_variable():
    """Documenta por que se cayo la reconstruccion por grilla regular."""
    puntos = _leer_real()
    pasos = {round(b[0] - a[0], 4) for a, b in zip(puntos, puntos[1:], strict=False)}
    assert pasos == {1.0, 2.0, 3.0, 5.0, 10.0}
    verificar_orden(puntos)  # y aun asi es un barrido valido


def test_en_el_csv_real_se_detectan_la_principal_y_la_secundaria():
    """Las dos resonancias que el front muestra como lambda y lambda secundario.

    La de 1200 nm cae en la zona de paso 10 nm y queda representada por un solo
    punto: con el filtro de ancho de M1 (3 puntos) se perdia.
    """
    puntos = _leer_real()
    indices = detectar_minimos([db for _, db in puntos])
    assert [puntos[i] for i in indices] == [(1200.0, -6.675), (1610.0, -13.859)]


# ── Deteccion de minimos ────────────────────────────────────────────────────


def test_encuentra_un_valle_en_su_centro():
    """El caso de libro: un valle triangular se detecta en su minimo."""
    assert detectar_minimos(_valle(centro=100)) == [100]


def test_encuentra_varios_valles():
    """Una LPG tiene varias resonancias, una por modo de revestimiento acoplado."""
    datos = [-1.0] * 300
    for centro, prof in [(60, 10.0), (150, 25.0), (240, 15.0)]:
        for i in range(centro - 15, centro + 16):
            datos[i] = -1.0 - prof * (1 - abs(i - centro) / 15)

    assert detectar_minimos(datos) == [60, 150, 240]


def test_ignora_el_ruido_poco_profundo():
    """Una ondulacion de 1 dB no es una resonancia."""
    datos = [-1.0, -1.5, -1.0, -1.8, -1.0] * 40
    assert detectar_minimos(datos, profundidad_minima=3.0) == []


def test_la_linea_de_base_es_robusta_a_valles_profundos():
    """Usa la mediana: un valle muy hondo no puede arrastrar la base hacia abajo."""
    datos = _valle(n=200, centro=100, ancho=5, profundidad=40.0)
    assert detectar_minimos(datos, profundidad_minima=30.0) == [100]


def test_un_valle_de_fondo_plano_se_cuenta_una_sola_vez():
    """Varios puntos con el mismo minimo no son varias resonancias."""
    datos = [-1.0] * 100
    for i in range(45, 56):
        datos[i] = -20.0
    assert len(detectar_minimos(datos)) == 1


def test_un_pico_de_ruido_de_un_solo_punto_no_es_resonancia():
    """Con grilla fina, el filtro opcional de ancho descarta el ruido de un punto.

    Por defecto esta apagado (ADR-0008): en un CSV diezmado un punto puede ser
    una resonancia real. Quien trabaja con barridos finos lo activa.
    """
    datos = [-1.0] * 100
    datos[50] = -30.0
    assert detectar_minimos(datos, ancho_minimo=3) == []


def test_dos_valles_separados_por_la_base_son_dos_resonancias():
    """Entre resonancias de una LPG la transmitancia vuelve a la base."""
    datos = [-1.0] * 100
    for i in range(20, 31):
        datos[i] = -20.0
    for i in range(60, 71):
        datos[i] = -15.0
    assert detectar_minimos(datos) == [20, 60]


def test_un_espectro_demasiado_corto_no_tiene_minimos():
    """Con menos de tres puntos no hay valle posible."""
    assert detectar_minimos([-1.0, -10.0]) == []


# ── Integracion con los modelos ─────────────────────────────────────────────


@pytest.mark.django_db
def test_importar_crea_el_espectro_sin_guardar_las_lambdas(red, procedimiento):
    """Se guarda la longitud inicial y las transmitancias; lambda se reconstruye."""
    puntos = [(1500.0 + i * 0.05, -1.0 - i) for i in range(5)]
    espectro = procedimiento.importar(red, _csv(puntos))

    assert espectro.longitud_onda_inicial == 1500.0
    assert espectro.transmitancias == [-1.0, -2.0, -3.0, -4.0, -5.0]
    assert espectro.longitudes_de_onda() == pytest.approx([p[0] for p in puntos])


@pytest.mark.django_db
def test_importar_el_csv_real_guarda_el_eje_tal_cual(red, procedimiento):
    """El dato guardado es exactamente el que exporto el instrumento."""
    espectro = procedimiento.importar(red, CSV_REAL.read_text(encoding="latin-1"))

    assert espectro.cantidad_de_puntos == 117
    assert espectro.longitudes_de_onda()[:3] == [1170.0, 1180.0, 1190.0]
    assert espectro.longitudes_de_onda()[-3:] == [1668.0, 1669.0, 1670.0]

    espectro.detectar_resonancias()
    principal = red.resonancia_principal()
    assert (principal.longitud_onda, principal.transmitancia) == (1610.0, -13.859)


@pytest.mark.django_db
def test_importar_dos_veces_reemplaza_el_espectro(red, procedimiento):
    """Reimportar un CSV corregido no puede chocar contra el espectro anterior."""
    procedimiento.importar(red, _csv([(1500.0, -1.0), (1500.1, -2.0)]))
    procedimiento.importar(red, _csv([(1600.0, -5.0), (1600.5, -6.0), (1601.0, -7.0)]))

    red.refresh_from_db()
    assert red.espectro.cantidad_de_puntos == 3


@pytest.mark.django_db
def test_importar_rechaza_un_csv_desordenado(red, procedimiento):
    """Lo unico que se exige ahora es que sea un barrido."""
    with pytest.raises(EspectroInvalidoError):
        procedimiento.importar(red, _csv([(1500.0, -1.0), (1499.0, -2.0)]))


@pytest.mark.django_db
def test_detectar_resonancias_marca_la_mas_profunda_como_principal(red, procedimiento):
    """La principal es la de mayor atenuacion, no la primera que aparece."""
    datos = [-1.0] * 300
    for centro, prof in [(60, 10.0), (150, 25.0), (240, 15.0)]:
        for i in range(centro - 15, centro + 16):
            datos[i] = -1.0 - prof * (1 - abs(i - centro) / 15)
    puntos = [(1500.0 + i * 0.05, db) for i, db in enumerate(datos)]
    espectro = procedimiento.importar(red, _csv(puntos))

    picos = espectro.detectar_resonancias()

    assert len(picos) == 3
    principal = red.resonancia_principal()
    assert principal.transmitancia == pytest.approx(-26.0)
    assert principal.longitud_onda == pytest.approx(1500.0 + 150 * 0.05)


@pytest.mark.django_db
def test_detectar_dos_veces_no_duplica_los_picos(red, procedimiento):
    """Reemplaza las detecciones anteriores: el resultado tiene que ser idempotente."""
    puntos = [(1500.0 + i * 0.05, db) for i, db in enumerate(_valle())]
    espectro = procedimiento.importar(red, _csv(puntos))

    espectro.detectar_resonancias()
    espectro.detectar_resonancias()

    assert PicoDeAtenuacion.objects.filter(red=red).count() == 1


@pytest.mark.django_db
def test_un_espectro_plano_no_deja_picos(red, procedimiento):
    """Sin resonancias la red queda sin picos, y sin principal."""
    puntos = [(1500.0 + i * 0.05, -1.0) for i in range(100)]
    espectro = procedimiento.importar(red, _csv(puntos))

    assert espectro.detectar_resonancias() == []
    assert red.resonancia_principal() is None
