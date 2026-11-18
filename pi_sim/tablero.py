"""Líneas GPIO y componentes físicos de la Pi 5 simulada, sin red ni archivos."""

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

GPIO_MAX = 27
CHIPS_VALIDOS = (0, 4)  # El compose de la Pi monta el conector en ambas rutas.
PINES_SPI = {"miso": 9, "mosi": 10, "clock": 11, "cs": {8: 0, 7: 1}}
FALLAS_LINEA = ("ocupado", "colgado")
TIEMPO_SHUTTER = 0.2  # Supuesto: reemplazar por el valor medido en el laboratorio.


class LgpioError(Exception):
    """Falla con los mismos textos que entrega la biblioteca lgpio."""


@dataclass
class Linea:
    """Estado de una línea del chip."""

    dueno: int | None = None
    salida: bool = False
    escrito: int = 0
    flags: int = 0
    alerta: bool = False
    sin_dueno: bool = False
    ultimo_evento: int | None = None


@dataclass
class Componente:
    """Pieza física cableada a los pines declarados en la configuración."""

    nombre: str
    tipo: str
    pines: dict
    parametros: dict
    conectado: bool = False
    contacto: bool = False
    temperatura_c: float = 25.0
    pasos: int = 0
    objetivo: bool = False
    t_cambio: float = field(default=-math.inf)


