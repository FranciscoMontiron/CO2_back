"""Vocabulario cerrado de los procedimientos guiados, y su validacion.

Un paso guiado no guarda codigo: guarda *datos* que el front interpreta. Este
modulo define que datos son validos. Es la lista blanca que impide que un
procedimiento lea una variable que no existe o mande un comando que no
corresponde.

Condicion
    Una hoja ``{"fuente": "variables.CAUDAL_REFRIGERANTE", "op": ">=", "valor": 2}``
    o una combinacion ``{"todas": [...]}`` (Y) / ``{"alguna": [...]}`` (O).

Indicador (chip de hardware)
    ``{"nombre": "Caudal", "estados": [{"cuando": <cond>, "texto": "...", "tono": "ok"}]}``.
    Gana el primer estado cuya condicion se cumple; uno sin ``cuando`` es el de
    siempre. El texto admite ``{fuente}`` o ``{fuente:decimales}``.

Accion
    Boton: ``{"etiqueta": "Encender refrigeracion", "accion": "encender_refrigeracion"}``.
    Interruptor: ``{"tipo": "interruptor", "estado": <cond>, "encender": <boton>,
    "apagar": <boton>}``. Un solo boton que manda ``apagar`` si la condicion de
    ``estado`` se cumple y ``encender`` si no.

Salida
    ``{"etiqueta": "Fabricar otra PHLPG", "destino": 6}``: el numero de paso al que va.

La seguridad no depende de esto: el controlador rechaza cualquier comando fuera
de orden (RN003, umbrales, emergencia). Un procedimiento mal armado puede
confundir al operador, pero no saltear un interlock.
"""

from __future__ import annotations

import re
from typing import Any

from comun.enums import CondicionSeguridad, TipoValidacion, VariableMonitoreada
from controller import protocolo as p

BOOL, NUM, TEXTO = "bool", "num", "texto"

# Fuente -> (tipo, descripcion para el editor). Refleja el mensaje de telemetria
# del controlador (``Maquina.telemetria``) mas el contexto de la pantalla:
# el programa elegido y el ensayo lanzado.
FUENTES: dict[str, tuple[str, str]] = {
    "estado": (TEXTO, "Estado del sistema (REPOSO, PREPARANDO, LISTO, GRABANDO, EMERGENCIA)"),
    # Verdadera mientras llega la telemetria. Es lo unico que se puede afirmar
    # de equipos que todavia no tienen sensor propio (el interrogador optico).
    "controlador.conectado": (BOOL, "El controlador esta conectado y mandando telemetria"),
    **{f"condiciones.{c.value}": (BOOL, f"Condicion: {c.label}") for c in CondicionSeguridad},
    **{f"variables.{v.value}": (NUM, v.label) for v in VariableMonitoreada},
    "senales.FIBRA_ALINEADA": (BOOL, "Senal: fibra alineada"),
    "senales.SHUTTER_ABIERTO": (BOOL, "Senal: shutter abierto"),
    "senales.SHUTTER_CERRADO": (BOOL, "Senal: shutter cerrado (fin de carrera)"),
    "actuadores.refrigeracion": (BOOL, "Actuador: refrigeracion encendida"),
    "actuadores.alta_tension": (BOOL, "Actuador: alta tension habilitada"),
    "actuadores.laser": (BOOL, "Actuador: laser encendido"),
    "actuadores.lazo_cerrado": (BOOL, "Actuador: lazo de potencia cerrado"),
    "actuadores.shutter_armado": (BOOL, "Actuador: shutter armado"),
    "actuadores.shutter_abierto": (BOOL, "Actuador: shutter abierto"),
    "actuadores.alineando": (BOOL, "Actuador: alineacion en curso"),
    "extra.potencia_optica_mw": (NUM, "Potencia optica [mW]"),
    "extra.sensor_sombra_mw": (NUM, "Sensor de sombra [mW]"),
    "extra.temperatura_laser": (NUM, "Temperatura del laser [C]"),
    "extra.frecuencia_hz": (NUM, "Frecuencia de pulsos [Hz]"),
    "ejecucion.activa": (BOOL, "Hay un programa ejecutandose"),
    "ejecucion.modo": (TEXTO, "Modo de la ejecucion (REAL, PRUEBA)"),
    "ejecucion.pulso": (NUM, "Pulso en curso"),
    "ejecucion.pulsos": (NUM, "Pulsos totales de la ejecucion"),
    "ejecucion.progreso": (NUM, "Progreso de la ejecucion (0 a 1)"),
    "ejecucion.distancia_mm": (NUM, "Distancia recorrida [mm]"),
    "programa.seleccionado": (BOOL, "Hay un programa elegido"),
    "programa.tiene_criterio_fin": (BOOL, "El programa tiene criterio de fin"),
    "programa.nombre": (TEXTO, "Nombre del programa"),
    "programa.distancia_mm": (NUM, "Distancia total del programa [mm]"),
    "programa.pulsos": (NUM, "Pulsos estimados del programa"),
    "ensayo.lanzado": (BOOL, "Se lanzo un ensayo desde este procedimiento"),
    "ensayo.terminado": (BOOL, "El ensayo lanzado ya termino y quedo registrado"),
    "ensayo.completado": (BOOL, "El ensayo lanzado termino completo"),
    "ensayo.modo": (TEXTO, "Modo del ensayo lanzado (REAL, PRUEBA)"),
    "ensayo.registro": (TEXTO, "Codigo del registro del ensayo"),
    "ensayo.estado": (TEXTO, "Estado del ensayo en mayusculas (COMPLETADO, ABORTADO...)"),
}

