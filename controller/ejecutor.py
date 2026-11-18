"""Ejecutor de programas por pasos: recorre el árbol sobre cualquier HAL.

El mismo árbol (``ACCION``, ``ESPERA``, ``BUCLE``, ``PARALELO``) corre igual en
simulado y en GPIO: cada ``ACCION`` es un ``hal.operar`` que vuelve cuando la
orden terminó. Lo que este módulo garantiza es la **ruta única de salida**:
cualquier excepción y el aborto del operador terminan en

1. parada segura (en GPIO ``gestor.detener()``; en simulado, shutter cerrado y
   desarmado, láser apagado y motores frenados);
2. evento ``abortado`` con el motivo.

El fin normal también deja un estado seguro: cada componente cuya ficha declara
``cerrar`` y ``desarmar`` (el shutter) se desarma por ``hal.operar``, igual en
simulado y en GPIO. El láser no se apaga solo, porque encenderlo es un paso del
programa; si quedó encendido se avisa.

La ``Maquina`` completa el paso 3 (``ejecucion = None``) al terminar la tarea.
Una emergencia cancela la tarea: la ``CancelledError`` no pasa por esta ruta,
porque quien cancela ya detuvo el hardware, y nunca se emite ``fin``.

No importa Django ni GPIO (ADR-0002).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from controller import protocolo as p
from controller.hal_base import OperacionRechazada
from controller.hardware_config import catalogo
from controller.programa import resumen

logger = logging.getLogger("controller.ejecutor")

# Desvío relativo de la duración de un pulso a partir del cual se avisa.
TOLERANCIA_PULSO = 0.02


class _Abortado(Exception):  # noqa: N818 - control de flujo interno
    """El operador pidió abortar; se sale por la ruta única."""


def _apagar_salidas_simuladas(hal) -> None:
    """Parada segura del HAL simulado: shutter, láser y motores."""
    hal.apagar_componentes()


def _motor_lineal(config: dict) -> str | None:
    """Primer componente vigente con contrato de motor y unidad ``mm``."""
    fichas = catalogo()
    for nombre, comp in config.get("componentes", {}).items():
        if comp.get("estado") != "Vigente":
            continue
        if fichas.get(comp.get("tipo"), {}).get("contrato") != "MOTOR":
            continue
        if (comp.get("parametros") or {}).get("unidad") == "mm":
            return nombre
    return None


class Ejecutor:
    """Corre un programa ya validado y publica su progreso."""

    def __init__(
        self,
        hal,
        emitir: Callable[[str, dict], None],
        avisar: Callable[..., None],
        *,
        registro: str,
        modo: str,
        inhibidos: set[str],
    ) -> None:
        """Prepara la ejecución.

        Args:
            hal: HAL con ``operar`` e ``inventario`` (simulado o GPIO).
            emitir: Recibe ``(tipo, datos)`` de cada evento a persistir.
            avisar: Recibe ``(texto, nivel)`` para el operador.
            registro: Código del registro de fabricación.
            modo: ``REAL`` o ``PRUEBA``.
            inhibidos: Componentes cuyas acciones no se envían en ``PRUEBA``.
        """
        self.hal = hal
        self._emitir = emitir
        self._avisar = avisar
        self.registro = registro
        self.modo = modo
        self.inhibidos = set(inhibidos)
        self.progreso: dict = {
            "acciones": 0,
            "total": 0,
            "pulsos": 0,
            "ubicacion": "",
            "ultimo_pulso_ms": None,
        }
        self.marcas: list[float | None] = []  # posición (um) de cada marca emitida
        self.omitidas = 0
        # Se llama al empezar y al terminar cada acción, para publicar telemetría.
        self.al_cambiar: Callable[[], None] | None = None
        self._abortar = asyncio.Event()
        self._motor: str | None = None
        self._config: dict = {}
        # Último comando enviado a cada componente, para saber qué quedó encendido.
        self._ultimo: dict[str, str] = {}

    # ── Control ─────────────────────────────────────────────────────────────

    def abortar(self) -> None:
        """Pide terminar después de la acción en curso (nunca corta un pulso)."""
        self._abortar.set()

    def _revisar_aborto(self) -> None:
        if self._abortar.is_set():
            raise _Abortado("Abortado por el operador.")

    def _cambio(self) -> None:
        if self.al_cambiar is not None:
            self.al_cambiar()

    # ── Recorrido ───────────────────────────────────────────────────────────

    async def correr(self, pasos: list) -> None:
        """Ejecuta el árbol; termina con ``fin`` o por la ruta única de salida.

        Args:
            pasos: Árbol normalizado por ``validar_programa``.

        Raises:
            asyncio.CancelledError: Si una emergencia canceló la tarea. No emite
                eventos: quien cancela ya detuvo el hardware.
        """
        try:
            config = self._config = self.hal.inventario()
            self._motor = _motor_lineal(config)
            self.progreso["total"] = resumen(pasos, config)["acciones"]
            await self._lista(pasos, [])
            await self._desarmar_al_terminar()
            self._terminar()
        except Exception as exc:  # noqa: BLE001 - toda falla sale por la misma ruta
            self._salir(exc)

    async def _lista(self, pasos: list, camino: list[int]) -> None:
        for i, paso in enumerate(pasos, 1):
            await self._nodo(paso, [*camino, i])

    async def _nodo(self, paso: dict, camino: list[int], iteracion: str = "") -> None:
        tipo = paso["tipo"]
        if tipo == "BUCLE":
            total = paso["repeticiones"]
            for n in range(1, total + 1):
                self._revisar_aborto()
                vuelta = f" · iteración {n} de {total}"
                for i, hijo in enumerate(paso["pasos"], 1):
                    await self._nodo(hijo, [*camino, i], vuelta)
            return
        # La ubicación nombra el paso y la vuelta del bucle más interno.
        self.progreso["ubicacion"] = "Paso " + ".".join(map(str, camino)) + iteracion
        if tipo == "ACCION":
            await self._accion(paso)
        elif tipo == "ESPERA":
            await self._espera(paso["duracion_ms"])
        else:  # PARALELO
            await self._paralelo(paso["pasos"])

    async def _paralelo(self, hijos: list) -> None:
        """Larga los hijos juntos; si uno falla, cancela al resto y propaga el error."""
        self._revisar_aborto()
        tareas = [asyncio.ensure_future(self._hijo_paralelo(h)) for h in hijos]
        try:
            await asyncio.gather(*tareas)
        except BaseException:
            for tarea in tareas:
                tarea.cancel()
            await asyncio.gather(*tareas, return_exceptions=True)
            raise

    async def _hijo_paralelo(self, hijo: dict) -> None:
        if hijo["tipo"] == "ACCION":
            await self._accion(hijo, revisar=False)
        else:
            await self._espera(hijo["duracion_ms"], revisar=False)

    async def _espera(self, duracion_ms: int, revisar: bool = True) -> None:
        """Espera; un aborto la corta, porque no hay hardware a mitad de una orden."""
        if revisar:
            self._revisar_aborto()
        segundos = duracion_ms / 1000 / (getattr(self.hal, "velocidad", 1.0) or 1.0)
        if not revisar:
            await asyncio.sleep(segundos)
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._abortar.wait(), segundos)
        self._revisar_aborto()

    async def _accion(self, paso: dict, revisar: bool = True) -> None:
        if revisar:
            self._revisar_aborto()
        componente, comando = paso["componente"], paso["comando"]
        if self.modo == "PRUEBA" and componente in self.inhibidos:
            self.omitidas += 1
            logger.info("PRUEBA: omitida %s.%s", componente, comando)
            if self.omitidas == 1:
                self._avisar(f"Modo PRUEBA: se omiten las acciones sobre {componente}.")
            self.progreso["acciones"] += 1
            self._cambio()
            return
        self._cambio()
        self._ultimo[componente] = comando
        salida = await self.hal.operar(componente, comando, paso["argumentos"]) or {}
        if comando == "pulso":
            self._pulso_completado(paso["argumentos"]["duracion_ms"], salida)
        self.progreso["acciones"] += 1
        self._cambio()

    # ── Pulsos y marcas ─────────────────────────────────────────────────────

    def _posicion_um(self) -> float | None:
        if self._motor is None:
            return None
        posicion = self.hal.posicion_de(self._motor)
        return None if posicion is None else round(posicion * 1000, 3)

    def _pulso_completado(self, pedido_ms: float, salida: dict) -> None:
        if salida.get("cancelado"):
            raise OperacionRechazada("shutter.pulso: el pulso se interrumpió antes de completarse.")
        real = salida.get("duracion_real_ms")
        real = float(pedido_ms if real is None else real)
        self.progreso["pulsos"] += 1
        self.progreso["ultimo_pulso_ms"] = round(real, 3)
        desvio = abs(real - pedido_ms) / pedido_ms
        if desvio > TOLERANCIA_PULSO:
            self._avisar(
                f"Pulso {self.progreso['pulsos']}: duró {real:.1f} ms de {pedido_ms} ms "
                f"pedidos ({100 * desvio:.1f} % de desvío).",
                "warn",
            )
        if self.modo == "PRUEBA":
            return  # en PRUEBA no se graba: no hay marca que registrar
        posicion = self._posicion_um()
        self.marcas.append(posicion)
        self._emitir(
            p.EV_MARCA,
            {
                "registro": self.registro,
                "numero": len(self.marcas),
                "tiempo_de_pulso": round(real, 3),
                "cant_pulsos": 1,
                "posicion_um": posicion,
            },
        )

    # ── Salidas ─────────────────────────────────────────────────────────────

    def _espectro(self) -> dict | None:
        if getattr(self.hal, "modo", None) == "gpio" or self.modo == "PRUEBA":
            return None
        posiciones = [x for x in self.marcas if x is not None]
        if len(self.marcas) < 2 or len(posiciones) != len(self.marcas):
            return None
        separaciones = [abs(b - a) for a, b in zip(posiciones, posiciones[1:], strict=False)]
        periodo_um = sum(separaciones) / len(separaciones)
        if periodo_um <= 0:
            return None
        lambdas, dbs = self.hal.espectro(periodo_um, len(self.marcas))
        return {"longitudes_onda": lambdas, "transmitancias": dbs}

    async def _desarmar_al_terminar(self) -> None:
        """Cierra y desarma por el HAL todo componente cuya ficha declara ambos comandos.

        Genérico, sin nombrar tipos: hoy es el shutter. En ``PRUEBA`` se respetan
        los componentes inhibidos. Si falla, la excepción sale por la ruta única.
        """
        fichas = catalogo()
        for nombre, comp in self._config.get("componentes", {}).items():
            if comp.get("estado") != "Vigente":
                continue
            if self.modo == "PRUEBA" and nombre in self.inhibidos:
                continue
            comandos = fichas.get(comp.get("tipo"), {}).get("operaciones", {})
            if "cerrar" in comandos and "desarmar" in comandos:
                await self.hal.operar(nombre, "desarmar", {})

    def _laser_encendido(self) -> bool:
        """Si el láser quedó encendido: estado del HAL o último comando del programa."""
        if getattr(self.hal, "modo", None) != "gpio":
            return bool(getattr(self.hal, "laser", False))
        laseres = [
            nombre
            for nombre, comp in self._config.get("componentes", {}).items()
            if comp.get("rol") == "LASER" and comp.get("estado") == "Vigente"
        ]
        if any(self._ultimo.get(nombre) == "encender" for nombre in laseres):
            return True
        if any(self._ultimo.get(nombre) == "apagar" for nombre in laseres):
            return False
        roles = getattr(self.hal, "roles", None)
        return roles is not None and roles()["actuadores"].get("laser") is True

    def _encendidos_sin_rol_laser(self) -> list[str]:
        """Sin rol ``LASER`` asignado: lo que el programa encendió y no apagó."""
        componentes = self._config.get("componentes", {})
        if any(c.get("rol") == "LASER" for c in componentes.values()):
            return []
        return sorted(n for n, comando in self._ultimo.items() if comando == "encender")

    def _terminar(self) -> None:
        marcas = len(self.marcas)
        self._emitir(
            p.EV_FIN, {"registro": self.registro, "marcas": marcas, "espectro": self._espectro()}
        )
        if self._laser_encendido():
            self._avisar(
                f"Programa terminado: {marcas} marcas. Quedó con el láser encendido: "
                "apáguelo si no lo va a usar.",
                "warn",
            )
        elif encendidos := self._encendidos_sin_rol_laser():
            self._avisar(
                f"Programa terminado: {marcas} marcas. Quedaron encendidos: "
                f"{', '.join(encendidos)}.",
                "warn",
            )
        else:
            self._avisar(f"Programa terminado: {marcas} marcas.")

    def _parada_segura(self) -> None:
        if getattr(self.hal, "modo", None) == "gpio":
            self.hal.hardware.detener()
        else:
            _apagar_salidas_simuladas(self.hal)

    def _salir(self, exc: Exception) -> None:
        """Ruta única de salida: parada segura y ``abortado`` con el motivo."""
        try:
            self._parada_segura()
        except Exception:  # noqa: BLE001 - el evento se emite igual
            logger.exception("Falló la parada segura del programa %s", self.registro)
        motivo = str(exc) or type(exc).__name__
        if not isinstance(exc, _Abortado | OperacionRechazada):
            logger.exception("Programa %s interrumpido por una falla", self.registro, exc_info=exc)
            motivo = f"Falla del controlador: {motivo}"
        marcas = len(self.marcas)
        self._emitir(p.EV_ABORTADO, {"registro": self.registro, "marcas": marcas, "motivo": motivo})
        self._avisar(f"Programa abortado después de {marcas} marcas: {motivo}", "warn")
