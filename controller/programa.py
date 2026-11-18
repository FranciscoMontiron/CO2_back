"""Árbol de pasos de un programa: validación y resumen, sin Django ni GPIO.

Lo usan la API (al guardar) y el controlador (al iniciar), siempre contra la
configuración de hardware vigente. Un nodo es un diccionario JSON:

* ``{"tipo": "ACCION", "componente", "comando", "argumentos"}``
* ``{"tipo": "ESPERA", "duracion_ms"}``
* ``{"tipo": "BUCLE", "repeticiones", "pasos"}``
* ``{"tipo": "PARALELO", "pasos"}`` (solo ``ACCION`` y ``ESPERA``)
"""

import copy

from controller.hardware_config import validar_operacion

PROFUNDIDAD_MAXIMA = 3
REPETICIONES_MAXIMAS = 10_000
ESPERA_MAXIMA_MS = 3_600_000

INVENTARIO_SIMULADO = {
    "componentes": {
        "laser": {
            "tipo": "SALIDA_GPIO",
            "pines": {"pin_señal": 17},
            "estado": "Vigente",
            "rol": "LASER",
            "inhibir_en_prueba": True,
        },
        "shutter": {
            "tipo": "SHUTTER_GPIO",
            "pines": {"pin_señal": 27},
            "estado": "Vigente",
            "rol": "SHUTTER",
        },
        "motor_x": {
            "tipo": "STEPPER_DRV8825",
            "pines": {"pin_step": 22, "pin_dir": 23, "pin_enable": 24},
            "parametros": {
                "unidad": "mm",
                "pasos_por_unidad": 200,
                "minimo": 0,
                "maximo": 300,
                "velocidad_maxima": 5,
            },
            "estado": "Vigente",
            "rol": "POSICION_MOTOR",
        },
        "motor_rotacion": {
            "tipo": "STEPPER_DRV8825",
            "pines": {"pin_step": 5, "pin_dir": 6, "pin_enable": 13},
            "parametros": {
                "unidad": "°",
                "pasos_por_unidad": 100,
                "minimo": 0,
                "maximo": 360,
                "velocidad_maxima": 10,
            },
            "estado": "Vigente",
        },
    }
}


def _ubicacion(camino):
    return "Paso " + ".".join(str(i) for i in camino) if camino else "Programa"


def _error(camino, mensaje):
    return ValueError(f"{_ubicacion(camino)}: {mensaje}")


def _entero(valor, nombre, minimo, maximo, camino):
    if type(valor) is not int or not minimo <= valor <= maximo:
        raise _error(camino, f"{nombre} debe ser un entero entre {minimo} y {maximo}.")
    return valor


def _claves(nodo, esperadas, camino):
    if set(nodo) != esperadas:
        raise _error(camino, f"el nodo debe tener exactamente {sorted(esperadas)}.")


def _accion(nodo, config, camino):
    _claves(nodo, {"tipo", "componente", "comando", "argumentos"}, camino)
    nombre = nodo["componente"]
    componente = config.get("componentes", {}).get(nombre) if isinstance(nombre, str) else None
    if componente is None:
        raise _error(camino, f"el componente {nombre!r} no existe.")
    if componente.get("estado") != "Vigente":
        raise _error(camino, f"el componente {nombre!r} no está vigente.")
    try:
        validar_operacion(componente, nodo["comando"], nodo["argumentos"])
    except ValueError as exc:
        raise _error(camino, f"{nombre}.{nodo['comando']}: {exc}") from exc
    return {
        "tipo": "ACCION",
        "componente": nombre,
        "comando": nodo["comando"],
        "argumentos": copy.deepcopy(nodo["argumentos"]),
    }


