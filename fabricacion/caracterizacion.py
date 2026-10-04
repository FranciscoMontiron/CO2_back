"""Procesamiento del espectro del OSA: lectura del CSV y deteccion de resonancias.

Son funciones puras, sin Django ni base de datos, a proposito: el algoritmo es la
parte que mas se va a discutir con el laboratorio, y tiene que poder probarse y
ajustarse sin levantar nada.

La nota del Diagrama de Clases lo deja asentado: lambda y L son el minimo del CSV
del OSA, y el sistema los **calcula** en vez de que alguien los tipee.
"""

from __future__ import annotations

import statistics


class EspectroInvalidoError(ValueError):
    """El CSV no se puede convertir en un espectro valido."""


def _a_numero(texto: str) -> float | None:
    """Convierte un campo a float aceptando coma decimal.

    El laboratorio trabaja con configuracion regional argentina: un CSV que paso
    por Excel trae ``1550,25`` en vez de ``1550.25``.

    Args:
        texto: El campo crudo.

    Returns:
        El numero, o ``None`` si el campo no es numerico.
    """
    try:
        return float(texto.strip().replace(",", "."))
    except ValueError:
        return None


def leer_csv_osa(contenido: str) -> list[tuple[float, float]]:
    """Extrae los pares (longitud de onda, transmitancia) de un CSV del OSA.

    Es tolerante con lo que exportan los distintos equipos: ignora las lineas de
    encabezado y metadatos (cualquier linea cuyos dos primeros campos no sean
    numericos), y acepta coma, punto y coma o tabulador como separador.

    Args:
        contenido: El texto completo del archivo.

    Returns:
        Los pares ``(nm, dB)`` en el orden del archivo.

    Raises:
        EspectroInvalidoError: Si no hay al menos dos puntos validos.
    """
    puntos = []
    for linea in contenido.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        # Con punto y coma o tabulador la coma es decimal; con coma, es separador.
        if ";" in linea:
            campos = linea.split(";")
        elif "\t" in linea:
            campos = linea.split("\t")
        else:
            campos = linea.split(",")
        if len(campos) < 2:
            continue
        lambda_nm, db = _a_numero(campos[0]), _a_numero(campos[1])
        if lambda_nm is not None and db is not None:
            puntos.append((lambda_nm, db))

    if len(puntos) < 2:
        raise EspectroInvalidoError(
            "El archivo no tiene al menos dos puntos (longitud de onda, dB)."
        )
    return puntos


def verificar_orden(puntos: list[tuple[float, float]]) -> None:
    """Verifica que las longitudes de onda sean estrictamente crecientes.

    En M1 esta funcion ademas exigia que el paso coincidiera con la resolucion
    del procedimiento, porque el espectro reconstruia el eje en vez de
    guardarlo. El primer CSV real del laboratorio vino diezmado con paso
    variable (de 1 a 10 nm) y esa exigencia lo habria rechazado. Ahora el eje se
    guarda tal cual (ADR-0008), y lo unico que se exige es que sea un barrido.

    Args:
        puntos: Pares ``(nm, dB)`` leidos del CSV.

    Raises:
        EspectroInvalidoError: Si una longitud de onda no supera a la anterior.
    """
    for (anterior, _), (actual, _) in zip(puntos, puntos[1:], strict=False):
        if actual <= anterior:
            raise EspectroInvalidoError(
                f"Las longitudes de onda del CSV no son estrictamente crecientes "
                f"({anterior} nm seguido de {actual} nm)."
            )


def detectar_minimos(
    transmitancias: list[float],
    profundidad_minima: float = 3.0,
    ancho_minimo: int = 1,
) -> list[int]:
    """Encuentra las resonancias del espectro: los valles de atenuacion.

    Una resonancia de una red de periodo largo es un valle en la transmitancia.
    El criterio es por **tramos**: cada tramo contiguo de puntos que cae al menos
    ``profundidad_minima`` dB por debajo de la linea de base es una resonancia, y
    su posicion es el punto mas bajo del tramo.

    Se descarto buscar minimos locales dentro de una ventana fija: un valle de
    fondo plano mas ancho que la ventana se contaba dos veces. Con tramos, un
    valle es uno solo sin importar su forma. Funciona porque entre dos
    resonancias de una LPG la transmitancia vuelve a la base, asi que los tramos
    quedan naturalmente separados.

    La linea de base es la **mediana** y no el promedio: los valles profundos
    tiran el promedio hacia abajo y harian que una resonancia real pareciera
    menos profunda de lo que es.

    Es un criterio de partida razonable, **no validado todavia con el CIOp**: los
    parametros por defecto hay que ajustarlos con espectros reales.

    Args:
        transmitancias: Los valores en dB, en orden de longitud de onda.
        profundidad_minima: Cuanto debajo de la base tiene que caer el tramo, en dB.
        ancho_minimo: Cantidad minima de puntos consecutivos del tramo. Sirve
            para filtrar ruido de un solo punto **cuando la grilla es fina y
            regular**. Por defecto es 1 (sin filtro): en un CSV diezmado una
            resonancia real puede quedar representada por un unico punto, como
            el valle de 1200 nm del archivo de ejemplo del laboratorio.

    Returns:
        Los indices de las resonancias, ordenados por posicion.
    """
    if len(transmitancias) < 3:
        return []

    umbral = statistics.median(transmitancias) - profundidad_minima
    minimos = []
    inicio = None
    for i, valor in enumerate([*transmitancias, float("inf")]):  # centinela de cierre
        if valor <= umbral:
            if inicio is None:
                inicio = i
        elif inicio is not None:
            if i - inicio >= ancho_minimo:
                tramo = transmitancias[inicio:i]
                minimos.append(inicio + tramo.index(min(tramo)))
            inicio = None
    return minimos
