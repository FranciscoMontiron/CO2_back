"""Contrato compartido por API y controlador, sin Django ni GPIO."""

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path


def _registro():
    """Registro de drivers, importándolos a demanda para evitar el ciclo de imports."""
    from controller import drivers  # noqa: F401 - registra los drivers
    from controller.factory import REGISTRO_COMPONENTES

    return REGISTRO_COMPONENTES


def comandos_de(tipo):
    """Operaciones y argumentos de un tipo; vacío si el tipo no existe."""
    clase = _registro().get(tipo)
    return clase.COMANDOS if clase else {}


def catalogo():
    """Catálogo público construido desde la ficha de cada driver registrado."""
    registro = _registro()
    return {
        tipo: {
            "descripcion": clase.DESCRIPCION,
            "pines": list(clase.PINES),
            "parametros": dict(clase.PARAMETROS),
            "operaciones": {op: dict(args) for op, args in clase.COMANDOS.items()},
            "campos": dict(clase.CAMPOS),
            "contrato": clase.CONTRATO,
            "ayuda": clase.AYUDA,
        }
        for tipo, clase in registro.items()
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


CLAVES_OPCIONES = ("rol", "ganancia", "offset", "inhibir_en_prueba")


def _opciones(original, clase):
    """Valida rol, ganancia, offset e inhibir_en_prueba de un componente Vigente."""
    from controller.roles import ROLES, campo_de

    opciones, rol = {}, original.get("rol")
    if "rol" in original:
        if not isinstance(rol, str) or rol not in ROLES:
            raise ValueError(f"Rol desconocido: {rol!r}. Válidos: {sorted(ROLES)}.")
        if rol == "SHUTTER":
            if not all(c in clase.CAMPOS for c in ROLES[rol]["campos"]):
                raise ValueError(f"El driver no declara los campos del rol {rol}.")
        elif campo_de(rol, clase.CAMPOS) is None:
            raise ValueError(f"El driver no declara ningún campo aceptado por el rol {rol}.")
        opciones["rol"] = rol
    for clave in ("ganancia", "offset"):
        if clave in original:
            if rol is None or ROLES[rol]["clase"] != "numero":
                raise ValueError(f"{clave} solo se admite con un rol numérico.")
            opciones[clave] = numero(original[clave], clave, -1e9, 1e9)
    if "inhibir_en_prueba" in original:
        if type(original["inhibir_en_prueba"]) is not bool:
            raise ValueError("inhibir_en_prueba debe ser booleano.")
        opciones["inhibir_en_prueba"] = original["inhibir_en_prueba"]
    return opciones


def validar_configuracion(datos):
    """Normaliza o rechaza toda la configuración antes de tocar hardware."""
    if not isinstance(datos, dict) or set(datos) != {"componentes"}:
        raise ValueError('Se requiere un objeto {"componentes": {...}}.')
    componentes = datos["componentes"]
    if not isinstance(componentes, dict) or len(componentes) > 64:
        raise ValueError("componentes debe ser un objeto con hasta 64 instancias.")
    registro = _registro()
    salida, ocupados, contexto = {}, {}, {}
    for nombre, original in componentes.items():
        if not isinstance(nombre, str) or not nombre.strip() or len(nombre) > 100:
            raise ValueError("Cada componente necesita un nombre de 1 a 100 caracteres.")
        try:
            if not isinstance(original, dict):
                raise ValueError("La definición debe ser un objeto.")
            if set(original) - {"tipo", "pines", "parametros", "estado", *CLAVES_OPCIONES}:
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
            clase = registro.get(tipo)
            if clase is None:
                raise ValueError(f"Driver {tipo!r} no soportado. Consulte /hardware/catalogo/.")
            parametros = clase.prevalidar(pines, parametros)
            if set(pines) != set(clase.PINES):
                raise ValueError(f"Los pines requeridos son: {clase.PINES}.")
            if set(parametros) - set(clase.PARAMETROS):
                raise ValueError("Parámetros desconocidos para este driver.")
            pines = {k: pin_bcm(v) for k, v in pines.items()}
            params = {**clase.PARAMETROS, **parametros}
            for bandera in ("activo_alto", "pull_up"):
                if bandera in params and type(params[bandera]) is not bool:
                    raise ValueError(f"{bandera} debe ser booleano.")
            usados, recurso = clase.validar_parametros(nombre, pines, params, contexto)
            for pin in usados:
                if pin in ocupados and not (recurso == "SPI0" == ocupados[pin][1]):
                    raise ValueError(f"GPIO{pin} ya está utilizado por {ocupados[pin][0]}.")
                ocupados[pin] = (nombre, recurso)
            salida[nombre] = {"tipo": tipo, "pines": pines, "parametros": params, "estado": estado}
            salida[nombre].update(_opciones(original, clase))
        except ValueError as exc:
            raise ValueError(f"{nombre}: {exc}") from exc
    roles = {}
    for nombre, comp in salida.items():
        rol = comp.get("rol")
        if comp["estado"] == "Vigente" and rol is not None:
            if rol in roles:
                raise ValueError(f"{nombre}: el rol {rol} ya lo tiene {roles[rol]}.")
            roles[rol] = nombre
    return {"componentes": salida}


def validar_operacion(config, operacion, argumentos):
    """Valida una orden al recibirla y antes de ejecutarla."""
    operaciones = comandos_de(config["tipo"])
    if not isinstance(operacion, str) or operacion not in operaciones:
        raise ValueError("Operación no soportada por el componente.")
    if not isinstance(argumentos, dict) or set(argumentos) != set(operaciones[operacion]):
        raise ValueError(f"Argumentos esperados: {list(operaciones[operacion])}.")
    _registro()[config["tipo"]].validar_argumentos(config, operacion, argumentos)


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
