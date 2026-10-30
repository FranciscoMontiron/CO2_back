"""Procedimiento guiado estandar: el grabado de LPG con laser de CO2.

Son los nueve pasos que «Nuevo ensayo» tenia escritos en el front, pasados a
datos. Cada instruccion general del paso quedo como el *detalle* de la
validacion a la que corresponde, y los sensores, botones y chips son los
mismos que antes.

Lo carga ``cargar_datos_iniciales`` y se asigna a los programas A, B y C.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.db import transaction

from comun.enums import TipoRed, TipoValidacion

from .models import ProcedimientoGuiado
from .serializers import ProcedimientoGuiadoSerializer

CODIGO_ESTANDAR = "PROC-001"


# ── Atajos para escribir condiciones ────────────────────────────────────────


def si(fuente: str) -> dict:
    """La fuente booleana es verdadera."""
    return {"fuente": fuente, "op": "es_verdadero"}


def no(fuente: str) -> dict:
    """La fuente booleana es falsa."""
    return {"fuente": fuente, "op": "es_falso"}


def cmp(fuente: str, op: str, valor) -> dict:
    """Compara la fuente con un valor."""
    return {"fuente": fuente, "op": op, "valor": valor}


def todas(*condiciones: dict) -> dict:
    """Todas las condiciones (Y)."""
    return {"todas": list(condiciones)}


def alguna(*condiciones: dict) -> dict:
    """Alguna de las condiciones (O)."""
    return {"alguna": list(condiciones)}


def estado(texto: str, tono: str, cuando: dict | None = None) -> dict:
    """Un estado de un indicador."""
    return {"cuando": cuando, "texto": texto, "tono": tono}


def manual(texto: str, detalle: str) -> dict:
    """Validacion que tilda el operador."""
    return {"texto": texto, "detalle": detalle, "tipo": TipoValidacion.MANUAL, "condicion": None}


def sensor(texto: str, detalle: str, condicion: dict) -> dict:
    """Validacion que confirma un sensor."""
    return {
        "texto": texto,
        "detalle": detalle,
        "tipo": TipoValidacion.SENSOR,
        "condicion": condicion,
    }


def boton(accion: str, etiqueta: str) -> dict:
    """Boton que manda un comando."""
    return {"accion": accion, "etiqueta": etiqueta}


# ── El procedimiento ────────────────────────────────────────────────────────

PASOS_ESTANDAR: list[dict] = [
    {
        "titulo": "Montaje y conexiones",
        "subtitulo": "Fibra e interrogador óptico",
        "fase": "Preparación",
        "indicadores": [
            {"nombre": "Fibra montada", "estados": [estado("VERIFICAR", "info")]},
            {"nombre": "Interrogador óptico", "estados": [estado("CONECTADO", "ok")]},
        ],
        "validaciones": [
            manual(
                "Fibra posicionada y bajo tensión",
                "Colocar la fibra en el arreglo de grabado y tensarla sin exceder el límite "
                "mecánico.",
            ),
            # El interrogador no tiene sensor propio todavia: se tildan con la
            # conexion al controlador, como lo hacia la version anterior del front.
            sensor(
                "Interrogador óptico conectado",
                "Conectar el interrogador óptico al puerto del arreglo y verificar el enlace.",
                si("controlador.conectado"),
            ),
            sensor(
                "Comunicación establecida",
                "Confirmar que la comunicación óptica se establece antes de continuar.",
                si("controlador.conectado"),
            ),
        ],
    },
    {
        "titulo": "Refrigeración",
        "subtitulo": "Estabilización térmica del láser",
        "fase": "Preparación",
        "acciones": [boton("encender_refrigeracion", "Encender refrigeración")],
        "indicadores": [
            {
                "nombre": "Chiller",
                "estados": [
                    estado("ENCENDIDO", "ok", si("actuadores.refrigeracion")),
                    estado("APAGADO", "neutral"),
                ],
            },
            {
                "nombre": "Caudal",
                "estados": [
                    estado(
                        "{variables.CAUDAL_REFRIGERANTE:2} L/min",
                        "ok",
                        cmp("variables.CAUDAL_REFRIGERANTE", ">=", 2),
                    ),
                    estado("{variables.CAUDAL_REFRIGERANTE:2} L/min", "warn"),
                ],
            },
        ],
        "condicion_avance": si("condiciones.REFRIGERACION_OK"),
        "validaciones": [
            sensor(
                "Refrigeración encendida",
                "Activar el circuito de refrigeración del láser de CO₂ con el botón "
                "«Encender refrigeración».",
                si("actuadores.refrigeracion"),
            ),
            sensor(
                "Caudal dentro de umbral",
                "Verificar caudal de refrigerante dentro de 1.8–3.0 L/min.",
                cmp("variables.CAUDAL_REFRIGERANTE", ">=", 2),
            ),
            sensor(
                "Temperatura estabilizada",
                "Esperar temperatura de refrigeración estable (~18–22 °C) antes de habilitar "
                "alta tensión.",
                si("condiciones.REFRIGERACION_OK"),
            ),
        ],
    },
    {
        "titulo": "Parámetros base",
        "subtitulo": "Configuración del programa de grabado",
        "fase": "Parámetros",
        "indicadores": [
            {
                "nombre": "Programa cargado",
                "estados": [
                    estado("{programa.nombre}", "ok", si("programa.seleccionado")),
                    estado("SIN PROGRAMA", "warn"),
                ],
            },
            {
                "nombre": "Criterio de fin",
                "estados": [
                    estado(
                        "{programa.distancia_mm:2} mm · {programa.pulsos} pulsos",
                        "info",
                        si("programa.seleccionado"),
                    ),
                    estado("—", "neutral"),
                ],
            },
        ],
        "condicion_avance": si("programa.seleccionado"),
        "validaciones": [
            sensor(
                "Programa seleccionado y válido",
                "Elegir el programa de grabado en el panel «Programa de grabado» (o crear uno "
                "desde «Programas de grabado» si el rol lo permite).",
                si("programa.seleccionado"),
            ),
            manual(
                "Parámetros dentro de umbrales de seguridad",
                "Verificar potencia objetivo, duración de pulso y desplazamiento por pulso.",
            ),
            sensor(
                "Criterio de finalización definido",
                "Definir el criterio de finalización (distancia total o cantidad de pulsos).",
                si("programa.tiene_criterio_fin"),
            ),
        ],
    },
    {
        "titulo": "Fuente de alta tensión",
        "subtitulo": "Energización de la fuente HV",
        "fase": "Verificación",
        "acciones": [boton("habilitar_at", "Habilitar alta tensión")],
        "indicadores": [
            {"nombre": "Interlock HV", "estados": [estado("VERIFICAR", "info")]},
            {
                "nombre": "Fuente HV (SW)",
                "estados": [
                    estado(
                        "HABILITADA {variables.TENSION_AT:1} kV",
                        "ok",
                        si("condiciones.AT_ENCENDIDA"),
                    ),
                    estado(
                        "RAMPA {variables.TENSION_AT:1} kV",
                        "neutral",
                        si("actuadores.alta_tension"),
                    ),
                    estado("APAGADA", "neutral"),
                ],
            },
        ],
        "condicion_avance": si("condiciones.AT_ENCENDIDA"),
        "validaciones": [
            manual(
                "Interlock físico cerrado",
                "Habilitar el interlock físico de la fuente de alta tensión.",
            ),
            sensor(
                "Fuente HV habilitada por software",
                "Confirmar el encendido desde software con «Habilitar alta tensión».",
                si("condiciones.AT_ENCENDIDA"),
            ),
            manual(
                "Semáforo en amarillo intermitente",
                "El semáforo debe parpadear en amarillo. Verificar que el voltaje se mantenga "
                "en 0 V hasta la activación del láser.",
            ),
        ],
    },
    {
        "titulo": "Sensor de sombra",
        "subtitulo": "Alineación de la fibra respecto al láser",
        "fase": "Verificación",
        "acciones": [boton("alinear_fibra", "Alinear fibra")],
        "indicadores": [
            {
                "nombre": "Sensor de sombra",
                "estados": [
                    estado("ALINEADO", "ok", si("condiciones.FIBRA_ALINEADA")),
                    estado("ALINEANDO…", "info", si("actuadores.alineando")),
                    estado("NO ALINEADO", "warn"),
                ],
            },
            {
                "nombre": "Lectura PD",
                "estados": [
                    estado(
                        "{extra.sensor_sombra_mw:2} mW",
                        "ok",
                        cmp("extra.sensor_sombra_mw", "<", 0.2),
                    ),
                    estado("{extra.sensor_sombra_mw:2} mW", "neutral"),
                ],
            },
        ],
        "condicion_avance": si("condiciones.FIBRA_ALINEADA"),
        "validaciones": [
            sensor(
                "Sensor de sombra activo",
                "Activar el fotodetector de sombra (PD) con «Alinear fibra». Si no detecta "
                "potencia, el láser impacta sobre la fibra.",
                alguna(si("actuadores.alineando"), si("condiciones.FIBRA_ALINEADA")),
            ),
            sensor(
                "Fibra alineada (potencia residual mínima)",
                "Alinear la fibra hasta que la lectura de potencia residual sea mínima.",
                si("condiciones.FIBRA_ALINEADA"),
            ),
            sensor(
                "Lectura PD dentro de umbral de alineación",
                "Confirmar estado «Alineado» antes de armar el shutter: la lectura del PD "
                "tiene que quedar por debajo de 0,2 mW.",
                cmp("extra.sensor_sombra_mw", "<", 0.2),
            ),
        ],
    },
    {
        "titulo": "Shutter mecánico",
        "subtitulo": "Brazo que obtura el paso del láser",
        "fase": "Verificación",
        "acciones": [boton("armar_shutter", "Armar shutter")],
        "indicadores": [
            {
                "nombre": "Shutter",
                "estados": [
                    estado("ARMADO", "ok", si("actuadores.shutter_armado")),
                    estado("DESARMADO", "warn"),
                ],
            },
            {
                "nombre": "Fin de carrera",
                "estados": [
                    estado("CERRADO DETECTADO", "ok", si("senales.SHUTTER_CERRADO")),
                    estado("ABIERTO", "danger"),
                ],
            },
        ],
        "condicion_avance": cmp("estado", "==", "LISTO"),
        "validaciones": [
            sensor(
                "Shutter armado",
                "Posicionar el brazo mecánico en la trayectoria del haz con «Armar shutter».",
                si("actuadores.shutter_armado"),
            ),
            sensor(
                "Cierre confirmado por fin de carrera",
                "Dejar el shutter en estado Cerrado hasta el inicio del loop de grabado: el "
                "fin de carrera tiene que detectar el cierre.",
                todas(si("actuadores.shutter_armado"), si("senales.SHUTTER_CERRADO")),
            ),
            manual(
                "Tiempo de apertura dentro de especificación",
                "Verificar que el shutter cierre y abra en el tiempo nominal.",
            ),
        ],
    },
    {
        "titulo": "Láser y lazo de control",
        "subtitulo": "Emisión y estabilización de potencia",
        "fase": "Grabado",
        "acciones": [boton("encender_laser", "Encender láser y cerrar lazo")],
        "indicadores": [
            {
                "nombre": "Láser CO₂",
                "estados": [
                    estado(
                        "ENCENDIDO {variables.POTENCIA_LASER:1} W",
                        "ok",
                        si("actuadores.laser"),
                    ),
                    estado("APAGADO", "neutral"),
                ],
            },
            {
                "nombre": "Lazo de control",
                "estados": [
                    estado("CERRADO", "ok", si("actuadores.lazo_cerrado")),
                    estado("ABIERTO", "warn"),
                ],
            },
        ],
        "condicion_avance": si("actuadores.laser"),
        "validaciones": [
            sensor(
                "Secuencia previa completa",
                "Confirmar que todos los pasos previos están validados: la máquina de estados "
                "tiene que estar en LISTO.",
                cmp("estado", "==", "LISTO"),
            ),
            sensor(
                "Láser encendido",
                "Activar el láser de CO₂ con «Encender láser y cerrar lazo». El semáforo debe "
                "pasar a rojo fijo.",
                si("actuadores.laser"),
            ),
            sensor(
                "Lazo de control cerrado y potencia estable",
                "Cerrar el lazo de control para mantener estable la potencia objetivo.",
                si("actuadores.lazo_cerrado"),
            ),
        ],
    },
    {
        "titulo": "Loop de grabado",
        "subtitulo": "Pulsos + desplazamiento lineal",
        "fase": "Grabado",
        "acciones": [
            boton("iniciar_prueba", "Pre-grabado (láser inhibido)"),
            boton("iniciar_real", "Iniciar grabado"),
            boton("abortar", "Abortar"),
        ],
        "indicadores": [
            {
                "nombre": "Loop",
                "estados": [
                    estado(
                        "PRE-GRABADO",
                        "info",
                        todas(
                            cmp("estado", "==", "GRABANDO"), cmp("ejecucion.modo", "==", "PRUEBA")
                        ),
                    ),
                    estado("GRABANDO", "info", cmp("estado", "==", "GRABANDO")),
                    estado("{ensayo.estado}", "ok", si("ensayo.completado")),
                    estado("{ensayo.estado}", "danger", si("ensayo.terminado")),
                    estado("DETENIDO", "neutral"),
                ],
            },
            {
                "nombre": "Progreso",
                "estados": [
                    estado(
                        "{ejecucion.pulso} / {ejecucion.pulsos} pulsos",
                        "info",
                        si("ejecucion.activa"),
                    ),
                    estado("0 / {programa.pulsos} pulsos", "neutral"),
                ],
            },
        ],
        "condicion_avance": todas(si("ensayo.terminado"), cmp("ensayo.modo", "==", "REAL")),
        "validaciones": [
            manual(
                "Pre-grabado ejecutado (láser inhibido) o omitido a propósito",
                "Opcional: «Pre-grabado (láser inhibido)» recorre el programa sin abrir el "
                "shutter, para verificar el desplazamiento antes de grabar.",
            ),
            sensor(
                "Primer pulso ejecutado correctamente",
                "Con «Iniciar grabado» arranca el loop: en cada pulso el shutter abre durante "
                "la duración configurada (p. ej. 400 ms) y la fibra se desplaza la distancia "
                "configurada (p. ej. 550 µm).",
                alguna(cmp("ejecucion.pulso", ">=", 1), si("ensayo.terminado")),
            ),
            sensor(
                "Criterio de finalización alcanzado",
                "El loop se repite hasta cumplir el criterio de finalización (distancia total "
                "o pulsos). Para cortarlo antes, usar «Abortar».",
                si("ensayo.completado"),
            ),
        ],
    },
    {
        "titulo": "Finalización",
        "subtitulo": "Apagado en orden inverso",
        "fase": "Finalización",
        "acciones": [boton("apagar", "Apagar en orden inverso")],
        "indicadores": [
            {
                "nombre": "Láser",
                "estados": [
                    estado(
                        "APAGADO",
                        "ok",
                        todas(no("actuadores.laser"), no("actuadores.alta_tension")),
                    ),
                    estado("ENCENDIDO", "danger"),
                ],
            },
            {
                "nombre": "Registro de ensayo",
                "estados": [
                    estado("{ensayo.registro}", "ok", si("ensayo.terminado")),
                    estado("SIN GUARDAR", "neutral"),
                ],
            },
        ],
        "condicion_avance": cmp("estado", "==", "REPOSO"),
        "validaciones": [
            sensor(
                "Láser y HV apagados",
                "Con «Apagar en orden inverso» se abre el lazo y se apaga el láser primero.",
                todas(no("actuadores.laser"), no("actuadores.alta_tension")),
            ),
            sensor(
                "Actuadores en posición segura",
                "Después se deshabilitan alta tensión, shutter y sensor de sombra, en ese orden.",
                no("actuadores.shutter_armado"),
            ),
            sensor(
                "Ensayo registrado en base de datos",
                "La refrigeración se apaga al final. Confirmar que el ensayo quedó persistido.",
                si("ensayo.terminado"),
            ),
        ],
    },
]


@transaction.atomic
def cargar_procedimiento_estandar(autor=None) -> tuple[ProcedimientoGuiado, bool]:
    """Crea el procedimiento estandar si todavia no existe.

    Pasa por el serializador de la API para que la semilla cumpla las mismas
    reglas que un procedimiento cargado desde el front.

    Args:
        autor: Usuario que figura como creador, o ``None``.

    Returns:
        El procedimiento y si se creo ahora.
    """
    existente = ProcedimientoGuiado.objects.filter(codigo=CODIGO_ESTANDAR).first()
    if existente:
        return existente, False
    serializador = ProcedimientoGuiadoSerializer(
        data={
            "nombre": "Grabado LPG con láser de CO₂",
            "descripcion": "Procedimiento estándar del arreglo de grabado: armado, loop de "
            "grabado y apagado en orden inverso.",
            "tipo_red": TipoRed.LPG,
            "pasos": PASOS_ESTANDAR,
        },
        context={"request": SimpleNamespace(user=autor) if autor else None},
    )
    serializador.is_valid(raise_exception=True)
    procedimiento = serializador.save()
    # El codigo correlativo seria PROC-001 en una base vacia; se fija igual para
    # que la semilla sea reconocible aunque ya existan otros procedimientos.
    if procedimiento.codigo != CODIGO_ESTANDAR:
        procedimiento.codigo = CODIGO_ESTANDAR
        procedimiento.save(update_fields=["codigo"])
    return procedimiento, True
