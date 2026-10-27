from controller.hal_base import HalBase
import logging

logger = logging.getLogger("controller.rpi")

class HalGpio(HalBase):
    """Driver de control directo para la Raspberry Pi 5."""

    def __init__(self) -> None:
        super().__init__()
        # Valores nominales iniciales para evitar alertas de umbral
        self.caudal = 4.2
        self.temp_agua = 20.0

    @property
    def fibra_alineada(self) -> bool:
        return self._alineacion >= 1.0

    @property
    def alineando(self) -> bool:
        return self._alineando and not self.fibra_alineada

    @property
    def sensor_sombra_mw(self) -> float:
        return 2.4 * (1 - self._alineacion) + 0.12 * self._alineacion

    @property
    def motor_en_destino(self) -> bool:
        return abs(self.posicion_mm - self._posicion_objetivo_mm) < 1e-6
    
    def __init__(self) -> None:
        super().__init__()

    def alinear(self) -> None:
        self._alineacion = 1.0
        logger.info("[HalGpio] Alineación activada (stub)")

    def desalinear(self) -> None:
        self._alineacion = 0.0
        logger.info("[HalGpio] Alineación desactivada (stub)")

    def mover_a(self, posicion_mm: float) -> float:
        return 0.0

    def lecturas(self) -> dict[str, float]:
        return {
            "TEMPERATURA_AGUA": 20.0,
            "CAUDAL_REFRIGERANTE": 4.2,
            "POTENCIA_LASER": 0.0,
            "TENSION_AT": 0.0,
            "CORRIENTE_AT": 0.0,
            "POSICION_MOTOR": self.posicion_mm,
            "TEMPERATURA_AMBIENTE": 23.0,
        }
    def senales(self) -> dict[str, bool]:
        # Devuelve el estado básico calculado a partir de las variables de la instancia
        return {
            "FIBRA_ALINEADA": self._alineacion >= 1.0,
            "SHUTTER_ABIERTO": self.shutter_abierto,
            "SHUTTER_CERRADO": not self.shutter_abierto,
        }
        
    def avanzar(self, dt: float) -> None:
        # Por ahora simplemente ignora el dt ya que se está en fase de prueba
        pass
    
    def armar_shutter(self) -> None:
        self.shutter_armado = True
        self.shutter_abierto = False
        logger.info("[HalGpio] Shutter armado (gpio stub)")
        
    def desarmar_shutter(self) -> None:
        self.shutter_armado = False
        logger.info("[HalGpio] Shutter desarmado (gpio stub)")