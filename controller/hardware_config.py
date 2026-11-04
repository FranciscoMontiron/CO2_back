"""Contrato compartido por API y controlador, sin Django ni GPIO."""

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

CATALOGO = {
    "SHUTTER_GPIO": {
        "pines": ["pin_señal"],
        "parametros": {"activo_alto": True},
        "operaciones": {"armar": {}, "desarmar": {}, "abrir": {}, "cerrar": {}},
    },
    "SALIDA_GPIO": {
        "pines": ["pin_señal"],
        "parametros": {"activo_alto": True},
        "operaciones": {"encender": {}, "apagar": {}},
    },
    "ENTRADA_GPIO": {
        "pines": ["pin_señal"],
        "parametros": {"pull_up": True},
        "operaciones": {},
    },
    "STEPPER_DRV8825": {
        "pines": ["pin_step", "pin_dir", "pin_enable"],
        "parametros": {"pasos_por_mm": 200, "carrera_max_mm": 300, "velocidad_mm_s": 1},
        "operaciones": {
            "referenciar": {"posicion_mm": "numero"},
            "mover": {"posicion_mm": "numero"},
            "deshabilitar": {},
        },
    },
    "LM35": {
        "pines": [],
        "parametros": {"adc": "MCP3008", "canal": 0, "dispositivo_spi": 0, "vref": 3.3},
        "operaciones": {},
        "ayuda": "Salida analógica al MCP3008, nunca directamente a un GPIO. "
        "SPI0 reserva GPIO9/10/11 y GPIO8 (CS0) o GPIO7 (CS1).",
    },
}

PIN_A_BCM = dict(
    zip(
        [
            3,
            5,
            7,
            8,
            10,
            11,
            12,
            13,
            15,
            16,
            18,
            19,
            21,
            22,
            23,
            24,
            26,
            27,
            28,
            29,
            31,
            32,
            33,
            35,
            36,
            37,
            38,
            40,
        ],
        [
            2,
            3,
            4,
            14,
            15,
            17,
            18,
            27,
            22,
            23,
            24,
            10,
            9,
            25,
            11,
            8,
            7,
            0,
            1,
            5,
            6,
            12,
            13,
            19,
            16,
            26,
            20,
            21,
        ],
        strict=True,
    )
)


def pin_bcm(valor):
    """Normaliza BCM, GPIO17 o PIN11; rechaza alimentación y masa."""
    if isinstance(valor, str):
        texto = valor.strip().upper()
        if re.fullmatch(r"PIN\d+", texto):
            if int(texto[3:]) not in PIN_A_BCM:
                raise ValueError(f"{valor} no es un contacto GPIO de señal.")
            return PIN_A_BCM[int(texto[3:])]
        if texto.startswith("GPIO"):
            texto = texto[4:]
        if texto.isdecimal():
            valor = int(texto)
    if type(valor) is not int or not 0 <= valor <= 27:
        raise ValueError(f"Pin inválido: {valor!r}; usar BCM 0..27, GPIO17 o PIN11.")
    return valor


def numero(valor, nombre, minimo, maximo):
    """Valida números finitos sin convertir bool ni texto implícitamente."""
    if type(valor) not in (int, float) or not math.isfinite(valor):
        raise ValueError(f"{nombre} debe ser un número finito.")
    if not minimo <= valor <= maximo:
        raise ValueError(f"{nombre} debe estar entre {minimo} y {maximo}.")
    return valor


