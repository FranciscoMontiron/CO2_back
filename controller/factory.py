from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, Type

logger = logging.getLogger("controller.factory")

class BaseComponente(ABC):
    """Clase base que deben heredar todos los drivers de componentes"""
    
    def __init__(self, nombre: str, **kwargs: Any) -> None:
        self.nombre = nombre
    
    @abstractmethod
    def inicializar(self) -> None:
        """Configura pines GPIO, periféricos o variables internas del hardware."""
        pass
    
    @abstractmethod
    def apagar(self) -> None:
        """Deja el componente en un estado seguro (Fail-Safe)"""
        pass
    
REGISTRO_COMPONENTES: Dict[str, Type[BaseComponente]] = {}

def registrar_componente(tipo_str: str):
    """Decorador para registrar una cadena de texto 'tipo' (ej: 'SHUTTER_GPIO') con su clase Python."""
    
    def decorador(cls: Type[BaseComponente]):
        REGISTRO_COMPONENTES[tipo_str] = cls
        return cls
    
    return decorador

class HardwareFactory:
    """Fábrica dinámica de componentes según la configuración JSON."""
    
    @staticmethod
    def crear_componente(nombre: str, config: dict) -> BaseComponente:
        tipo = config.get("tipo")
        if not tipo:
            raise ValueError(f"El componente '{nombre}' no especifica el tipo en la configuración")
        
        clase_driver = REGISTRO_COMPONENTES.get(tipo)
        if not clase_driver:
            raise ValueError(f"El componente '{tipo}' no es soportado por la versión actual del software")
        
        # Extraemos pines y parámetros y los unificamos en un diccionario
        pines = config.get("pines", {})
        parametros = config.get("parametros", {})
        kwargs = {**pines, **parametros}
        
        logger.debug(f"Creando componente '{nombre}': ({tipo}) con parámetros: {kwargs}")
        
        return clase_driver(nombre, **kwargs)