class Tablero:
    """Modelo de la placa. Los textos de error imitan a lgpio."""

    SET_PULL_UP = 32
    SET_PULL_DOWN = 64
    SET_PULL_NONE = 128

    _MODO_SALIDA = 2 | 512
    _MODO_ENTRADA = 256
    _MODO_ALERTA = 1024

    def __init__(self, tiempo_shutter=TIEMPO_SHUTTER):
        """Arranca con todas las líneas libres y sin componentes."""
        self.tiempo_shutter = tiempo_shutter
        self.error_config = None
        self._cv = threading.Condition(threading.RLock())
        self._lineas = {g: Linea() for g in range(GPIO_MAX + 1)}
        self._componentes = {}
        self._fallas = {}
        self._chip_colgado = False
        self._handles = {}
        self._siguiente = 1
        self._registro = deque(maxlen=100)
        self._spi = {"cs": None, "n": -1, "bits": 0, "codigo": 0, "miso": 0}

    # ── API equivalente a lgpio ───────────────────────────────────────────

    def abrir_chip(self, chip):
        """Equivale a gpiochip_open."""
        with self._cv:
            while self._chip_colgado:
                self._cv.wait()
            if chip not in CHIPS_VALIDOS:
                raise LgpioError("can not open gpiochip")
            handle = self._siguiente
            self._siguiente += 1
            self._handles[handle] = None
            self._anotar(f"El controlador abrió el chip {chip}.")
            return handle

    def suscribir(self, handle, notificar):
        """Registra quién recibe los flancos de las líneas con alerta."""
        with self._cv:
            self._handles[handle] = notificar

    def cerrar_chip(self, handle):
        """Libera las líneas: un cierre sin apagado deja la salida como estaba."""
        with self._cv:
            if handle not in self._handles:
                return 0
            del self._handles[handle]
            for gpio, linea in self._lineas.items():
                if linea.dueno == handle:
                    linea.dueno, linea.alerta = None, False
                    linea.sin_dueno, linea.flags = linea.salida, 0
                    if linea.sin_dueno:
                        self._anotar(f"GPIO{gpio} quedó sin dueño en nivel {linea.escrito}.")
            self._anotar("El controlador cerró el chip.")
            self._cv.notify_all()
            self._actualizar()
            return 0

    def reclamar_entrada(self, handle, gpio, flags=0):
        """Equivale a gpio_claim_input."""
        with self._cv:
            linea = self._reclamable(handle, gpio)
            linea.dueno, linea.salida, linea.flags = handle, False, flags
            linea.alerta, linea.sin_dueno = False, False
            self._actualizar()
            return 0

    def reclamar_salida(self, handle, gpio, nivel=0, flags=0):
        """Equivale a gpio_claim_output: el nivel se aplica al reservar."""
        with self._cv:
            linea = self._reclamable(handle, gpio)
            anterior = self._nivel(gpio)
            linea.dueno, linea.salida, linea.flags = handle, True, flags
            linea.alerta, linea.sin_dueno = False, False
            linea.escrito = 1 if nivel else 0
            self._al_cambiar(gpio, anterior)
            return 0

    def reclamar_alerta(self, handle, gpio, _bordes=3, flags=0):
        """Equivale a gpio_claim_alert."""
        with self._cv:
            self.reclamar_entrada(handle, gpio, flags)
            linea = self._lineas[gpio]
            linea.alerta, linea.ultimo_evento = True, self._nivel(gpio)
            return 0

    def liberar(self, handle, gpio):
        """Equivale a gpio_free."""
        with self._cv:
            linea = self._propia(handle, gpio)
            linea.dueno, linea.salida, linea.flags, linea.alerta = None, False, 0, False
            self._actualizar()
            return 0

    def leer(self, handle, gpio):
        """Equivale a gpio_read."""
        with self._cv:
            self._propia(handle, gpio)
            return self._nivel(gpio)

    def escribir(self, handle, gpio, nivel):
        """Equivale a gpio_write."""
        with self._cv:
            linea = self._propia(handle, gpio)
            if not linea.salida:
                raise LgpioError("GPIO not allocated for output")
            anterior = linea.escrito
            linea.escrito = 1 if nivel else 0
            self._al_cambiar(gpio, anterior)
            return 0

    def modo(self, handle, gpio):
        """Equivale a gpio_get_mode, con los bits que interpreta gpiozero."""
        with self._cv:
            linea = self._propia(handle, gpio)
            base = self._MODO_SALIDA if linea.salida else self._MODO_ENTRADA
            return base | linea.flags | (self._MODO_ALERTA if linea.alerta else 0)

    def debounce(self, handle, gpio, _micros):
        """Equivale a gpio_set_debounce_micros. El contacto simulado no rebota."""
        with self._cv:
            self._propia(handle, gpio)
            return 0

    # ── Acciones de la página ─────────────────────────────────────────────

    def sincronizar(self, config):
        """Refleja el inventario. Recablear un componente lo deja desconectado."""
        with self._cv:
            vigentes = {
                n: c for n, c in config["componentes"].items() if c["estado"] == "Vigente"
            }
            for nombre in list(self._componentes):
                if nombre not in vigentes:
                    del self._componentes[nombre]
                    self._anotar(f"Baja de {nombre}.")
            for nombre, c in vigentes.items():
                actual = self._componentes.get(nombre)
                if actual and (actual.tipo, actual.pines, actual.parametros) == (
                    c["tipo"],
                    c["pines"],
                    c["parametros"],
                ):
                    continue
                self._componentes[nombre] = Componente(
                    nombre, c["tipo"], c["pines"], c["parametros"]
                )
                self._anotar(f"Alta de {nombre} ({c['tipo']}).")
            self._actualizar()

    def conectar(self, nombre, conectado):
        """Enchufa o desenchufa el cable del componente."""
        with self._cv:
            componente = self._componente(nombre)
            componente.conectado = self._booleano(conectado)
            self._anotar(f"{nombre} {'conectado' if conectado else 'desconectado'}.")
            self._actualizar()

    def accionar_contacto(self, nombre, activo):
        """Cierra o abre el contacto de una entrada digital."""
        with self._cv:
            componente = self._componente(nombre, "ENTRADA_GPIO")
            componente.contacto = self._booleano(activo)
            self._anotar(f"Contacto de {nombre} {'cerrado' if activo else 'abierto'}.")
            self._actualizar()

    def fijar_temperatura(self, nombre, valor):
        """Fija lo que mide un LM35, dentro de su rango de -55 a 150 °C."""
        with self._cv:
            componente = self._componente(nombre, "LM35")
            if type(valor) not in (int, float) or not -55 <= valor <= 150:
                raise ValueError("La temperatura debe ser un número entre -55 y 150.")
            componente.temperatura_c = valor

    def fijar_falla(self, tipo, activo, gpio=None):
        """Activa o quita una falla de línea o del chip."""
        with self._cv:
            self._booleano(activo)
            if tipo == "chip_colgado":
                self._chip_colgado = activo
            elif tipo in FALLAS_LINEA:
                if type(gpio) is not int or not 0 <= gpio <= GPIO_MAX:
                    raise ValueError("Indique un GPIO entre 0 y 27.")
                if activo:
                    self._fallas[gpio] = tipo
                elif self._fallas.get(gpio) == tipo:
                    del self._fallas[gpio]
            else:
                raise ValueError(f"Falla desconocida: {tipo}.")
            destino = "" if gpio is None else f" en GPIO{gpio}"
            self._anotar(f"Falla {tipo}{destino}: {'activa' if activo else 'quitada'}.")
            self._cv.notify_all()

    def estado(self):
        """Instantánea para la página."""
        with self._cv:
            etiquetas = self._etiquetas()
            return {
                "chip": {"abierto": bool(self._handles), "colgado": self._chip_colgado},
                "pines": {
                    g: {
                        "nivel": self._nivel(g),
                        "modo": self._nombre_modo(linea),
                        "dueno": linea.dueno,
                        "sin_dueno": linea.sin_dueno,
                        "falla": self._fallas.get(g),
                        "componentes": etiquetas.get(g, []),
                    }
                    for g, linea in self._lineas.items()
                },
                "componentes": [
                    {
                        "nombre": c.nombre,
                        "tipo": c.tipo,
                        "pines": c.pines,
                        "parametros": c.parametros,
                        "conectado": c.conectado,
                        "fisico": self._fisico(c),
                    }
                    for c in self._componentes.values()
                ],
                "error_config": self.error_config,
                "registro": list(self._registro),
            }

    # ── Reglas internas ───────────────────────────────────────────────────

    def _reclamable(self, handle, gpio):
        linea = self._esperar_linea(handle, gpio)
        if self._fallas.get(gpio) == "ocupado" or linea.dueno not in (None, handle):
            self._anotar(f"GPIO{gpio} rechazado: GPIO busy.")
            raise LgpioError("GPIO busy")
        return linea

    def _propia(self, handle, gpio):
        linea = self._esperar_linea(handle, gpio)
        if linea.dueno != handle:
            raise LgpioError("GPIO not allocated")
        return linea

    def _esperar_linea(self, handle, gpio):
        if handle not in self._handles:
            raise LgpioError("bad handle")
        if type(gpio) is not int or not 0 <= gpio <= GPIO_MAX:
            raise LgpioError("bad GPIO number")
        while self._fallas.get(gpio) == "colgado":
            self._cv.wait()
        if handle not in self._handles:
            raise LgpioError("bad handle")
        return self._lineas[gpio]

    def _componente(self, nombre, tipo=None):
        componente = self._componentes.get(nombre)
        if componente is None or (tipo and componente.tipo != tipo):
            raise ValueError(f"Componente inexistente o de otro tipo: {nombre!r}.")
        return componente

    @staticmethod
    def _booleano(valor):
        if type(valor) is not bool:
            raise ValueError("Se esperaba un booleano.")
        return valor

    def _nivel(self, gpio):
        linea = self._lineas[gpio]
        if linea.salida:
            return linea.escrito
        externo = self._externo(gpio)
        if externo is not None:
            return externo
        return 1 if linea.flags & self.SET_PULL_UP else 0

    def _externo(self, gpio):
        """Nivel que le impone a una entrada algo conectado de afuera."""
        if gpio == PINES_SPI["miso"] and self._spi["cs"] is not None:
            return self._spi["miso"]
        for c in self._componentes.values():
            if c.tipo == "ENTRADA_GPIO" and c.pines["pin_señal"] == gpio:
                if c.conectado and c.contacto:
                    return 0 if c.parametros["pull_up"] else 1
        return None

    def _salida_activa(self, c, pin="pin_señal", activo_alto=None):
        linea = self._lineas[c.pines[pin]]
        activo_alto = c.parametros["activo_alto"] if activo_alto is None else activo_alto
        return linea.salida and bool(linea.escrito) == activo_alto

    def _al_cambiar(self, gpio, anterior):
        nuevo = self._nivel(gpio)
        if nuevo != anterior:
            self._modelar_motor(gpio, nuevo)
            self._modelar_mcp3008(gpio, nuevo)
            if gpio not in self._silenciosos():
                self._anotar(f"GPIO{gpio} → {nuevo}.")
        self._actualizar()

    def _silenciosos(self):
        """Líneas de muchos flancos por segundo, que taparían el registro."""
        silenciosos = {PINES_SPI["mosi"], PINES_SPI["clock"], *PINES_SPI["cs"]}
        silenciosos.update(
            c.pines["pin_step"] for c in self._componentes.values() if "pin_step" in c.pines
        )
        return silenciosos

    def _modelar_motor(self, gpio, nivel):
        if not nivel:
            return
        for c in self._componentes.values():
            if c.tipo == "STEPPER_DRV8825" and c.conectado and c.pines["pin_step"] == gpio:
                if self._salida_activa(c, "pin_enable", activo_alto=False):
                    c.pasos += 1 if self._nivel(c.pines["pin_dir"]) else -1

    def _modelar_mcp3008(self, gpio, nivel):
        """Protocolo del MCP3008, bit a bit, como lo recibe por SPI por software."""
        spi = self._spi
        if gpio in PINES_SPI["cs"]:
            if not nivel:
                spi.update(cs=gpio, n=-1, bits=0, codigo=0, miso=0)
            elif spi["cs"] == gpio:
                spi["cs"] = None
            return
        if gpio != PINES_SPI["clock"] or spi["cs"] is None:
            return
        if nivel:  # flanco de subida: el chip muestrea MOSI
            dato = self._nivel(PINES_SPI["mosi"])
            if spi["n"] < 0:
                spi["n"] = 0 if dato else -1
                return
            spi["n"] += 1
            if 1 <= spi["n"] <= 4:
                spi["bits"] = spi["bits"] << 1 | dato
            if spi["n"] == 4:
                spi["codigo"] = self._conversion(PINES_SPI["cs"][spi["cs"]], spi["bits"] & 7)
        elif spi["n"] >= 5:  # flanco de bajada: el chip entrega el bit siguiente
            indice = spi["n"] - 6  # primero sale el bit nulo
            spi["miso"] = 0 if not 0 <= indice <= 9 else spi["codigo"] >> (9 - indice) & 1

    def _conversion(self, dispositivo, canal):
        for c in self._componentes.values():
            p = c.parametros
            if c.tipo == "LM35" and (p["dispositivo_spi"], p["canal"]) == (dispositivo, canal):
                return self._codigo_adc(c)
        return 0

    @staticmethod
    def _codigo_adc(c):
        """10 mV/°C sobre 10 bits. Con el cable suelto el canal queda en 0."""
        if not c.conectado:
            return 0
        codigo = round(c.temperatura_c * 0.01 / c.parametros["vref"] * 1023)
        return max(0, min(1023, codigo))

    def _actualizar(self):
        """Recalcula el shutter y notifica flancos tras cualquier cambio."""
        ahora = time.monotonic()
        for c in self._componentes.values():
            if c.tipo == "SHUTTER_GPIO":
                objetivo = c.conectado and self._salida_activa(c)
                if objetivo != c.objetivo:
                    c.objetivo, c.t_cambio = objetivo, ahora
        for gpio, linea in self._lineas.items():
            if not linea.alerta:
                continue
            nivel = self._nivel(gpio)
            if nivel != linea.ultimo_evento:
                linea.ultimo_evento = nivel
                notificar = self._handles.get(linea.dueno)
                if notificar:
                    notificar(gpio, nivel, time.monotonic_ns())

    def _fisico(self, c):
        if c.tipo == "SALIDA_GPIO":
            return {"carga_activa": c.conectado and self._salida_activa(c)}
        if c.tipo == "SHUTTER_GPIO":
            moviendo = time.monotonic() - c.t_cambio < self.tiempo_shutter
            if c.objetivo:
                return {"posicion": "ABRIENDO" if moviendo else "ABIERTO"}
            return {"posicion": "CERRANDO" if moviendo else "CERRADO"}
        if c.tipo == "ENTRADA_GPIO":
            return {"contacto": c.contacto, "nivel": self._nivel(c.pines["pin_señal"])}
        if c.tipo == "STEPPER_DRV8825":
            return {
                "pasos": c.pasos,
                "posicion": round(c.pasos / c.parametros["pasos_por_unidad"], 6),
                "unidad": c.parametros["unidad"],
                "habilitado": c.conectado and self._salida_activa(c, "pin_enable", False),
            }
        return {"temperatura_c": c.temperatura_c, "codigo_adc": self._codigo_adc(c)}

    def _etiquetas(self):
        etiquetas = {}
        for c in self._componentes.values():
            for senal, pin in c.pines.items():
                etiquetas.setdefault(pin, []).append(f"{c.nombre} ({senal})")
            if c.tipo == "LM35":
                dispositivo = c.parametros["dispositivo_spi"]
                cs = next(p for p, d in PINES_SPI["cs"].items() if d == dispositivo)
                for pin in (PINES_SPI["miso"], PINES_SPI["mosi"], PINES_SPI["clock"], cs):
                    etiquetas.setdefault(pin, []).append(f"{c.nombre} (SPI0)")
        return etiquetas

    @staticmethod
    def _nombre_modo(linea):
        if linea.salida:
            return "salida"
        return "entrada" if linea.dueno is not None else "libre"

    def _anotar(self, texto):
        hora = datetime.now(UTC).strftime("%H:%M:%S")
        self._registro.appendleft({"hora": hora, "texto": texto})