OPERADORES: dict[str, set[str]] = {
    BOOL: {"es_verdadero", "es_falso"},
    NUM: {"==", "!=", ">", ">=", "<", "<="},
    TEXTO: {"==", "!="},
}
OPERADORES_SIN_VALOR = {"es_verdadero", "es_falso"}

INICIAR_REAL, INICIAR_PRUEBA = "iniciar_real", "iniciar_prueba"

# Comandos que un paso puede ofrecer como boton. Quedan afuera los globales
# (emergencia y rearme tienen su propio control siempre visible), la inyeccion
# de fallas (es del simulador) y mover el motor (necesita un parametro).
ACCIONES: dict[str, str] = {
    p.ENCENDER_REFRIGERACION: "Encender refrigeracion",
    p.APAGAR_REFRIGERACION: "Apagar refrigeracion",
    p.HABILITAR_AT: "Habilitar alta tension",
    p.DESHABILITAR_AT: "Deshabilitar alta tension",
    p.ALINEAR_FIBRA: "Alinear fibra",
    p.ARMAR_SHUTTER: "Armar shutter",
    p.DESARMAR_SHUTTER: "Desarmar shutter",
    p.ENCENDER_LASER: "Encender laser y cerrar lazo",
    p.APAGAR_LASER: "Apagar laser",
    INICIAR_PRUEBA: "Pre-grabado (laser inhibido)",
    INICIAR_REAL: "Iniciar grabado",
    p.ABORTAR: "Abortar",
    p.APAGAR: "Apagar en orden inverso",
}

TONOS = {"ok", "warn", "danger", "neutral", "info"}

_MARCADOR = re.compile(r"\{([^{}:]+)(?::(\d))?\}")
_PROFUNDIDAD_MAXIMA = 4


def _es_numero(valor: Any) -> bool:
    return isinstance(valor, int | float) and not isinstance(valor, bool)