def _nodo(nodo, config, camino, profundidad, en_paralelo=False):
    if not isinstance(nodo, dict):
        raise _error(camino, "cada paso debe ser un objeto.")
    tipo = nodo.get("tipo")
    if en_paralelo and tipo not in ("ACCION", "ESPERA"):
        raise _error(camino, "un PARALELO admite solo ACCION y ESPERA.")
    if tipo == "ACCION":
        return _accion(nodo, config, camino)
    if tipo == "ESPERA":
        _claves(nodo, {"tipo", "duracion_ms"}, camino)
        return {
            "tipo": "ESPERA",
            "duracion_ms": _entero(nodo["duracion_ms"], "duracion_ms", 1, ESPERA_MAXIMA_MS, camino),
        }
    if tipo in ("BUCLE", "PARALELO"):
        if profundidad > PROFUNDIDAD_MAXIMA:
            raise _error(camino, f"se admiten hasta {PROFUNDIDAD_MAXIMA} niveles de bloque.")
        if tipo == "BUCLE":
            _claves(nodo, {"tipo", "repeticiones", "pasos"}, camino)
            repeticiones = _entero(
                nodo["repeticiones"], "repeticiones", 1, REPETICIONES_MAXIMAS, camino
            )
        else:
            _claves(nodo, {"tipo", "pasos"}, camino)
        hijos = _lista(nodo["pasos"], config, camino, profundidad + 1, tipo == "PARALELO")
        if tipo == "PARALELO":
            vistos = set()
            for i, hijo in enumerate(hijos, 1):
                if hijo["tipo"] != "ACCION":
                    continue
                if hijo["componente"] in vistos:
                    raise _error(
                        [*camino, i],
                        f"dos acciones sobre {hijo['componente']!r} en el mismo PARALELO.",
                    )
                vistos.add(hijo["componente"])
            return {"tipo": "PARALELO", "pasos": hijos}
        return {"tipo": "BUCLE", "repeticiones": repeticiones, "pasos": hijos}
    raise _error(camino, f"tipo de paso desconocido: {tipo!r}.")


def _lista(pasos, config, camino, profundidad, en_paralelo=False):
    if not isinstance(pasos, list) or not pasos:
        raise _error(camino, "se requiere una lista de pasos no vacía.")
    return [
        _nodo(paso, config, [*camino, i], profundidad, en_paralelo)
        for i, paso in enumerate(pasos, 1)
    ]


def _hay_accion(pasos):
    return any(
        p["tipo"] == "ACCION" or (p["tipo"] in ("BUCLE", "PARALELO") and _hay_accion(p["pasos"]))
        for p in pasos
    )


def validar_programa(pasos, config):
    """Normaliza el árbol de pasos o lanza ``ValueError`` con la ubicación del problema.

    Args:
        pasos: Lista de nodos tal como llega de la API.
        config: Configuración de hardware vigente (``{"componentes": {...}}``).

    Returns:
        Copia normalizada del árbol; la entrada no se modifica.
    """
    if not isinstance(pasos, list):
        raise _error([], "los pasos deben ser una lista.")
    if not pasos:
        raise _error([], "el programa no tiene pasos.")
    normalizado = _lista(pasos, config, [], 1)
    if not _hay_accion(normalizado):
        raise _error([], "el programa necesita al menos una ACCION.")
    return normalizado


class _Acumulado:
    """Cuentas de un tramo del árbol; se pueden sumar y multiplicar."""

    def __init__(self):
        self.acciones = 0
        self.pulsos = 0
        self.recorrido = {}
        self.duracion_ms = 0

    def sumar(self, otro, veces=1):
        self.acciones += otro.acciones * veces
        self.pulsos += otro.pulsos * veces
        self.duracion_ms += otro.duracion_ms * veces
        for nombre, valor in otro.recorrido.items():
            self.recorrido[nombre] = self.recorrido.get(nombre, 0.0) + valor * veces


def _acumular(pasos, posiciones):
    """Recorre los pasos en orden; ``posiciones`` guarda la última posición conocida."""
    total = _Acumulado()
    for paso in pasos:
        total.sumar(_acumular_nodo(paso, posiciones))
    return total