def validar_configuracion(datos):
    """Normaliza o rechaza toda la configuración antes de tocar hardware."""
    if not isinstance(datos, dict) or set(datos) != {"componentes"}:
        raise ValueError('Se requiere un objeto {"componentes": {...}}.')
    componentes = datos["componentes"]
    if not isinstance(componentes, dict) or len(componentes) > 64:
        raise ValueError("componentes debe ser un objeto con hasta 64 instancias.")
    salida, ocupados, canales = {}, {}, set()
    for nombre, original in componentes.items():
        if not isinstance(nombre, str) or not nombre.strip() or len(nombre) > 100:
            raise ValueError("Cada componente necesita un nombre de 1 a 100 caracteres.")
        try:
            if not isinstance(original, dict):
                raise ValueError("La definición debe ser un objeto.")
            if set(original) - {"tipo", "pines", "parametros", "estado"}:
                raise ValueError("Campos desconocidos en la definición.")
            tipo, estado = original.get("tipo"), original.get("estado")
            if not isinstance(tipo, str) or estado not in ("Vigente", "Eliminado"):
                raise ValueError("Se requieren tipo y estado Vigente/Eliminado.")
            pines, parametros = original.get("pines", {}), original.get("parametros", {})
            if not isinstance(pines, dict) or not isinstance(parametros, dict):
                raise ValueError("pines y parametros deben ser objetos.")
            if estado == "Eliminado":
                salida[nombre] = original.copy()
                continue
            if tipo not in CATALOGO:
                raise ValueError(f"Driver {tipo!r} no soportado. Consulte /hardware/catalogo/.")
            esquema = CATALOGO[tipo]
            if tipo == "LM35" and (pines or parametros.get("adc") != "MCP3008"):
                raise ValueError(
                    "LM35 requiere ADC MCP3008: pines={}, "
                    "parametros.adc=MCP3008 y canal=0..7; Datos no es GPIO."
                )
            if set(pines) != set(esquema["pines"]):
                raise ValueError(f"Los pines requeridos son: {esquema['pines']}.")
            if set(parametros) - set(esquema["parametros"]):
                raise ValueError("Parámetros desconocidos para este driver.")
            pines = {k: pin_bcm(v) for k, v in pines.items()}
            params = {**esquema["parametros"], **parametros}
            for bandera in ("activo_alto", "pull_up"):
                if bandera in params and type(params[bandera]) is not bool:
                    raise ValueError(f"{bandera} debe ser booleano.")
            recurso, usados = f"gpio:{nombre}", list(pines.values())
            if len(usados) != len(set(usados)):
                raise ValueError("Dos señales del componente usan el mismo GPIO.")
            if tipo == "STEPPER_DRV8825":
                numero(params["pasos_por_mm"], "pasos_por_mm", 1, 100000)
                numero(params["carrera_max_mm"], "carrera_max_mm", 0.001, 10000)
                numero(params["velocidad_mm_s"], "velocidad_mm_s", 0.001, 100)
                if params["pasos_por_mm"] * params["velocidad_mm_s"] > 2000:
                    raise ValueError("La velocidad excede 2000 pasos/s del driver manual.")
            if tipo == "LM35":
                for campo, maximo in (("canal", 7), ("dispositivo_spi", 1)):
                    if type(params[campo]) is not int or not 0 <= params[campo] <= maximo:
                        raise ValueError(f"{campo} debe ser un entero entre 0 y {maximo}.")
                numero(params["vref"], "vref", 0.1, 3.3)
                direccion = (params["dispositivo_spi"], params["canal"])
                if direccion in canales:
                    raise ValueError("Canal del ADC duplicado.")
                canales.add(direccion)
                usados, recurso = [9, 10, 11, 8 if params["dispositivo_spi"] == 0 else 7], "SPI0"
            for pin in usados:
                if pin in ocupados and not (recurso == "SPI0" == ocupados[pin][1]):
                    raise ValueError(f"GPIO{pin} ya está utilizado por {ocupados[pin][0]}.")
                ocupados[pin] = (nombre, recurso)
            salida[nombre] = {"tipo": tipo, "pines": pines, "parametros": params, "estado": estado}
        except ValueError as exc:
            raise ValueError(f"{nombre}: {exc}") from exc
    return {"componentes": salida}


def validar_operacion(config, operacion, argumentos):
    """Valida una orden al recibirla y antes de ejecutarla."""
    operaciones = CATALOGO.get(config["tipo"], {}).get("operaciones", {})
    if not isinstance(operacion, str) or operacion not in operaciones:
        raise ValueError("Operación no soportada por el componente.")
    if not isinstance(argumentos, dict) or set(argumentos) != set(operaciones[operacion]):
        raise ValueError(f"Argumentos esperados: {list(operaciones[operacion])}.")
    if "posicion_mm" in argumentos:
        numero(argumentos["posicion_mm"], "posicion_mm", 0, config["parametros"]["carrera_max_mm"])


def revision(datos):
    """Identificador estable del contenido."""
    return hashlib.sha256(
        json.dumps(datos, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()[:24]


def ruta_configuracion():
    """Ruta única configurable; Docker usa un volumen persistente."""
    return Path(
        os.environ.get("HARDWARE_CONFIG_PATH", str(Path(__file__).parent / "hardware_config.json"))
    )


def leer_archivo(ruta=None):
    """Un archivo ausente equivale a inventario vacío."""
    ruta = Path(ruta or ruta_configuracion())
    if not ruta.exists():
        return {"componentes": {}}
    with ruta.open(encoding="utf-8") as archivo:
        return json.load(archivo)


def guardar_archivo(datos, ruta=None):
    """Reemplaza atómicamente el JSON validado."""
    datos = validar_configuracion(datos)
    ruta = Path(ruta or ruta_configuracion())
    ruta.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(dir=ruta.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2, allow_nan=False)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, ruta)
    finally:
        Path(temporal).unlink(missing_ok=True)
    return datos
