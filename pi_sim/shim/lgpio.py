"""Doble de la biblioteca lgpio: cada llamada viaja al contenedor pi-sim.

Solo está en la imagen del simulador. gpiozero, sus dispositivos y LGPIOFactory
corren sin cambios encima de este módulo.
"""

import json
import os
import socket
import threading

from gpiozero.pins import local as _local

SET_ACTIVE_LOW = 4
SET_OPEN_DRAIN = 8
SET_OPEN_SOURCE = 16
SET_PULL_UP = 32
SET_PULL_DOWN = 64
SET_PULL_NONE = 128

RISING_EDGE = 1
FALLING_EDGE = 2
BOTH_EDGES = 3

# Pi 5 de 8 GB: la revisión física que gpiozero no encuentra fuera de la placa.
_local.get_pi_revision = lambda: 0xD04170

_concesiones = {}
_callbacks = {}
_libres = {}
_cerrojo = threading.Lock()


class error(Exception):  # noqa: N801, N818 - mismo nombre que la biblioteca real
    """Excepción que lanza lgpio."""


def _direccion():
    return os.environ.get("PI_SIM_HOST", "pi-sim"), int(os.environ.get("PI_SIM_PUERTO", "9000"))


def _conectar():
    conexion = socket.create_connection(_direccion(), timeout=5)
    conexion.settimeout(None)  # Una llamada nativa colgada no vence por sí sola.
    conexion.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return conexion, conexion.makefile("rb")


def _intercambiar(conexion, lector, op, args):
    conexion.sendall(json.dumps({"op": op, "args": args}).encode() + b"\n")
    linea = lector.readline()
    if not linea:
        raise OSError("el simulador cerró la conexión")
    respuesta = json.loads(linea)
    if "e" in respuesta:
        raise error(respuesta["e"])
    return respuesta["r"]


def _llamar(op, *args):
    """Una conexión por llamada en curso: colgarse no frena a los demás hilos."""
    with _cerrojo:
        libres = _libres.setdefault(_direccion(), [])
        par = libres.pop() if libres else None
    try:
        par = par or _conectar()
        resultado = _intercambiar(*par, op, list(args))
    except OSError as exc:
        if par:
            par[0].close()
        raise error(f"comunicación con el chip perdida: {exc}") from exc
    except error:
        with _cerrojo:
            libres.append(par)
        raise
    with _cerrojo:
        libres.append(par)
    return resultado


def _escuchar_eventos(handle, conexion, lector):
    try:
        for linea in lector:
            gpio, nivel, ticks = json.loads(linea)["ev"]
            bit = RISING_EDGE if nivel else FALLING_EDGE
            for cb in list(_callbacks.get((handle, gpio), ())):
                if cb.borde & bit:
                    cb.funcion(0, gpio, nivel, ticks)
    except (OSError, ValueError):
        pass
    finally:
        conexion.close()


def gpiochip_open(gpiochip):
    """La conexión que queda abierta representa al proceso dueño del chip."""
    if os.path.exists("/dev/gpiochip0"):
        raise error("este doble de lgpio no debe usarse en una placa con chip real")
    try:
        conexion, lector = _conectar()
    except OSError as exc:
        raise error("can not open gpiochip") from exc
    try:
        handle = _intercambiar(conexion, lector, "abrir_chip", [gpiochip])
    except (OSError, error):
        conexion.close()
        raise
    _concesiones[handle] = conexion
    threading.Thread(
        target=_escuchar_eventos, args=(handle, conexion, lector), daemon=True
    ).start()
    return handle


def gpiochip_close(handle):
    """Libera las líneas antes de volver, como la biblioteca real."""
    _llamar("cerrar_chip", handle)
    conexion = _concesiones.pop(handle, None)
    if conexion:
        conexion.shutdown(socket.SHUT_RDWR)
    return 0


def gpio_claim_input(handle, gpio, lFlags=0):  # noqa: N803
    """Reserva una línea como entrada."""
    return _llamar("reclamar_entrada", handle, gpio, lFlags)


def gpio_claim_output(handle, gpio, level=0, lFlags=0):  # noqa: N803
    """Reserva una línea como salida."""
    return _llamar("reclamar_salida", handle, gpio, level, lFlags)


def gpio_claim_alert(handle, gpio, eFlags, lFlags=0, notify_handle=None):  # noqa: N803, ARG001
    """Reserva una línea que informa flancos."""
    return _llamar("reclamar_alerta", handle, gpio, eFlags, lFlags)


def gpio_free(handle, gpio):
    """Libera una línea."""
    return _llamar("liberar", handle, gpio)


def gpio_get_mode(handle, gpio):
    """Devuelve los bits de modo de la línea."""
    return _llamar("modo", handle, gpio)


def gpio_read(handle, gpio):
    """Lee el nivel de la línea."""
    return _llamar("leer", handle, gpio)


def gpio_write(handle, gpio, level):
    """Escribe el nivel de una salida."""
    return _llamar("escribir", handle, gpio, int(level))


def gpio_set_debounce_micros(handle, gpio, debounce_micros):
    """Configura el antirrebote."""
    return _llamar("debounce", handle, gpio, debounce_micros)


class callback:  # noqa: N801 - mismo nombre que la biblioteca real
    """Suscripción a los flancos de una línea."""

    def __init__(self, handle, gpio, edge=RISING_EDGE, func=None):
        """Registra la función a invocar en cada flanco."""
        self.clave, self.borde, self.funcion = (handle, gpio), edge, func
        _callbacks.setdefault(self.clave, []).append(self)

    def cancel(self):
        """Deja de recibir flancos."""
        if self in _callbacks.get(self.clave, ()):
            _callbacks[self.clave].remove(self)


def tx_pwm(handle, gpio, frequency, duty, offset=0, cycles=0):  # noqa: ARG001
    """Ningún driver del catálogo usa PWM."""
    raise error("PWM no implementado en el simulador")


def spi_open(spiDev, spiChan, baud, spiFlags=0):  # noqa: N803, ARG001
    """La Pi no monta /dev/spidev*: gpiozero cae a SPI por software."""
    raise error("can not open SPI device")


def spi_close(handle):  # noqa: ARG001
    """Sin dispositivos SPI abiertos no hay nada que cerrar."""
    raise error("bad SPI handle")


spi_read = spi_write = spi_xfer = spi_close
