from abc import ABC, abstractmethod


class OperacionRechazada(Exception):  # noqa: N818 - nombre fijado por el plan
    """Una orden no se aplicó; el mensaje está pensado para el operador."""


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

    async def operar(self, componente: str, comando: str, argumentos: dict) -> dict:
        """Ejecuta una orden de un componente y vuelve cuando terminó.

        Devuelve la salida del comando (por ejemplo la duración real de un pulso).
        Lanza ``OperacionRechazada`` si no se pudo aplicar.
        """
        raise NotImplementedError("Este HAL no ejecuta órdenes por componente.")

    def inventario(self) -> dict:
        """Configuración de componentes contra la que se valida un programa."""
        raise NotImplementedError("Este HAL no tiene inventario de componentes.")

    def posicion_de(self, componente: str) -> float | None:
        """Última posición conocida de un motor, en su unidad; ``None`` si no se sabe."""
        return None

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