def validar_condicion(condicion: Any, donde: str, profundidad: int = 0) -> list[str]:
    """Valida una condicion contra el catalogo.

    Args:
        condicion: La condicion a validar.
        donde: Descripcion de donde esta, para el mensaje de error.
        profundidad: Nivel de anidamiento actual.

    Returns:
        Los errores encontrados; vacio si es valida.
    """
    if not isinstance(condicion, dict):
        return [f"{donde}: la condicion tiene que ser un objeto."]
    if profundidad > _PROFUNDIDAD_MAXIMA:
        return [f"{donde}: la condicion anida demasiados niveles."]
    for combinador in ("todas", "alguna"):
        if combinador in condicion:
            partes = condicion[combinador]
            if set(condicion) != {combinador} or not isinstance(partes, list) or not partes:
                return [f"{donde}: «{combinador}» necesita una lista no vacia de condiciones."]
            errores = []
            for i, parte in enumerate(partes, 1):
                errores += validar_condicion(parte, f"{donde} ({combinador} #{i})", profundidad + 1)
            return errores

    fuente, op = condicion.get("fuente"), condicion.get("op")
    if fuente not in FUENTES:
        return [f"{donde}: la fuente «{fuente}» no existe."]
    tipo = FUENTES[fuente][0]
    if op not in OPERADORES[tipo]:
        permitidos = ", ".join(sorted(OPERADORES[tipo]))
        return [f"{donde}: «{fuente}» admite los operadores {permitidos}; llego «{op}»."]
    if op in OPERADORES_SIN_VALOR:
        if "valor" in condicion:
            return [f"{donde}: «{op}» no lleva valor."]
        return []
    if "valor" not in condicion:
        return [f"{donde}: «{op}» necesita un valor."]
    valor = condicion["valor"]
    if tipo == NUM and not _es_numero(valor):
        return [f"{donde}: «{fuente}» es numerica y el valor «{valor}» no."]
    if tipo == TEXTO and not isinstance(valor, str):
        return [f"{donde}: «{fuente}» es de texto y el valor «{valor}» no."]
    return []


def validar_texto(texto: Any, donde: str) -> list[str]:
    """Valida un texto con marcadores ``{fuente}`` o ``{fuente:decimales}``."""
    if not isinstance(texto, str) or not texto.strip():
        return [f"{donde}: falta el texto."]
    return [
        f"{donde}: el marcador «{{{fuente}}}» no es una fuente conocida."
        for fuente, _ in _MARCADOR.findall(texto)
        if fuente not in FUENTES
    ]


def validar_indicador(indicador: Any, donde: str) -> list[str]:
    """Valida un chip de hardware."""
    if not isinstance(indicador, dict) or not str(indicador.get("nombre", "")).strip():
        return [f"{donde}: el indicador necesita un nombre."]
    estados = indicador.get("estados")
    if not isinstance(estados, list) or not estados:
        return [f"{donde}: el indicador necesita al menos un estado."]
    errores = []
    for i, estado in enumerate(estados, 1):
        lugar = f"{donde}, estado #{i}"
        if not isinstance(estado, dict):
            errores.append(f"{lugar}: tiene que ser un objeto.")
            continue
        if estado.get("tono") not in TONOS:
            errores.append(f"{lugar}: el tono tiene que ser uno de {', '.join(sorted(TONOS))}.")
        errores += validar_texto(estado.get("texto"), lugar)
        if estado.get("cuando") is not None:
            errores += validar_condicion(estado["cuando"], lugar)
    return errores


def _validar_boton(boton: Any, donde: str) -> tuple[list[str], str | None]:
    """Valida un boton ``{etiqueta, accion}``; devuelve errores y el comando."""
    if not isinstance(boton, dict) or boton.get("accion") not in ACCIONES:
        return [f"{donde}: el comando no esta permitido en un paso."], None
    if not str(boton.get("etiqueta", "")).strip():
        return [f"{donde}: falta la etiqueta del boton."], boton["accion"]
    return [], boton["accion"]


