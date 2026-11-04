"""Drivers físicos para puesta en marcha manual; GPIO se importa solo en la Pi."""

import os
import time

from controller.factory import BaseComponente, registrar_componente


class GpioBackend:
    """Adaptador gpiozero/lgpio para Pi 5, sustituible en pruebas."""

    def __init__(self):
        """Inicializa el estado y las dependencias de la instancia."""
        from gpiozero.pins.lgpio import LGPIOFactory

        self.factory = LGPIOFactory(chip=int(os.environ.get("GPIO_CHIP", "0")))

    def salida(self, pin, activo_alto):
        """Crea una salida inicialmente inactiva con la polaridad indicada."""
        from gpiozero import OutputDevice

        return OutputDevice(
            pin, active_high=activo_alto, initial_value=False, pin_factory=self.factory
        )

    def entrada(self, pin, pull_up):
        """Crea una entrada digital con la resistencia indicada."""
        from gpiozero import DigitalInputDevice

        return DigitalInputDevice(pin, pull_up=pull_up, pin_factory=self.factory)

    def adc(self, canal, dispositivo, vref):
        """Abre un canal del conversor MCP3008."""
        from gpiozero import MCP3008

        return MCP3008(
            channel=canal, device=dispositivo, max_voltage=vref, pin_factory=self.factory
        )

    def cerrar(self):
        """Libera los recursos del dispositivo."""
        self.factory.close()


@registrar_componente("SALIDA_GPIO")
class SalidaGPIO(BaseComponente):
    """Salida lógica con polaridad configurable y apagado al inicializar."""

    def inicializar(self):
        """Reserva recursos e inicializa el dispositivo en estado seguro."""
        self.pin = self.salida("pin_señal", self.parametros["activo_alto"])

    def ejecutar(self, operacion, argumentos):
        """Aplica una operación previamente validada por el gestor."""
        if operacion == "encender":
            self.pin.on()
        else:
            self.pin.off()

    def leer(self):
        """Obtiene valores y distingue órdenes de realimentación física."""
        return {"salida_activa": bool(self.pin.value), "realimentacion": False}

    def seguro(self):
        """Indica si el dispositivo está detenido para reconfigurarlo."""
        return not self.pin.value

    def apagar(self):
        """Desactiva las salidas y descarta el estado de armado."""
        if hasattr(self, "pin"):
            self.pin.off()


@registrar_componente("SHUTTER_GPIO")
class ShutterGPIO(SalidaGPIO):
    """Orden eléctrica de shutter; no inventa un sensor de posición."""

    def inicializar(self):
        """Reserva recursos e inicializa el dispositivo en estado seguro."""
        self.armado = False
        super().inicializar()

    def ejecutar(self, operacion, argumentos):
        """Aplica una operación previamente validada por el gestor."""
        if operacion == "armar":
            self.pin.off()
            self.armado = True
        elif operacion == "abrir":
            if not self.armado:
                raise ValueError("Arme el shutter antes de abrirlo.")
            self.pin.on()
        elif operacion == "cerrar":
            self.pin.off()
        elif operacion == "desarmar":
            self.apagar()

    def leer(self):
        """Obtiene valores y distingue órdenes de realimentación física."""
        return {
            "armado": self.armado,
            "salida_apertura": bool(self.pin.value),
            "realimentacion": False,
        }

    def seguro(self):
        """Indica si el dispositivo está detenido para reconfigurarlo."""
        return not self.armado and super().seguro()

    def apagar(self):
        """Desactiva las salidas y descarta el estado de armado."""
        super().apagar()
        self.armado = False


@registrar_componente("ENTRADA_GPIO")
class EntradaGPIO(BaseComponente):
    """Entrada digital con resistencia interna configurable."""

    def inicializar(self):
        """Reserva recursos e inicializa el dispositivo en estado seguro."""
        self.pin = self.backend.entrada(self.pines["pin_señal"], self.parametros["pull_up"])
        self.dispositivos.append(self.pin)

    def leer(self):
        """Obtiene valores y distingue órdenes de realimentación física."""
        return {"activo": bool(self.pin.value), "realimentacion": True}


@registrar_componente("LM35")
class LM35(BaseComponente):
    """LM35 a través de MCP3008: 10 mV por grado Celsius."""

    def inicializar(self):
        """Reserva recursos e inicializa el dispositivo en estado seguro."""
        self.adc = self.backend.adc(
            self.parametros["canal"], self.parametros["dispositivo_spi"], self.parametros["vref"]
        )
        self.dispositivos.append(self.adc)

    def leer(self):
        """Obtiene valores y distingue órdenes de realimentación física."""
        tension = float(self.adc.voltage)
        return {
            "temperatura_c": round(tension * 100, 2),
            "tension_v": tension,
            "realimentacion": True,
        }


@registrar_componente("STEPPER_DRV8825")
class StepperDRV8825(BaseComponente):
    """Posición estimada por pasos. El operador referencia físicamente el eje.

    Pulsos para pruebas manuales a baja velocidad, sin garantía de tiempo real
    ni encoder. La temporización de grabado requiere un generador dedicado.
    """

    def inicializar(self):
        """Reserva recursos e inicializa el dispositivo en estado seguro."""
        self.step = self.salida("pin_step")
        self.direccion = self.salida("pin_dir")
        self.enable = self.salida("pin_enable", activo_alto=False)
        self.pasos = 0
        self.referenciado = False

    def ejecutar(self, operacion, argumentos):
        """Aplica una operación previamente validada por el gestor."""
        if operacion == "deshabilitar":
            self.apagar()
            return
        if operacion == "referenciar":
            self.enable.off()
            self.pasos = round(argumentos["posicion_mm"] * self.parametros["pasos_por_mm"])
            self.referenciado = True
            return
        if not self.referenciado:
            raise ValueError("Referencie físicamente el eje y declare su posición antes de mover.")
        destino = round(argumentos["posicion_mm"] * self.parametros["pasos_por_mm"])
        diferencia = destino - self.pasos
        signo = 1 if diferencia >= 0 else -1
        self.direccion.value = diferencia >= 0
        self.enable.on()
        pausa = 0.5 / (self.parametros["pasos_por_mm"] * self.parametros["velocidad_mm_s"])
        time.sleep(0.001)
        try:
            for _ in range(abs(diferencia)):
                if self.cancelar.is_set():
                    raise InterruptedError(
                        "Movimiento interrumpido; es necesario referenciar otra vez."
                    )
                self.step.on()
                self.pasos += signo
                time.sleep(pausa)
                self.step.off()
                if self.cancelar.wait(pausa):
                    raise InterruptedError("Movimiento interrumpido.")
        except BaseException:
            self.referenciado = False
            self.enable.off()
            raise
        finally:
            self.step.off()

    def leer(self):
        """Obtiene valores y distingue órdenes de realimentación física."""
        return {
            "posicion_estimada_mm": self.pasos / self.parametros["pasos_por_mm"],
            "referenciado": self.referenciado,
            "habilitado": bool(self.enable.value),
            "realimentacion": False,
        }

    def seguro(self):
        """Indica si el dispositivo está detenido para reconfigurarlo."""
        return not self.enable.value

    def apagar(self):
        """Desactiva las salidas y descarta el estado de armado."""
        if hasattr(self, "step"):
            self.step.off()
        if hasattr(self, "enable"):
            self.enable.off()
        self.referenciado = False
