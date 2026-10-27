from abc import ABC, abstractmethod

class HalBase(ABC):
    """Interfaz abstracta para la capa de abstracción de hardware (HAL)"""
    
    def __init__(self) -> None:
        self.refrigeracion: bool = False
        self.alta_tension: bool = False
        self.laser: bool = False
        self.shutter_armado: bool = False
        self.shutter_abierto: bool = False
        self.potencia_objetivo_mw: float = 0.0
        self.falla: str = "ninguna"
        self.temp_agua: float = 23.0
        self.caudal: float = 0.0
        self.tension_kv: float = 0.0
        self.corriente_ma: float = 0.0
        self.potencia_optica_mw: float = 0.0
        self.temp_laser: float = 23.0
        self.posicion_mm: float = 0.0
        self._posicion_objetivo_mm: float = 0.0
        self._alineacion: float = 0.0
        self._alineando: bool = False

    @abstractmethod
    def alinear(self) -> None:
        pass

    @abstractmethod
    def desalinear(self) -> None:
        pass

    @abstractmethod
    def mover_a(self, posicion_mm: float) -> float:
        pass

    @abstractmethod
    def lecturas(self) -> dict[str, float]:
        pass

    @abstractmethod
    def senales(self) -> dict[str, bool]:
        pass
    
    @abstractmethod
    def avanzar(self, dt: float) -> None:
        pass