def validar_accion(accion: Any, donde: str) -> tuple[list[str], set[str]]:
    """Valida un boton o un interruptor.

    Returns:
        Los errores y los comandos que puede mandar.
    """
    if not isinstance(accion, dict):
        return [f"{donde}: la accion tiene que ser un objeto."], set()
    tipo = accion.get("tipo", "boton")
    if tipo == "boton":
        errores, comando = _validar_boton(accion, donde)
        return errores, {comando} if comando else set()
    if tipo != "interruptor":
        return [f"{donde}: el tipo «{tipo}» no existe (boton o interruptor)."], set()

    errores: list[str] = []
    comandos: set[str] = set()
    if accion.get("estado") is None:
        errores.append(f"{donde}: el interruptor necesita la condicion que dice si esta encendido.")
    else:
        errores += validar_condicion(accion["estado"], f"{donde}, estado")
    for lado in ("encender", "apagar"):
        errores_lado, comando = _validar_boton(accion.get(lado), f"{donde}, {lado}")
        errores += errores_lado
        if comando in (INICIAR_REAL, INICIAR_PRUEBA):
            errores.append(
                f"{donde}, {lado}: iniciar un grabado no puede ser parte de un interruptor."
            )
        if comando:
            comandos.add(comando)
    return errores, comandos


def validar_procedimiento(pasos: list[dict]) -> list[str]:
    """Valida un procedimiento completo, tal como llega a la API.

    Los pasos se numeran por su posicion en la lista (1, 2, 3...).

    Args:
        pasos: Los pasos, cada uno con sus validaciones.

    Returns:
        Los errores encontrados; vacio si el procedimiento se puede usar.
    """
    if not pasos:
        return ["El procedimiento no tiene pasos."]
    errores: list[str] = []
    total = len(pasos)
    ultimo_iniciar = ultimo_apagar = 0
    for n, paso in enumerate(pasos, 1):
        lugar = f"Paso {n}"
        validaciones = paso.get("validaciones") or []
        if not validaciones:
            errores.append(f"{lugar}: necesita al menos una validacion.")
        for i, v in enumerate(validaciones, 1):
            donde = f"{lugar}, validacion {i}"
            if v.get("tipo") == TipoValidacion.SENSOR:
                if v.get("condicion") is None:
                    errores.append(f"{donde}: una validacion por sensor necesita su condicion.")
                else:
                    errores += validar_condicion(v["condicion"], donde)
            elif v.get("condicion") is not None:
                errores.append(f"{donde}: una validacion manual no lleva condicion.")

        for i, indicador in enumerate(paso.get("indicadores") or [], 1):
            errores += validar_indicador(indicador, f"{lugar}, indicador {i}")

        for i, accion in enumerate(paso.get("acciones") or [], 1):
            donde = f"{lugar}, accion {i}"
            errores_accion, comandos = validar_accion(accion, donde)
            errores += errores_accion
            if INICIAR_REAL in comandos:
                ultimo_iniciar = n
            if p.APAGAR in comandos:
                ultimo_apagar = n

        if paso.get("condicion_avance") is not None:
            errores += validar_condicion(paso["condicion_avance"], f"{lugar}, condicion de avance")

        for i, salida in enumerate(paso.get("salidas") or [], 1):
            donde = f"{lugar}, salida {i}"
            if not isinstance(salida, dict) or not str(salida.get("etiqueta", "")).strip():
                errores.append(f"{donde}: falta la etiqueta.")
                continue
            destino = salida.get("destino")
            if (
                not isinstance(destino, int)
                or isinstance(destino, bool)
                or not 1 <= destino <= total
            ):
                errores.append(f"{donde}: el destino tiene que ser un paso entre 1 y {total}.")

    # Si el procedimiento graba, tiene que dejar el equipo apagado despues.
    if ultimo_iniciar and ultimo_apagar <= ultimo_iniciar:
        errores.append(
            "El procedimiento inicia un grabado real pero ningun paso posterior "
            "ofrece «Apagar en orden inverso»."
        )
    return errores


def catalogo() -> dict:
    """Devuelve el vocabulario para el editor del front."""
    return {
        "fuentes": [
            {"fuente": f, "tipo": tipo, "descripcion": desc} for f, (tipo, desc) in FUENTES.items()
        ],
        "operadores": {tipo: sorted(ops) for tipo, ops in OPERADORES.items()},
        "acciones": [{"accion": a, "etiqueta": e} for a, e in ACCIONES.items()],
        "tonos": sorted(TONOS),
    }
