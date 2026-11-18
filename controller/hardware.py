"""Escrituras acotadas, exclusión por bus y lecturas concurrentes fuera del lazo."""

import asyncio
import copy
import logging
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from uuid import uuid4

from controller import protocolo as p
from controller.drivers import GpioBackend
from controller.factory import HardwareFactory
from controller.hal_base import OperacionRechazada
from controller.hardware_config import (
    comandos_de,
    leer_archivo,
    revision,
    validar_configuracion,
    validar_operacion,
)

logger = logging.getLogger("controller.hardware")


def ahora():
    """Devuelve el instante UTC para muestras y resultados."""
    return datetime.now(UTC).isoformat()


async def ejecutar_fuera_del_lazo(funcion, *args):
    """Aísla I/O en un hilo que no impide terminar un controlador averiado.

    Un timeout no mata el hilo: la exclusión física y la cuarentena siguen
    siendo responsabilidad de _llamar. No usa el executor global de asyncio,
    cuyo apagado esperaría indefinidamente una llamada nativa colgada.
    """
    bucle = asyncio.get_running_loop()
    futuro = bucle.create_future()

    def entregar(valor, error):
        if not futuro.done():
            if error is not None:
                futuro.set_exception(error)
            else:
                futuro.set_result(valor)

    def trabajar():
        valor, error = None, None
        try:
            valor = funcion(*args)
        except BaseException as exc:
            error = exc
        try:
            bucle.call_soon_threadsafe(entregar, valor, error)
        except RuntimeError:
            pass  # El proceso ya cerró su event loop.

    threading.Thread(target=trabajar, daemon=True, name="co2-hardware-io").start()
    return await futuro


