import logging
from controller.factory import BaseComponente, registrar_componente

logger = logging.getLogger("controller.drivers")


@registrar_componente("SHUTTER_GPIO")
class ShutterGPIO(BaseComponente):
    """Driver para un obturador controlado por GPIO. """
    
    def __init__(self, nombre: str):
        super().__init__(nombre)
        self.armado = False
        self.abierto = False
        
    def inicializar(self) -> None:
        logger.info(
            f"[{self.nombre}] ShutterGPIO inicializado"
        )
        self.armado = False
        self.abierto = False
        
    def armar(self) -> None:
        """Arma el shutter para ser utilizado (abierto o cerrado)"""
        self.armado = True
        self.abierto = False
        logger.info(f"Shutter '{self.nombre}' Armado.")
    
    def desarmar(self) -> None:
        self.armado = False
        self.cerrar()
        logger.info(f"Shutter '{self.nombre}' Desarmado.")
    
    def abrir(self) -> None:
        """Abre la ventana del shutter si está previamente armado."""
        if not self.armado:
            logger.warning(
                f"[{self.nombre}] Intento de abrir el shutter sin estar ARMADO. Comando ignorado."
            )
            return

        self.abierto = True
        logger.info(f"[{self.nombre}] Shutter ABIERTO (Pin {self.pin_señal} HIGH)")

    def cerrar(self) -> None:
        """Cierra el paso de luz del shutter."""
        self.abierto = False
        logger.info(f"[{self.nombre}] Shutter CERRADO (Pin {self.pin_señal} LOW)")

    def apagar(self) -> None:
        """Método Fail-Safe: Cierra y desactiva el componente ante emergencias o apagados."""
        self.cerrar()
        self.armado = False
        logger.info(f"[{self.nombre}] Apagado de seguridad completado.")