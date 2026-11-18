"""Roles: qué componente alimenta cada tarjeta del dashboard (puro, sin Django ni GPIO)."""

ROLES = {
    "TEMPERATURA_AGUA": {"grupo": "variables", "campos": ["temperatura_c"], "clase": "numero"},
    "CAUDAL_REFRIGERANTE": {
        "grupo": "variables",
        "campos": ["caudal", "tension_v"],
        "clase": "numero",
    },
    "TENSION_AT": {"grupo": "variables", "campos": ["tension_kv", "tension_v"], "clase": "numero"},
    "POSICION_MOTOR": {"grupo": "variables", "campos": ["posicion"], "clase": "numero"},
    "TEMPERATURA_LASER": {
        "grupo": "extra",
        "clave": "temperatura_laser",
        "campos": ["temperatura_c"],
        "clase": "numero",
    },
    "SENSOR_SOMBRA": {
        "grupo": "extra",
        "clave": "sensor_sombra_mw",
        "campos": ["potencia_mw", "tension_v"],
        "clase": "numero",
    },
    "LASER": {"grupo": "actuadores", "campos": ["salida_activa"], "clase": "booleano"},
    "SHUTTER": {"grupo": "actuadores", "campos": ["armado", "salida_apertura"], "clase": "booleano"},
}

# Antigüedad máxima de una muestra para publicarla en el dashboard.
VIGENCIA_S = 3.0

_VARIABLES = (
    "TEMPERATURA_AGUA",
    "CAUDAL_REFRIGERANTE",
    "POTENCIA_LASER",
    "TENSION_AT",
    "CORRIENTE_AT",
    "POSICION_MOTOR",
    "TEMPERATURA_AMBIENTE",
)


def campo_de(rol, campos_driver):
    """Primer campo aceptado por el rol que el driver declara; None si no hay."""
    for campo in ROLES[rol]["campos"]:
        if campo in campos_driver:
            return campo
    return None


def _valor(muestra, campo, ahora):
    """Valor de la muestra solo si es reciente y de calidad OK; si no, None."""
    if not muestra or muestra.get("calidad") != "OK":
        return None
    instante = muestra.get("monotonic")
    if instante is None or ahora - instante > VIGENCIA_S:
        return None
    return (muestra.get("valores") or {}).get(campo)


def resolver(config, muestras, ahora):
    """Publica cada rol asignado desde su muestra; lo ausente queda en None."""
    from controller.hardware_config import catalogo

    fichas = catalogo()
    salida = {
        "variables": dict.fromkeys(_VARIABLES),
        "actuadores": {"laser": None, "shutter_armado": None, "shutter_abierto": None},
        "senales": {"FIBRA_ALINEADA": None, "SHUTTER_ABIERTO": None, "SHUTTER_CERRADO": None},
        "extra": {"sensor_sombra_mw": None, "temperatura_laser": None},
    }
    for nombre, comp in config.get("componentes", {}).items():
        rol = comp.get("rol")
        if comp.get("estado") != "Vigente" or rol not in ROLES:
            continue
        ficha, muestra = ROLES[rol], muestras.get(nombre)
        campos = fichas.get(comp.get("tipo"), {}).get("campos", {})
        if rol == "SHUTTER":
            armado = _valor(muestra, "armado", ahora) if "armado" in campos else None
            abierto = (
                _valor(muestra, "salida_apertura", ahora) if "salida_apertura" in campos else None
            )
            salida["actuadores"]["shutter_armado"] = armado
            salida["actuadores"]["shutter_abierto"] = abierto
            salida["senales"]["SHUTTER_ABIERTO"] = abierto
            continue
        campo = campo_de(rol, campos)
        valor = _valor(muestra, campo, ahora) if campo else None
        if ficha["clase"] == "numero" and valor is not None:
            valor = valor * comp.get("ganancia", 1.0) + comp.get("offset", 0.0)
        clave = ficha.get("clave") or ("laser" if rol == "LASER" else rol)
        salida[ficha["grupo"]][clave] = valor
    return salida
