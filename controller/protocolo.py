"""Contrato del bus entre Django y el proceso controlador (ADR-0004, ADR-0009).

Es la unica fuente de verdad de los nombres de canales, claves y comandos.
No importa nada mas que la biblioteca estandar: lo usa el controlador, que no
puede arrastrar Django (ADR-0002), y lo importa Django desde aca para no
duplicar los nombres.

Tres mecanismos, cada uno para lo que sirve:

====================  ===============  ==============================================
Que                   Mecanismo        Por que
====================  ===============  ==============================================
Comandos              Stream           Un comando perdido es una orden que no ocurrio.
                                       El stream la conserva hasta que se lee.
Eventos               Stream + grupo   Marcas, checkpoints y emergencias se persisten:
                                       el grupo de consumo garantiza que se procesan
                                       aunque el servicio de eventos se reinicie.
Telemetria            Pub/sub          Efimera (ADR-0007): el dato viejo no sirve.
Configuracion         Clave            Los umbrales vigentes, que Django publica y el
                                       controlador lee sin tocar MySQL.
====================  ===============  ==============================================
"""

# ── Canales y claves (todos en la base de pub/sub de Redis) ──────────────────

STREAM_COMANDOS = "co2:comandos"
STREAM_EVENTOS = "co2:eventos"
CANAL_TELEMETRIA = "co2:telemetria"
CLAVE_TELEMETRIA = "co2:telemetria:ultima"
CLAVE_CONFIGURACION = "co2:configuracion"
GRUPO_EVENTOS = "persistencia"

# La ultima telemetria vive unos segundos: si el controlador muere, la clave
# vence y nadie lee un estado viejo como si fuera actual.
TTL_TELEMETRIA = 5

# Largo maximo de los streams. Son colas de transito, no historico.
MAXLEN_STREAM = 10_000

# ── Comandos ─────────────────────────────────────────────────────────────────

ENCENDER_REFRIGERACION = "encender_refrigeracion"
APAGAR_REFRIGERACION = "apagar_refrigeracion"
HABILITAR_AT = "habilitar_at"
DESHABILITAR_AT = "deshabilitar_at"
ALINEAR_FIBRA = "alinear_fibra"
ARMAR_SHUTTER = "armar_shutter"
DESARMAR_SHUTTER = "desarmar_shutter"
ENCENDER_LASER = "encender_laser"
APAGAR_LASER = "apagar_laser"
MOVER_MOTOR = "mover_motor"
INICIAR = "iniciar"
ABORTAR = "abortar"
APAGAR = "apagar"
EMERGENCIA = "emergencia"
REARMAR = "rearmar"
INYECTAR_FALLA = "inyectar_falla"

# Los que se pueden mandar sueltos desde la API. INICIAR va por su propio
# endpoint porque necesita crear el registro del ensayo antes.
COMANDOS_SIMPLES = {
    ENCENDER_REFRIGERACION,
    APAGAR_REFRIGERACION,
    HABILITAR_AT,
    DESHABILITAR_AT,
    ALINEAR_FIBRA,
    ARMAR_SHUTTER,
    DESARMAR_SHUTTER,
    ENCENDER_LASER,
    APAGAR_LASER,
    MOVER_MOTOR,
    ABORTAR,
    APAGAR,
    EMERGENCIA,
    REARMAR,
    INYECTAR_FALLA,
}

# Los que energizan o desenergizan el sistema: se auditan como criticos (RN010).
COMANDOS_CRITICOS = {
    HABILITAR_AT,
    ENCENDER_LASER,
    INICIAR,
    EMERGENCIA,
    REARMAR,
    INYECTAR_FALLA,
}

# Recorrido util del desplazador lineal, en mm.
CARRERA_MOTOR_MM = 300.0

# ── Fallas simulables (solo con el HAL simulado) ─────────────────────────────

FALLA_NINGUNA = "ninguna"
FALLA_SOBRETEMPERATURA = "sobretemperatura"
FALLA_CAUDAL = "caudal"
FALLAS = {FALLA_NINGUNA, FALLA_SOBRETEMPERATURA, FALLA_CAUDAL}

# ── Eventos ──────────────────────────────────────────────────────────────────

EV_MARCA = "marca"
EV_CHECKPOINT = "checkpoint"
EV_ALERTA = "alerta"
EV_EMERGENCIA = "emergencia"
EV_FIN = "fin"
EV_ABORTADO = "abortado"
