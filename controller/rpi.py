"""HAL GPIO: inventario físico y operación manual por instancia."""

from controller.hal_base import HalBase
from controller.hardware import GestorHardware


class HalGpio(HalBase):
    """No simula sensores ni promete funciones de grabado aún no implementadas."""

    modo = "gpio"

    def __init__(self, gestor=None):
        """Inicializa el estado y las dependencias de la instancia."""
        super().__init__()
        self.hardware = gestor or GestorHardware()

    def alinear(self):
        """Rechaza alineación automática durante la puesta en marcha manual."""
        raise ValueError("Alineación automática no disponible en puesta en marcha manual.")

    def desalinear(self):
        """No modifica dispositivos sin enlaces funcionales configurados."""
        pass

    def mover_a(self, posicion_mm):
        """Exige seleccionar explícitamente el desplazador de destino."""
        raise ValueError("Seleccione el desplazador por su nombre en control de componentes.")

    def lecturas(self):
        """Declara ausentes las magnitudes del arreglo todavía no enlazadas."""
        return {
            k: None
            for k in (
                "TEMPERATURA_AGUA",
                "CAUDAL_REFRIGERANTE",
                "POTENCIA_LASER",
                "TENSION_AT",
                "CORRIENTE_AT",
                "POSICION_MOTOR",
                "TEMPERATURA_AMBIENTE",
            )
        }

    def senales(self):
        """Declara ausentes las señales del arreglo todavía no enlazadas."""
        return {"FIBRA_ALINEADA": None, "SHUTTER_ABIERTO": None, "SHUTTER_CERRADO": None}

    def avanzar(self, dt):
        """El muestreo físico lo realiza el gestor, sin simular dinámica."""
        pass