def _tiempo_de_motor_ms(distancia, args):
    """Lo que tarda un movimiento: ``|distancia| / velocidad``, en ms (0 sin velocidad)."""
    velocidad = args.get("velocidad")
    if type(velocidad) not in (int, float) or velocidad <= 0:
        return 0
    return abs(distancia) / velocidad * 1000


def _acumular_nodo(paso, posiciones):
    parcial = _Acumulado()
    tipo = paso["tipo"]
    if tipo == "ESPERA":
        parcial.duracion_ms = paso["duracion_ms"]
    elif tipo == "ACCION":
        parcial.acciones = 1
        comando, args, nombre = paso["comando"], paso["argumentos"], paso["componente"]
        if comando == "pulso":
            parcial.pulsos = 1
            parcial.duracion_ms = args["duracion_ms"]
        elif comando == "mover":
            parcial.recorrido[nombre] = abs(args["delta"])
            parcial.duracion_ms = _tiempo_de_motor_ms(args["delta"], args)
            if nombre in posiciones:
                posiciones[nombre] += args["delta"]
        elif comando == "mover_a":
            if nombre in posiciones:
                distancia = args["posicion"] - posiciones[nombre]
                parcial.recorrido[nombre] = abs(distancia)
                parcial.duracion_ms = _tiempo_de_motor_ms(distancia, args)
            posiciones[nombre] = args["posicion"]
        elif comando == "referenciar":
            posiciones[nombre] = args["posicion"]
    elif tipo == "PARALELO":
        for hijo in paso["pasos"]:
            h = _acumular_nodo(hijo, posiciones)
            parcial.acciones += h.acciones
            parcial.pulsos += h.pulsos
            parcial.duracion_ms = max(parcial.duracion_ms, h.duracion_ms)
            for nombre, valor in h.recorrido.items():
                parcial.recorrido[nombre] = parcial.recorrido.get(nombre, 0.0) + valor
    else:  # BUCLE
        primera = _acumular(paso["pasos"], posiciones)
        parcial.sumar(primera)
        if paso["repeticiones"] > 1:
            # Desde la segunda vuelta el recorrido se repite igual (una posición absoluta
            # termina siempre donde terminó la anterior). La posición, en cambio, avanza
            # el neto de la vuelta (``mover`` relativo): se extrapola a las n vueltas.
            tras_primera = dict(posiciones)
            segunda = _acumular(paso["pasos"], posiciones)
            parcial.sumar(segunda, paso["repeticiones"] - 1)
            for nombre, tras_segunda in list(posiciones.items()):
                neto = tras_segunda - tras_primera.get(nombre, tras_segunda)
                posiciones[nombre] = tras_primera.get(nombre, tras_segunda) + neto * (
                    paso["repeticiones"] - 1
                )
    return parcial


def resumen(pasos, config):
    """Cuenta lo que hará un programa expandiendo los bucles, sin ejecutarlo.

    ``recorrido`` suma ``|delta|`` de cada ``mover`` y, en ``mover_a``, la distancia
    desde la última posición conocida (si todavía no se conoce, no suma). La duración
    mínima suma pulsos, esperas y el tiempo de cada movimiento (``|distancia| / velocidad``,
    con la misma distancia que el recorrido); un ``PARALELO`` aporta el máximo de sus
    hijos. Es una cota inferior: no cuenta aceleración ni demoras de las órdenes.

    Args:
        pasos: Árbol de pasos.
        config: Configuración de hardware vigente.

    Returns:
        ``{"acciones", "pulsos", "recorrido", "duracion_minima_ms"}``.
    """
    total = _acumular(validar_programa(pasos, config), {})
    return {
        "acciones": total.acciones,
        "pulsos": total.pulsos,
        "recorrido": dict(total.recorrido),
        "duracion_minima_ms": round(total.duracion_ms),
    }
