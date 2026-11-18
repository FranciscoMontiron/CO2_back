"""Drivers físicos para puesta en marcha manual; GPIO se importa solo en la Pi."""

import os
import time

from controller.factory import MOTOR_CONTRATO, BaseComponente, registrar_componente
from controller.hardware_config import numero

# Cola del pulso que se espera en bucle activo: cubre la granularidad del temporizador del
# sistema (hasta ~16 ms en Windows, ~0,1 ms en Linux) sin gastar CPU en el resto del pulso.
MARGEN_ESPERA_ACTIVA = 0.02


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

    DESCRIPCION = "Salida digital con polaridad configurable."
    PINES = ["pin_señal"]
    PARAMETROS = {"activo_alto": True}
    COMANDOS = {"encender": {}, "apagar": {}}
    CAMPOS = {"salida_activa": "booleano"}

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

    DESCRIPCION = "Orden eléctrica de shutter, sin sensor de posición."
    COMANDOS = {
        "armar": {},
        "desarmar": {},
        "abrir": {},
        "cerrar": {},
        "pulso": {"duracion_ms": "numero"},
    }
    CAMPOS = {"armado": "booleano", "salida_apertura": "booleano"}

    @classmethod
    def validar_argumentos(cls, config, comando, argumentos):
        """El pulso dura de 1 a 10 000 ms."""
        if comando == "pulso":
            numero(argumentos["duracion_ms"], "duracion_ms", 1, 10000)

    def plazo(self, comando, argumentos):
        """El pulso tarda su duración más dos segundos."""
        if comando == "pulso":
            return argumentos["duracion_ms"] / 1000 + 2
        return super().plazo(comando, argumentos)

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
        elif operacion == "pulso":
            return self._pulso(argumentos["duracion_ms"])

    def _pulso(self, duracion_ms):
        """Abre el shutter y lo cierra a los `duracion_ms` medidos aquí, sin red de por medio.

        Espera bloqueante cancelable hasta los últimos 20 ms y completa en espera activa.
        """
        if not self.armado:
            raise ValueError("Arme el shutter antes de abrirlo.")
        total = duracion_ms / 1000
        try:
            self.pin.on()
            t0 = time.perf_counter()
            cancelado = self.cancelar.wait(max(0, total - MARGEN_ESPERA_ACTIVA))
            while not cancelado and time.perf_counter() - t0 < total:
                time.sleep(0)  # cede el GIL a otros hilos (p. ej. el motor) sin dejar de sondear
                cancelado = self.cancelar.is_set()
        finally:
            self.pin.off()
        duracion_real = (time.perf_counter() - t0) * 1000
        return {"duracion_real_ms": duracion_real, "cancelado": bool(cancelado)}

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

    DESCRIPCION = "Entrada digital con resistencia interna configurable."
    PINES = ["pin_señal"]
    PARAMETROS = {"pull_up": True}
    CAMPOS = {"activo": "booleano"}

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

    DESCRIPCION = "Sensor de temperatura LM35 vía MCP3008."
    PARAMETROS = {"adc": "MCP3008", "canal": 0, "dispositivo_spi": 0, "vref": 3.3}
    CAMPOS = {"temperatura_c": "numero", "tension_v": "numero"}
    RECURSO = "SPI0"
    AYUDA = (
        "Salida analógica al MCP3008, nunca directamente a un GPIO. "
        "SPI0 reserva GPIO9/10/11 y GPIO8 (CS0) o GPIO7 (CS1)."
    )

    @classmethod
    def prevalidar(cls, pines, parametros):
        """El LM35 no usa GPIO directos: exige el MCP3008 y ningún pin."""
        if pines or parametros.get("adc") != "MCP3008":
            raise ValueError(
                "LM35 requiere ADC MCP3008: pines={}, "
                "parametros.adc=MCP3008 y canal=0..7; Datos no es GPIO."
            )
        return parametros

    @classmethod
    def validar_parametros(cls, nombre, pines, params, contexto):
        """Valida canal, SPI y tensión de referencia, y reserva el canal del ADC."""
        for campo, maximo in (("canal", 7), ("dispositivo_spi", 1)):
            if type(params[campo]) is not int or not 0 <= params[campo] <= maximo:
                raise ValueError(f"{campo} debe ser un entero entre 0 y {maximo}.")
        numero(params["vref"], "vref", 0.1, 3.3)
        direccion = (params["dispositivo_spi"], params["canal"])
        canales = contexto.setdefault("canales", set())
        if direccion in canales:
            raise ValueError("Canal del ADC duplicado.")
        canales.add(direccion)
        return [9, 10, 11, 8 if params["dispositivo_spi"] == 0 else 7], cls.RECURSO

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
    Cumple el contrato genérico de motor (`MOTOR_CONTRATO`).
    """

    DESCRIPCION = "Eje con motor paso a paso y driver DRV8825."
    CONTRATO = "MOTOR"
    PINES = ["pin_step", "pin_dir", "pin_enable"]
    PARAMETROS = {
        "unidad": "mm",
        "pasos_por_unidad": 200,
        "minimo": 0,
        "maximo": 300,
        "velocidad_maxima": 1,
    }
    COMANDOS = MOTOR_CONTRATO["comandos"]
    CAMPOS = MOTOR_CONTRATO["campos"]
    AYUDA = (
        "«detener» desde Control manual espera a que termine el movimiento en curso. "
        "Para frenarlo ya, usar la parada de emergencia (después hay que referenciar)."
    )

    NOMBRES_ANTIGUOS = {
        "pasos_por_mm": "pasos_por_unidad",
        "carrera_max_mm": "maximo",
        "velocidad_mm_s": "velocidad_maxima",
    }

    @classmethod
    def prevalidar(cls, pines, parametros):
        """Migra los nombres anteriores (mm) a los del contrato genérico de motor."""
        antiguos = set(parametros) & set(cls.NOMBRES_ANTIGUOS)
        if not antiguos:
            return parametros
        if set(parametros) & set(cls.NOMBRES_ANTIGUOS.values()) or "unidad" in parametros:
            raise ValueError(
                "No mezcle los parámetros anteriores "
                f"({', '.join(sorted(antiguos))}) con los nuevos del motor."
            )
        return {cls.NOMBRES_ANTIGUOS.get(k, k): v for k, v in parametros.items()}

    @classmethod
    def validar_parametros(cls, nombre, pines, params, contexto):
        """Valida unidad, pasos, límites y velocidad máxima del eje."""
        resultado = super().validar_parametros(nombre, pines, params, contexto)
        if params["unidad"] not in ("mm", "°"):
            raise ValueError('unidad debe ser "mm" o "°".')
        numero(params["pasos_por_unidad"], "pasos_por_unidad", 1, 100000)
        numero(params["minimo"], "minimo", -100000, 100000)
        numero(params["maximo"], "maximo", -100000, 100000)
        if params["maximo"] <= params["minimo"]:
            raise ValueError("maximo debe ser mayor que minimo.")
        numero(params["velocidad_maxima"], "velocidad_maxima", 0.001, 100)
        if params["pasos_por_unidad"] * params["velocidad_maxima"] > 2000:
            raise ValueError("La velocidad excede 2000 pasos/s del driver manual.")
        return resultado

    @classmethod
    def validar_argumentos(cls, config, comando, argumentos):
        """Rangos que solo dependen de la configuración: límites y velocidad."""
        params = config["parametros"]
        if comando in ("referenciar", "mover_a"):
            numero(argumentos["posicion"], "posicion", params["minimo"], params["maximo"])
        if comando == "mover":
            ancho = params["maximo"] - params["minimo"]
            numero(argumentos["delta"], "delta", -ancho, ancho)
        if comando in ("mover_a", "mover"):
            velocidad = numero(argumentos["velocidad"], "velocidad", 0, params["velocidad_maxima"])
            if velocidad <= 0:
                raise ValueError("velocidad debe ser mayor que cero.")

    def _posicion(self):
        """Posición estimada en la unidad configurada."""
        return self.pasos / self.parametros["pasos_por_unidad"]

    def _distancia(self, comando, argumentos):
        """Distancia a recorrer, con signo, a partir de la posición actual."""
        if comando == "mover_a":
            return argumentos["posicion"] - self._posicion()
        return argumentos["delta"]

    def plazo(self, comando, argumentos):
        """Un movimiento tarda |distancia| / velocidad más cinco segundos, hasta diez minutos."""
        if comando not in ("mover_a", "mover"):
            return super().plazo(comando, argumentos)
        velocidad = argumentos["velocidad"]
        if type(velocidad) not in (int, float) or not velocidad > 0:
            return super().plazo(comando, argumentos)  # la validación la rechazará
        distancia = abs(self._distancia(comando, argumentos))
        return min(600.0, distancia / velocidad + 5)

    def inicializar(self):
        """Reserva recursos e inicializa el dispositivo en estado seguro."""
        self.step = self.salida("pin_step")
        self.direccion = self.salida("pin_dir")
        self.enable = self.salida("pin_enable", activo_alto=False)
        self.pasos = 0
        # Posición comandada en la unidad, sin redondear: los `mover` relativos suman sobre
        # ella y solo el destino absoluto se redondea a pasos, así el error no se acumula.
        self._comandada = None
        self.referenciado = False
        self.en_movimiento = False

    def ejecutar(self, operacion, argumentos):
        """Aplica una operación previamente validada por el gestor."""
        if operacion == "detener":
            self.apagar()
            return
        if operacion == "referenciar":
            self.enable.off()
            self.pasos = round(argumentos["posicion"] * self.parametros["pasos_por_unidad"])
            self._comandada = argumentos["posicion"]
            self.referenciado = True
            return
        if not self.referenciado:
            raise ValueError("Referencie físicamente el eje y declare su posición antes de mover.")
        if operacion == "mover_a":
            destino = argumentos["posicion"]
        else:
            base = self._posicion() if self._comandada is None else self._comandada
            destino = base + argumentos["delta"]
        minimo, maximo = self.parametros["minimo"], self.parametros["maximo"]
        if not minimo <= destino <= maximo:
            raise ValueError(f"El destino {destino} queda fuera de [{minimo}, {maximo}].")
        pasos = round(destino * self.parametros["pasos_por_unidad"])
        self._mover_pasos(pasos, argumentos["velocidad"])
        self._comandada = destino

    def _mover_pasos(self, destino, velocidad):
        """Genera los pulsos hasta `destino` (en pasos), cancelable en cada paso."""
        diferencia = destino - self.pasos
        signo = 1 if diferencia >= 0 else -1
        self.direccion.value = diferencia >= 0
        self.enable.on()
        pausa = 0.5 / (self.parametros["pasos_por_unidad"] * velocidad)
        time.sleep(0.001)
        self.en_movimiento = True
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
            self._comandada = None
            self.enable.off()
            raise
        finally:
            self.en_movimiento = False
            self.step.off()

    def leer(self):
        """Obtiene valores y distingue órdenes de realimentación física."""
        return {
            "posicion": self._posicion(),
            "en_movimiento": self.en_movimiento,
            "referenciado": self.referenciado,
            "habilitado": bool(self.enable.value),
            "realimentacion": False,
        }

    def seguro(self):
        """Indica si el dispositivo está detenido para reconfigurarlo."""
        return not self.enable.value

    def apagar(self):
        """Frena, deshabilita el driver y descarta la referencia."""
        if hasattr(self, "step"):
            self.step.off()
        if hasattr(self, "enable"):
            self.enable.off()
        self.referenciado = False
        self._comandada = None
        self.en_movimiento = False
