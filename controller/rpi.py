"""HAL GPIO: inventario físico y operación manual por instancia."""

import time

from controller.hal_base import HalBase
from controller.hardware import GestorHardware
from controller.roles import resolver


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

    async def operar(self, componente, comando, argumentos):
        """Encola la orden en el gestor y espera su resultado."""
        return await self.hardware.ejecutar_y_esperar(componente, comando, argumentos)

    def inventario(self):
        """Configuración vigente del gestor."""
        return self.hardware.config

    def posicion_de(self, componente):
        """Posición de la última muestra válida del motor; None si no hay una."""
        muestra = self.hardware.muestras.get(componente) or {}
        if muestra.get("calidad") != "OK":
            return None
        return (muestra.get("valores") or {}).get("posicion")

    def roles(self):
        """Magnitudes del dashboard según los roles asignados; lo ausente es None."""
        return resolver(self.hardware.config, self.hardware.muestras, time.monotonic())

    def lecturas(self):
        """Variables del dashboard alimentadas por componentes con rol."""
        return self.roles()["variables"]

    def senales(self):
        """Señales del dashboard alimentadas por componentes con rol."""
        return self.roles()["senales"]

    def avanzar(self, dt):
        """El muestreo físico lo realiza el gestor, sin simular dinámica."""
        pass
