"""Servidor del simulador: protocolo de lgpio por TCP y página por HTTP."""

import json
import os
import socketserver
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from controller.hardware_config import leer_archivo, ruta_configuracion, validar_configuracion
from pi_sim.tablero import LgpioError, Tablero

PAGINA = Path(__file__).with_name("pagina.html")
OPERACIONES_LGPIO = {
    "reclamar_entrada",
    "reclamar_salida",
    "reclamar_alerta",
    "liberar",
    "leer",
    "escribir",
    "modo",
    "debounce",
    "cerrar_chip",
}
MAX_CUERPO = 64 * 1024


class _ConexionGpio(socketserver.StreamRequestHandler):
    """Atiende un proceso cliente; la conexión de abrir_chip marca su vida útil."""

    def handle(self):
        tablero = self.server.tablero
        escritura = threading.Lock()
        handle = None

        def enviar(mensaje):
            with escritura:
                self.wfile.write(json.dumps(mensaje).encode() + b"\n")
                self.wfile.flush()

        def notificar(gpio, nivel, ticks):
            try:
                enviar({"ev": [gpio, nivel, ticks]})
            except OSError:
                pass

        try:
            for linea in self.rfile:
                pedido = json.loads(linea)
                try:
                    if pedido["op"] == "abrir_chip":
                        handle = tablero.abrir_chip(*pedido["args"])
                        tablero.suscribir(handle, notificar)
                        enviar({"r": handle})
                    elif pedido["op"] in OPERACIONES_LGPIO:
                        enviar({"r": getattr(tablero, pedido["op"])(*pedido["args"])})
                    else:
                        enviar({"e": "operación desconocida"})
                except (LgpioError, TypeError) as exc:
                    enviar({"e": str(exc)})
        except (OSError, ValueError):
            pass
        finally:
            if handle is not None:
                tablero.cerrar_chip(handle)


class _ServidorGpio(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class _PaginaHttp(BaseHTTPRequestHandler):
    """Página y API del tablero. Solo se publica en 127.0.0.1."""

    def log_message(self, *args):
        """El registro de la página ya muestra lo importante."""

    def _responder(self, codigo, cuerpo, tipo="application/json"):
        self.send_response(codigo)
        self.send_header("Content-Type", f"{tipo}; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, codigo, datos):
        self._responder(codigo, json.dumps(datos, ensure_ascii=False).encode())

    def do_GET(self):  # noqa: N802
        """Entrega la página, el estado y el chequeo de salud."""
        simulador = self.server.simulador
        if self.path == "/":
            self._responder(HTTPStatus.OK, PAGINA.read_bytes(), "text/html")
        elif self.path == "/api/estado":
            simulador.sincronizar_archivo()
            self._json(HTTPStatus.OK, simulador.tablero.estado())
        elif self.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "No existe."})

    def do_POST(self):  # noqa: N802
        """Ejecuta una acción. Exigir JSON impide accionar desde un formulario ajeno."""
        simulador = self.server.simulador
        tablero = simulador.tablero
        acciones = {
            "/api/conexion": lambda d: tablero.conectar(d["componente"], d["conectado"]),
            "/api/contacto": lambda d: tablero.accionar_contacto(d["componente"], d["activo"]),
            "/api/temperatura": lambda d: tablero.fijar_temperatura(d["componente"], d["valor"]),
            "/api/falla": lambda d: tablero.fijar_falla(d["tipo"], d["activo"], d.get("gpio")),
        }
        try:
            largo = int(self.headers.get("Content-Length", "0"))
            if not 0 <= largo <= MAX_CUERPO:
                raise ValueError("Cuerpo demasiado grande.")
            cuerpo = self.rfile.read(largo)
            if self.path not in acciones:
                self._json(HTTPStatus.NOT_FOUND, {"detail": "No existe."})
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"detail": "Se requiere JSON."})
                return
            datos = json.loads(cuerpo)
            if not isinstance(datos, dict):
                raise ValueError("Se esperaba un objeto JSON.")
            simulador.sincronizar_archivo()
            acciones[self.path](datos)
            self._json(HTTPStatus.OK, {"ok": True})
        except KeyError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"detail": f"Falta el campo {exc}."})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})


class Servidor:
    """Une el tablero con sus dos puertos y con la configuración del SCADA."""

    def __init__(self, tablero, host, puerto_gpio, puerto_http, ruta_config):
        """Reserva los puertos; 0 elige uno libre."""
        self.tablero = tablero
        self.ruta_config = ruta_config
        self._cerrojo_config = threading.Lock()
        self._gpio = _ServidorGpio((host, puerto_gpio), _ConexionGpio)
        self._gpio.tablero = tablero
        self._http = ThreadingHTTPServer((host, puerto_http), _PaginaHttp)
        self._http.daemon_threads = True
        self._http.simulador = self
        self.puerto_gpio = self._gpio.server_address[1]
        self.puerto_http = self._http.server_address[1]

    def sincronizar_archivo(self):
        """Lee lo dado de alta. Si el archivo está roto conserva el último inventario."""
        with self._cerrojo_config:
            try:
                self.tablero.sincronizar(validar_configuracion(leer_archivo(self.ruta_config)))
                self.tablero.error_config = None
            except (ValueError, OSError) as exc:
                self.tablero.error_config = str(exc)

    def iniciar(self):
        """Atiende ambos puertos en segundo plano."""
        for servidor in (self._gpio, self._http):
            threading.Thread(target=servidor.serve_forever, daemon=True).start()

    def detener(self):
        """Cierra ambos puertos."""
        for servidor in (self._gpio, self._http):
            servidor.shutdown()
            servidor.server_close()


if __name__ == "__main__":
    principal = Servidor(
        Tablero(),
        os.environ.get("PI_SIM_BIND", "0.0.0.0"),  # noqa: S104 - compose publica solo en loopback
        int(os.environ.get("PI_SIM_PUERTO", "9000")),
        int(os.environ.get("PI_SIM_PUERTO_HTTP", "8090")),
        ruta_configuracion(),
    )
    print(f"Pi 5 simulada: GPIO en {principal.puerto_gpio}, página en {principal.puerto_http}")
    principal.iniciar()
    threading.Event().wait()