class GestorHardware:
    """Único dueño de drivers. Las tareas asyncio nunca hacen I/O físico directo."""

    def __init__(self, ruta=None, backend_factory=GpioBackend, capacidad=32, timeout_io=2):
        """Inicializa el estado y las dependencias de la instancia."""
        self.ruta = ruta
        self.backend_factory = backend_factory
        self.backend = None
        self.timeout_io = timeout_io
        self.cola = asyncio.Queue(maxsize=capacidad)
        self.drivers = {}
        self.muestras = {}
        self.resultados = OrderedDict()
        self.config = {"componentes": {}}
        self.revision_aplicada = None
        self.revision_deseada = None
        self.intentada = None
        self.error_config = None
        self.aplicando = False
        self.emergencia = False
        self.causa_parada = None  # qué falló, si el gestor se detuvo solo
        self.cerrado = False
        self.generacion = 0
        self._ordenes = {}  # id -> (orden, tarea): aceptadas por el despachador, sin terminar
        self._ejecutando = {}  # id -> orden: las que tienen su recurso y tocan el driver
        self._turnos = {}  # recurso -> asyncio.Lock: FIFO de escrituras por recurso
        self._parando = None
        self._locks = {}
        self._async_locks = {}
        self._io = set()
        self._cuarentena = set()
        self._esperas = {}
        self._ciclo = time.monotonic()
        self.emitir = lambda tipo, datos: None

    @property
    def actual(self):
        """Primera orden que está tocando un driver, o None (compatibilidad)."""
        return next(iter(self._ejecutando.values()), None)

    def _pendientes(self):
        """Órdenes aceptadas que todavía no terminaron (en cola o en ejecución)."""
        return self.cola.qsize() + len(self._ordenes)

    def _resultado(self, cmd, estado, detalle=None, salida=None):
        identificador = cmd["id"]
        resultado = {
            "id": identificador,
            "componente": cmd.get("componente"),
            "operacion": cmd.get("operacion"),
            "estado": estado,
            "detalle": detalle,
            "salida": salida,
            "ts": ahora(),
        }
        self.resultados[identificador] = resultado
        self.resultados.move_to_end(identificador)
        while len(self.resultados) > 256:
            self.resultados.popitem(last=False)
        if estado not in ("EN_COLA", "EJECUTANDO"):
            # Primero quien espera (p. ej. el ejecutor): si emitir fallara, no queda colgado.
            espera = self._esperas.pop(identificador, None)
            if espera is not None and not espera.done():
                espera.set_result(resultado)
            try:
                self.emitir(p.EV_HARDWARE, {**resultado, "usuario": cmd.get("usuario")})
            except Exception:  # noqa: BLE001 - el resultado ya quedó en memoria
                logger.exception("No se pudo emitir el resultado de %s", identificador)
        return resultado

    async def ejecutar_y_esperar(self, componente, comando, argumentos, usuario=None):
        """Encola una orden y espera su resultado; devuelve la salida del driver.

        Lanza OperacionRechazada con el motivo si no se aplicó.
        """
        driver = self.drivers.get(componente)
        try:
            plazo = driver.plazo(comando, argumentos) if driver else 5.0
        except (KeyError, TypeError, ValueError):
            # Argumentos inválidos: el plazo no importa, encolar los rechaza con motivo.
            plazo = 5.0
        cmd = {
            "id": str(uuid4()),
            "componente": componente,
            "operacion": comando,
            "argumentos": argumentos,
            "revision": self.revision_aplicada,
            "vence_en": time.time() + plazo + 1,
            "usuario": usuario,
        }
        espera = asyncio.get_running_loop().create_future()
        self._esperas[cmd["id"]] = espera
        try:
            self.encolar(cmd)
            resultado = await espera
        finally:
            self._esperas.pop(cmd["id"], None)
        if resultado["estado"] in ("RECHAZADO", "VENCIDO", "CANCELADO", "FALLIDO"):
            raise OperacionRechazada(f"{componente}.{comando}: {resultado['detalle']}")
        return resultado["salida"] or {}

    def encolar(self, cmd):
        """Entrega inmediata; revalida revisión, plazo y emergencia al ejecutar."""
        if cmd["id"] in self.resultados:
            return self.resultados[cmd["id"]]
        try:
            if self.cerrado or self.emergencia or self.aplicando:
                raise ValueError("Hardware detenido, en emergencia o aplicando configuración.")
            if cmd.get("revision") != self.revision_aplicada or self.revision_aplicada is None:
                raise ValueError("La configuración cambió o todavía no se aplicó.")
            nombre = cmd.get("componente")
            if nombre not in self.drivers:
                raise ValueError("Componente inexistente, eliminado o no inicializado.")
            if self.drivers[nombre].recurso in self._cuarentena:
                raise ValueError(
                    "Recurso bloqueado por timeout; requiere revisar y reiniciar controlador."
                )
            validar_operacion(
                self.config["componentes"][nombre], cmd.get("operacion"), cmd.get("argumentos", {})
            )
            if cmd.get("vence_en", 0) <= time.time():
                return self._resultado(cmd, "VENCIDO", "La orden llegó fuera de plazo.")
            if self._pendientes() >= self.cola.maxsize:
                raise asyncio.QueueFull
            self.cola.put_nowait({**cmd, "generacion": self.generacion})
            return self._resultado(cmd, "EN_COLA")
        except (ValueError, asyncio.QueueFull) as exc:
            return self._resultado(cmd, "RECHAZADO", str(exc) or "Cola de escrituras llena.")

    async def _llamar(self, driver, funcion, timeout=None, rescate=False):
        recurso = driver.recurso
        if recurso in self._cuarentena and not rescate:
            raise RuntimeError("Recurso en cuarentena por timeout.")
        lock = self._locks.setdefault(recurso, threading.Lock())
        semaforo = self._async_locks.setdefault(recurso, asyncio.Lock())

        def protegido():
            # El lock real permanece tomado aunque venza la espera asyncio.
            if not lock.acquire(timeout=self.timeout_io):
                raise TimeoutError("El recurso sigue ocupado por una operación anterior.")
            try:
                return funcion()
            finally:
                lock.release()

        async with semaforo:
            if recurso in self._cuarentena and not rescate:
                raise RuntimeError("Recurso en cuarentena por timeout.")
            tarea = asyncio.create_task(ejecutar_fuera_del_lazo(protegido))
            self._io.add(tarea)

            def terminado(futuro):
                self._io.discard(futuro)
                if not futuro.cancelled():
                    futuro.exception()  # consume fallos tardíos sin ocultar el resultado al await

            tarea.add_done_callback(terminado)
            try:
                return await asyncio.wait_for(asyncio.shield(tarea), timeout or self.timeout_io)
            except TimeoutError:
                driver.cancelar.set()
                self._cuarentena.add(recurso)
                raise

    def _guardar_muestra(self, nombre, valores, seguro):
        self.muestras[nombre] = {
            "valores": valores,
            "seguro": seguro,
            "calidad": "OK",
            "ts": ahora(),
            "error": None,
            "monotonic": time.monotonic(),
        }

    async def _leer(self, nombre, driver):
        escribiendo = {
            self.drivers[c["componente"]].recurso
            for c in self._ejecutando.values()
            if c["componente"] in self.drivers
        }
        if driver.recurso in self._cuarentena or driver.recurso in escribiendo:
            return
        try:
            valores, seguro = await self._llamar(driver, lambda: (driver.leer(), driver.seguro()))
            self._guardar_muestra(nombre, valores, seguro)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            self.muestras[nombre] = {
                **self.muestras.get(nombre, {}),
                "calidad": "ERROR",
                "error": error,
            }
            if not self.aplicando and not self.emergencia:
                self.detener(f"lectura de {nombre}: {error}")

    def todo_seguro(self):
        """Comprueba que todas las muestras confirmen salidas detenidas."""
        return all(
            self.muestras.get(n, {}).get("seguro") is True
            and self.muestras[n].get("calidad") == "OK"
            for n in self.drivers
        )

    async def aplicar(self):
        """Aplica revisiones solo con salidas seguras y sin operaciones pendientes."""
        try:
            datos = validar_configuracion(
                await asyncio.wait_for(
                    ejecutar_fuera_del_lazo(leer_archivo, self.ruta), self.timeout_io
                )
            )
            deseada = revision(datos)
            self.revision_deseada = deseada
        except (ValueError, OSError, TimeoutError) as exc:
            self.error_config = str(exc)
            return
        if deseada == self.revision_aplicada:
            self.error_config = None
            return
        if deseada == self.intentada:
            return
        if (
            self._ordenes
            or not self.cola.empty()
            or self._io
            or self.aplicando
            or (self._parando and not self._parando.done())
            or not self.todo_seguro()
        ):
            self.error_config = "Revisión pendiente: detenga las salidas y espere la cola vacía."
            return
        if self._cuarentena:
            self.error_config = "Recurso en cuarentena; reinicie tras revisar la conexión."
            return
        self.aplicando = True
        self.intentada = deseada
        self.revision_aplicada = None
        try:
            for nombre, driver in list(self.drivers.items()):
                await self._llamar(driver, driver.cerrar)
                del self.drivers[nombre]
                self.muestras.pop(nombre, None)
            self.config = datos
            vigentes = {n: c for n, c in datos["componentes"].items() if c["estado"] == "Vigente"}
            if vigentes and self.backend is None:
                self.backend = await asyncio.wait_for(
                    ejecutar_fuera_del_lazo(self.backend_factory), self.timeout_io
                )
            for nombre, config in vigentes.items():
                driver = HardwareFactory.crear_componente(nombre, config, self.backend)
                self.drivers[nombre] = driver  # incluye inicialización parcial en limpieza
                await self._llamar(driver, driver.inicializar)
                await self._leer(nombre, driver)
                if self.muestras.get(nombre, {}).get("calidad") != "OK":
                    raise RuntimeError(f"No se pudo leer {nombre} después de inicializar.")
            self.revision_aplicada = deseada
            self.error_config = None
        except Exception as exc:
            self.error_config = f"No se aplicó la configuración: {exc}"
            # Una inicialización parcial no tiene una lectura válida. Liberar
            # sus recursos permite corregir y reintentar sin quedar esperando
            # para siempre una muestra de un driver que nunca llegó a arrancar.
            for nombre, driver in list(self.drivers.items()):
                driver.cancelar.set()
                if driver.recurso in self._cuarentena:
                    continue
                try:
                    await self._llamar(driver, driver.cerrar, rescate=True)
                    del self.drivers[nombre]
                    self.muestras.pop(nombre, None)
                except Exception:
                    self._cuarentena.add(driver.recurso)
            self.detener()
        finally:
            self.aplicando = False

    async def _escribir(self):
        """Despacha cada orden a su recurso: recursos distintos corren a la vez.

        Las órdenes de un mismo recurso quedan en FIFO (``asyncio.Lock`` despierta a
        sus esperas en orden de llegada) y se ejecutan de a una.
        """
        while not self.cerrado:
            cmd = await self.cola.get()
            try:
                tarea = asyncio.create_task(self._ejecutar_orden(cmd))
                self._ordenes[cmd["id"]] = (cmd, tarea)
                tarea.add_done_callback(lambda _t, i=cmd["id"]: self._ordenes.pop(i, None))
            finally:
                self.cola.task_done()

    async def _ejecutar_orden(self, cmd):
        """Ejecuta una orden cuando su recurso queda libre.

        Plazo: ``vence_en`` se revisa al obtener el turno del propio recurso. Como los
        recursos distintos no se esperan entre sí, el plazo solo cubre la espera
        detrás de órdenes del mismo recurso (y la del despachador), nunca la de otro.
        """
        driver = self.drivers.get(cmd["componente"])
        recurso = driver.recurso if driver else None
        turno = self._turnos.setdefault(recurso, asyncio.Lock())
        try:
            async with turno:
                await self._ejecutar_con_turno(cmd)
        except asyncio.CancelledError:
            # Solo se cancelan órdenes que esperaban turno (``detener`` ya las
            # resolvió) o las que quedan al cerrar el servicio.
            if self.resultados.get(cmd["id"], {}).get("estado") in (None, "EN_COLA", "EJECUTANDO"):
                self._resultado(cmd, "CANCELADO", "Cancelado por parada.")
            raise

    async def _ejecutar_con_turno(self, cmd):
        try:
            if (
                self.emergencia
                or cmd["generacion"] != self.generacion
                or cmd["revision"] != self.revision_aplicada
            ):
                self._resultado(cmd, "CANCELADO", "Orden invalidada por parada/configuración.")
                return
            if cmd["vence_en"] <= time.time():
                self._resultado(cmd, "VENCIDO", "Venció esperando la cola.")
                return
            driver = self.drivers[cmd["componente"]]
            validar_operacion(driver.config, cmd["operacion"], cmd.get("argumentos", {}))
            self._ejecutando[cmd["id"]] = cmd
            self._resultado(cmd, "EJECUTANDO")

            def ejecutar(driver=driver, cmd=cmd):
                """Aplica una operación previamente validada por el gestor."""
                if driver.cancelar.is_set():
                    raise InterruptedError("Orden cancelada antes de acceder al dispositivo.")
                try:
                    salida = driver.ejecutar(cmd["operacion"], cmd.get("argumentos", {}))
                finally:
                    # Un timeout no mata el hilo. Si regresa tarde, apaga
                    # bajo el mismo lock antes de liberar el recurso.
                    if driver.cancelar.is_set():
                        driver.apagar()
                return salida, driver.leer(), driver.seguro()

            timeout = driver.plazo(cmd["operacion"], cmd.get("argumentos", {}))
            salida, valores, seguro = await self._llamar(driver, ejecutar, timeout)
            self._guardar_muestra(cmd["componente"], valores, seguro)
            if cmd["generacion"] != self.generacion:
                self._resultado(cmd, "CANCELADO", "Interrumpido por parada.")
            else:
                self._resultado(
                    cmd,
                    "APLICADO",
                    "Orden aplicada al driver; consulte realimentación.",
                    salida,
                )
        except ValueError as exc:
            self._resultado(cmd, "RECHAZADO", str(exc))
        except InterruptedError as exc:
            self._resultado(cmd, "CANCELADO", str(exc))
        except Exception as exc:
            detalle = str(exc) or type(exc).__name__
            self._resultado(cmd, "FALLIDO", detalle)
            self.detener(f"{cmd['componente']}.{cmd['operacion']}: {detalle}")
        finally:
            self._ejecutando.pop(cmd["id"], None)

    def detener(self, causa=None):
        """Enclava emergencia, cancela cola y solicita apagado fuera de ella.

        ``causa`` dice qué falló cuando el gestor se detiene solo; queda en
        ``causa_parada`` hasta rearmar (conserva la primera).
        """
        if not self.emergencia:
            self.causa_parada = causa
        self.emergencia = True
        self.generacion += 1
        for driver in self.drivers.values():
            driver.cancelar.set()
        while not self.cola.empty():
            cmd = self.cola.get_nowait()
            self._resultado(cmd, "CANCELADO", "Cancelado por parada.")
            self.cola.task_done()
        # Las que esperan el turno de su recurso se cancelan ya y sus esperas se
        # resuelven; las que están tocando un driver terminan por ``cancelar``.
        for identificador, (cmd, tarea) in list(self._ordenes.items()):
            if identificador not in self._ejecutando and not tarea.done():
                self._resultado(cmd, "CANCELADO", "Cancelado por parada.")
                tarea.cancel()
        if self._parando is None or self._parando.done():
            self._parando = asyncio.create_task(self._apagar_todos())

    async def _apagar_todos(self):
        async def apagar(nombre, driver):
            """Desactiva las salidas y descarta el estado de armado."""
            try:

                def operacion():
                    driver.apagar()
                    return driver.leer(), driver.seguro()

                valores, seguro = await self._llamar(driver, operacion, rescate=True)
                self._guardar_muestra(nombre, valores, seguro)
            except Exception as exc:
                self.muestras[nombre] = {
                    **self.muestras.get(nombre, {}),
                    "seguro": False,
                    "calidad": "ERROR",
                    "error": str(exc) or "Timeout apagando",
                }

        await asyncio.gather(*(apagar(n, d) for n, d in self.drivers.items()))
        self.emitir(
            p.EV_HARDWARE,
            {
                "id": str(uuid4()),
                "operacion": "detener",
                "ts": ahora(),
                "estado": "APAGADO"
                if self.todo_seguro() and not self._cuarentena
                else "FALLO_APAGADO",
                "detalle": "Resultado eléctrico; no acredita tiempo de respuesta físico.",
            },
        )

    def rearmar(self):
        """No rearma recursos inciertos ni operaciones que siguen ejecutándose."""
        if (
            self._ordenes
            or self._io
            or self.aplicando
            or self._cuarentena
            or (self._parando and not self._parando.done())
            or not self.todo_seguro()
        ):
            raise ValueError("El apagado no terminó o hay recursos en error; no se puede rearmar.")
        for driver in self.drivers.values():
            driver.cancelar.clear()
        self.emergencia = False
        self.causa_parada = None

    def estado(self):
        """Instantánea de caché: nunca toca pines ni espera lecturas."""
        componentes = {}
        for nombre, config in self.config["componentes"].items():
            muestra = copy.deepcopy(self.muestras.get(nombre, {}))
            edad = time.monotonic() - muestra.pop("monotonic", 0)
            if muestra.get("calidad") == "OK" and edad > 3:
                muestra["calidad"] = "VENCIDO"
            componentes[nombre] = {
                **config,
                **muestra,
                "operaciones": comandos_de(config["tipo"]),
            }
        return {
            "modo_hal": "gpio",
            "revision_aplicada": self.revision_aplicada,
            "revision_deseada": self.revision_deseada,
            "error_configuracion": self.error_config,
            "aplicando": self.aplicando,
            "emergencia": self.emergencia,
            "cola": self.cola.qsize(),
            "capacidad_cola": self.cola.maxsize,
            "ejecutando": self.actual and self.actual["id"],
            "en_ejecucion": list(self._ejecutando),
            "componentes": componentes,
            "resultados": list(self.resultados.values()),
            "ts": ahora(),
            "automatico_disponible": False,
        }

    async def correr(self, parar):
        """Supervisa escritura y muestreo; un fallo del worker termina el servicio."""
        trabajador = asyncio.create_task(self._escribir())
        try:
            while not parar.is_set():
                if trabajador.done():
                    await trabajador
                    raise RuntimeError("El trabajador de hardware terminó inesperadamente.")
                await self.aplicar()
                if not self.aplicando:
                    await asyncio.gather(*(self._leer(n, d) for n, d in self.drivers.items()))
                self._ciclo = time.monotonic()
                await asyncio.sleep(0.25)
        finally:
            self.cerrado = True
            self.detener()
            await self._parando
            trabajador.cancel()
            await asyncio.gather(trabajador, return_exceptions=True)
            # Las órdenes en curso terminan solas (``cancelar`` o el timeout de _llamar).
            ordenes = [tarea for _, tarea in list(self._ordenes.values())]
            await asyncio.gather(*ordenes, return_exceptions=True)
            # No liberar GPIO mientras un hilo tardío todavía puede usarlos.
            if not self._io and not self._cuarentena:
                for driver in self.drivers.values():
                    await self._llamar(driver, driver.cerrar, rescate=True)
                if self.backend:
                    await asyncio.wait_for(
                        ejecutar_fuera_del_lazo(self.backend.cerrar), self.timeout_io
                    )
