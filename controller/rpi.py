import json
import logging
from pathlib import Path
from typing import Dict

from controller.hal_base import HalBase
from controller.factory import BaseComponente, HardwareFactory

import controller.drivers

logger = logging.getLogger("controller.rpi")

# Ruta por defecto al archivo JSON de configuración dentro del módulo controller
RUTA_JSON_DEFAULT = Path(__file__).resolve().parent / "hardware_config.json"

class HalGpio(HalBase):
    """Driver de control directo para la Raspberry Pi 5."""

    def __init__(self) -> None:
        self.componentes: Dict[str, BaseComponente] = {}
        self.ruta_json = RUTA_JSON_DEFAULT
        
        super().__init__()
        
        
        self.cargar_componentes()

    def cargar_componentes(self) -> None:
        """Importa los componentes desde el JSON y los instancia"""
        
        if not self.ruta_json.exists():
            logger.warning(f"[HalGpio] No se encontro el archivo de configuración {self.ruta_json}.")

        try:
            with open(self.ruta_json, "r") as f:
                config = json.load(f)
                
            config_componentes = config.get("componentes", {})
            
            for nombre, cfg in config_componentes.items():
                
                # Filtramos los componentes eliminados del arreglo
                if cfg.get("estado") != "Vigente":
                    logger.info(f"[HalGpio] Componente '{nombre}' no vigente. Ignorado.")
                    continue
                
                try:
                    
                    instancia = HardwareFactory.crear_componente(nombre, cfg)
                    instancia.inicializar()
                    self.componentes[nombre] = instancia
                
                except Exception as e:
                    logger.error(f"[HalGpio] Error al crear el componente '{nombre}': {e}")

            logger.info(f"[HalGpio] Componentes cargados: {list(self.componentes.keys())}")
        
        except Exception as e:
            logger.error(f"[HalGpio] Error al cargar el archivo de configuración: {e}")
        
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
            "TEMPERATURA_AGUA": 23.0,
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
    
    @property
    def shutter(self) -> BaseComponente | None:
        """Acceso al shutter principal registrado en la HAL."""
        componentes = getattr(self, "componentes", {})
        return componentes.get("shutter_principal")
    
    @property
    def shutter_armado(self) -> bool:
        """La verdad del estado la tiene la instancia del driver."""
        sh = self.shutter
        return sh.armado if sh else False

    @shutter_armado.setter
    def shutter_armado(self, valor: bool) -> None:
        pass
            
    @property
    def shutter_abierto(self) -> bool:
        sh = self.shutter
        return sh.abierto if sh else False
    
        
    @shutter_abierto.setter
    def shutter_abierto(self, valor: bool) -> None:
        """Setter de compatibilidad para apertura/cierre directo."""
        pass
    
    def armar_shutter(self) -> None:
        """Arma el obturador delegando la acción al driver del JSON."""
        sh = self.shutter
        if sh:
            sh.armar()
            logger.info("[HalGpio] 'shutter' ARMADO exitosamente.")
        else:
            logger.warning("[HalGpio] No se encontró 'shutter' en componentes vigentes.")

    def desarmar_shutter(self) -> None:
        """Desarma el obturador delegando la acción al driver del JSON."""
        sh = self.shutter
        if sh:
            sh.desarmar()
            logger.info("[HalGpio] 'shutter' DESARMADO exitosamente.")
        else:
            logger.warning("[HalGpio] No se encontró 'shutter' en componentes vigentes.")            