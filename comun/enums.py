"""Enumeraciones del dominio.

Corresponden una a una con el paquete «Enumeraciones» del Diagrama de Clases.
Se usan como ``choices`` de los modelos: guardar el valor como texto y no como
entero hace que un ``SELECT`` crudo sobre MySQL siga siendo legible, lo cual
importa cuando se diagnostica un ensayo que salio mal.
"""

from django.db import models


class TipoRol(models.TextChoices):
    """Rol funcional del usuario dentro del sistema."""

    OPERADOR = "OPERADOR", "Operador"
    INVESTIGADOR = "INVESTIGADOR", "Investigador"
    ADMINISTRADOR = "ADMINISTRADOR", "Administrador"


class TipoRed(models.TextChoices):
    """Familia de red de periodo largo que se graba."""

    LPG = "LPG", "LPG"
    HLPG = "HLPG", "HLPG"
    PHLPG = "pHLPG", "pHLPG"


class EstadoRed(models.TextChoices):
    """Resultado de la caracterizacion de una red fabricada."""

    VIABLE = "VIABLE", "Viable"
    INVIABLE = "INVIABLE", "Inviable"
    ROTA = "ROTA", "Rota"


class EstadoEjecucion(models.TextChoices):
    """Estado de una corrida de fabricacion."""

    EN_CURSO = "EN_CURSO", "En curso"
    COMPLETADO = "COMPLETADO", "Completado"
    INTERRUMPIDO = "INTERRUMPIDO", "Interrumpido"
    ABORTADO = "ABORTADO", "Abortado"


class ModoEjecucion(models.TextChoices):
    """Modo en que se ejecuta un programa.

    En ``PRUEBA`` se recorre el programa sin grabar: no se produce ninguna red.
    """

    REAL = "REAL", "Real"
    PRUEBA = "PRUEBA", "Prueba"


class CriterioFin(models.TextChoices):
    """Condicion que da por terminada la fabricacion."""

    CANT_MARCAS = "CANT_MARCAS", "Cantidad de marcas"
    LONGITUD = "LONGITUD", "Longitud"
    TIEMPO = "TIEMPO", "Tiempo"
    ATENUACION_OBJETIVO = "ATENUACION_OBJETIVO", "Atenuacion objetivo"
    MANUAL = "MANUAL", "Manual"


class TipoCheckpoint(models.TextChoices):
    """Disparador que origina un punto de control."""

    TIEMPO = "TIEMPO", "Por tiempo"
    MARCA = "MARCA", "Por marca"


class TipoPeriodo(models.TextChoices):
    """Perfil de variacion del periodo a lo largo de la red.

    El Diagrama de Clases lo modela como jerarquia (``Periodo`` abstracta con
    tres subclases). Aca se aplana a una sola tabla con discriminador: las
    subclases aportan cero y un atributo, y la herencia multitabla costaria un
    JOIN por cada lectura de programa a cambio de nada.
    """

    CONSTANTE = "CONSTANTE", "Constante"
    LINEAL = "LINEAL", "Lineal"
    EXPONENCIAL = "EXPONENCIAL", "Exponencial"


class EstadoSistema(models.TextChoices):
    """Estado de la maquina. Mutuamente excluyentes.

    Las condiciones de armado NO viven aca: ver :class:`CondicionSeguridad`.
    Mezclarlas haria imposible representar que el sistema esta GRABANDO *y* con
    la fibra alineada al mismo tiempo, que es lo que ocurre fisicamente.
    """

    REPOSO = "REPOSO", "En reposo"
    PREPARANDO = "PREPARANDO", "Preparando"
    LISTO = "LISTO", "Listo para grabar"
    GRABANDO = "GRABANDO", "Grabando"
    EMERGENCIA = "EMERGENCIA", "Emergencia"


class CondicionSeguridad(models.TextChoices):
    """Checklist de armado. NO son excluyentes.

    Las cuatro deben cumplirse para que el sistema pueda pasar de
    ``PREPARANDO`` a ``LISTO``.
    """

    REFRIGERACION_OK = "REFRIGERACION_OK", "Refrigeracion OK"
    AT_ENCENDIDA = "AT_ENCENDIDA", "Alta tension encendida"
    FIBRA_ALINEADA = "FIBRA_ALINEADA", "Fibra alineada"
    SHUTTER_ARMADO = "SHUTTER_ARMADO", "Shutter armado"


class VariableMonitoreada(models.TextChoices):
    """Magnitudes analogicas que el lazo de control muestrea (ADR-0007).

    Son las unicas que admiten ``Umbral``: las senales digitales
    (fibra alineada, shutter abierto/cerrado) son interlocks booleanos y se
    evaluan como checklist, no como rango.
    """

    TEMPERATURA_AGUA = "TEMPERATURA_AGUA", "Temperatura del agua [C]"
    CAUDAL_REFRIGERANTE = "CAUDAL_REFRIGERANTE", "Caudal de refrigerante [L/min]"
    POTENCIA_LASER = "POTENCIA_LASER", "Potencia del laser [W]"
    TENSION_AT = "TENSION_AT", "Tension de alta tension [kV]"
    CORRIENTE_AT = "CORRIENTE_AT", "Corriente de alta tension [mA]"
    POSICION_MOTOR = "POSICION_MOTOR", "Posicion del motor [mm]"
    TEMPERATURA_AMBIENTE = "TEMPERATURA_AMBIENTE", "Temperatura ambiente [C]"


class Severidad(models.TextChoices):
    """Gravedad de una alerta."""

    INFO = "INFO", "Informativa"
    ADVERTENCIA = "ADVERTENCIA", "Advertencia"
    CRITICA = "CRITICA", "Critica"


class AccionUmbral(models.TextChoices):
    """Que hace el sistema cuando una lectura cae fuera del umbral."""

    ALERTA = "ALERTA", "Solo alertar"
    BLOQUEAR_COMANDO = "BLOQUEAR_COMANDO", "Bloquear comandos"
    PARADA_EMERGENCIA = "PARADA_EMERGENCIA", "Parada de emergencia"


class OrigenEmergencia(models.TextChoices):
    """Quien disparo la parada de emergencia.

    ``BOTON_FISICO`` es el interlock por hardware: corta el enable de la fuente
    HV y el shutter electricamente, sin software en el camino (ADR-0002). El
    sistema lo *registra*, no lo ejecuta.
    """

    BOTON_FISICO = "BOTON_FISICO", "Boton fisico"
    UMBRAL = "UMBRAL", "Umbral excedido"
    OPERADOR = "OPERADOR", "Operador"


class AccionAuditoria(models.TextChoices):
    """Tipo de accion que queda asentada en la auditoria (RN010)."""

    LOGIN = "LOGIN", "Inicio de sesion"
    CREAR = "CREAR", "Creacion"
    MODIFICAR = "MODIFICAR", "Modificacion"
    ELIMINAR = "ELIMINAR", "Eliminacion"
    COMANDAR = "COMANDAR", "Comando a hardware"
    EXPORTAR = "EXPORTAR", "Exportacion"
