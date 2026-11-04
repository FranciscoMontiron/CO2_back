"""Registro explícito de drivers. El JSON nunca importa código arbitrario."""

import threading


class BaseComponente:
    """Driver síncrono ejecutado exclusivamente por el gestor de hardware."""

    def __init__(self, nombre, config, backend):
        """Inicializa el estado y las dependencias de la instancia."""
        self.nombre, self.config, self.backend = nombre, config, backend
        self.parametros, self.pines = config["parametros"], config["pines"]
        self.cancelar = threading.Event()
        self.dispositivos = []
        self.recurso = "SPI0" if config["tipo"] == "LM35" else f"gpio:{nombre}"

    def inicializar(self):
        """Reserva recursos e inicializa el dispositivo en estado seguro."""
        raise NotImplementedError

    def ejecutar(self, operacion, argumentos):
        """Aplica una operación previamente validada por el gestor."""
        raise NotImplementedError

    def leer(self):
        """Obtiene valores y distingue órdenes de realimentación física."""
        raise NotImplementedError

    def seguro(self):
        """Indica si el dispositivo está detenido para reconfigurarlo."""
        return True

    def apagar(self):
        """Las entradas no requieren escritura de apagado."""

    def cerrar(self):
        """Libera los recursos incluso ante un error de apagado."""
        try:
            self.apagar()
        finally:
            for dispositivo in reversed(self.dispositivos):
                dispositivo.close()
            self.dispositivos.clear()

    def salida(self, nombre, activo_alto=True):
        """Crea una salida inicialmente inactiva con la polaridad indicada."""
        dispositivo = self.backend.salida(self.pines[nombre], activo_alto)
        self.dispositivos.append(dispositivo)
        return dispositivo


REGISTRO_COMPONENTES = {}


def registrar_componente(tipo):
    """Registra tipos conocidos al importar los drivers."""

    def registrar(clase):
        REGISTRO_COMPONENTES[tipo] = clase
        return clase

    return registrar


class HardwareFactory:
    """Construye tantas instancias como declare la configuración validada."""

    @staticmethod
    def crear_componente(nombre, config, backend):
        """Construye el driver registrado con su configuración validada."""
        from controller import drivers  # noqa: F401 - registra los drivers

        return REGISTRO_COMPONENTES[config["tipo"]](nombre, config, backend)
