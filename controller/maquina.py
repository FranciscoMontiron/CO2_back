"""Maquina de estados del arreglo de grabado y ejecucion de programas.

Estados (``EstadoSistema`` del diagrama): REPOSO, PREPARANDO, LISTO, GRABANDO y
EMERGENCIA. La transicion a LISTO no se comanda: ocurre sola cuando se cumplen
las cuatro ``CondicionSeguridad`` (refrigeracion, alta tension, fibra alineada y
shutter armado), y se revierte sola si alguna se pierde.

No depende de Redis ni de Django: recibe comandos como diccionarios y entrega
eventos a una funcion. Eso la hace probable sin infraestructura, y deja el
transporte en ``main.py``.

Reglas que hace cumplir:

* **RN003**: el lazo de potencia solo se cierra con el laser encendido, y el
  laser solo se enciende con el sistema LISTO.
* **Orden del apagado seguro**: shutter, laser y alta tension, en ese orden. La
  refrigeracion queda encendida: el tubo sigue caliente (ADR-0002).
* **Umbrales**: se evaluan en cada ciclo mientras haya alta tension, que es
  cuando hay energia que puede danar algo. Sin alta tension un caudal nulo no
  es un riesgo, es el estado normal de un equipo apagado.
* **Rearme explicito**: despues de una emergencia el sistema no vuelve solo.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from controller import protocolo as p
from controller.ejecutor import Ejecutor
from controller.hal_base import HalBase
from controller.programa import resumen, validar_programa
from controller.simulador import TIEMPO_SHUTTER_S

logger = logging.getLogger("controller.maquina")

REPOSO, PREPARANDO, LISTO, GRABANDO, EMERGENCIA = (
    "REPOSO",
    "PREPARANDO",
    "LISTO",
    "GRABANDO",
    "EMERGENCIA",
)

# Latencia de los actuadores en un apagado seguro: el shutter mecanico mas los
# reles del laser y de la fuente de alta tension. Se suma al tiempo medido por
# software para reportar la respuesta completa en `tiempo_respuesta_ms` (RNF001).
LATENCIA_ACTUADORES_MS = TIEMPO_SHUTTER_S * 1000 + 15

# Umbrales con los que arranca el controlador si todavia no recibio la
# configuracion vigente. Son los mismos de la carga inicial: el controlador nunca
# corre sin proteccion, aunque Django no le haya hablado todavia.
UMBRALES_POR_DEFECTO = [
    {
        "variable": "TEMPERATURA_AGUA",
        "valor_min": 15.0,
        "valor_max": 28.0,
        "accion": "PARADA_EMERGENCIA",
    },
    {
        "variable": "CAUDAL_REFRIGERANTE",
        "valor_min": 2.0,
        "valor_max": 10.0,
        "accion": "PARADA_EMERGENCIA",
    },
    {"variable": "TENSION_AT", "valor_min": 0.0, "valor_max": 30.0, "accion": "BLOQUEAR_COMANDO"},
    {
        "variable": "CORRIENTE_AT",
        "valor_min": 0.0,
        "valor_max": 25.0,
        "accion": "PARADA_EMERGENCIA",
    },
]

# Roles cuyo dato se exige vigente y dentro de umbral para iniciar en GPIO.
# Regla provisoria: pregunta abierta 1 del spec de programas por pasos.
ROLES_CRITICOS = ("TEMPERATURA_AGUA", "CAUDAL_REFRIGERANTE", "TENSION_AT")


def _ahora_iso() -> str:
    return datetime.now(UTC).isoformat()


class ComandoRechazadoError(Exception):
    """El comando no se puede ejecutar en el estado actual."""


class Maquina:
    """Controlador del arreglo: estado, comandos, programa y seguridad."""

    def __init__(
        self,
        hal: HalBase,
        emitir: Callable[[str, dict], None],
        velocidad: float = 1.0,
    ) -> None:
        """Arranca la maquina en reposo.

        Args:
            hal: El hardware (simulado o real) detras de la interfaz comun.
            emitir: Recibe ``(tipo, datos)`` de cada evento a persistir.
            velocidad: Factor de aceleracion del tiempo simulado. 1 es tiempo
                real; las pruebas usan valores altos.
        """
        self.hal = hal
        self._emitir = emitir
        self.velocidad = velocidad
        hal.velocidad = velocidad  # el HAL simulado escala sus esperas con el mismo factor
        self.estado = REPOSO
        self.umbrales: list[dict] = list(UMBRALES_POR_DEFECTO)
        self.bloqueado = False
        self.ejecucion: dict | None = None
        self.mensaje: dict | None = None
        self.hubo_cambio = True  # pide publicar telemetria fuera de turno
        self._excursiones: set[str] = set()
        self._tarea: asyncio.Task | None = None
        self._ejecutor: Ejecutor | None = None

    # ── Estado ──────────────────────────────────────────────────────────────

    def condiciones(self) -> dict[str, bool]:
        """Evalua las cuatro condiciones de armado (``CondicionSeguridad``)."""
        if getattr(self.hal, "modo", None) == "gpio":
            return dict.fromkeys(
                ("REFRIGERACION_OK", "AT_ENCENDIDA", "FIBRA_ALINEADA", "SHUTTER_ARMADO"), False
            )
        lect = self.hal.lecturas()
        lim = {u["variable"]: u for u in self.umbrales}
        caudal_min = lim.get("CAUDAL_REFRIGERANTE", {}).get("valor_min", 2.0)
        temp_max = lim.get("TEMPERATURA_AGUA", {}).get("valor_max", 28.0)
        return {
            "REFRIGERACION_OK": (
                self.hal.refrigeracion
                and lect["CAUDAL_REFRIGERANTE"] >= caudal_min
                and lect["TEMPERATURA_AGUA"] <= temp_max
            ),
            "AT_ENCENDIDA": self.hal.alta_tension and self.hal.tension_kv >= 15.0,
            "FIBRA_ALINEADA": self.hal.fibra_alineada,
            "SHUTTER_ARMADO": self.hal.shutter_armado and not self.hal.shutter_abierto,
        }

    def _avisar(self, texto: str, nivel: str = "info") -> None:
        self.mensaje = {"texto": texto, "nivel": nivel, "ts": _ahora_iso()}
        self.hubo_cambio = True
        logger.info("[%s] %s", nivel, texto)

    def _pasar_a(self, estado: str) -> None:
        if estado != self.estado:
            logger.info("estado %s -> %s", self.estado, estado)
            self.estado = estado
            self.hubo_cambio = True

    def _actualizar_estado(self) -> None:
        if self.estado in (EMERGENCIA, GRABANDO):
            return
        cond = self.condiciones()
        if all(cond.values()):
            self._pasar_a(LISTO)
            return
        if self.hal.laser:
            # Se perdio una condicion con el laser encendido: se apaga, no se
            # deja emitiendo con el sistema fuera de condiciones.
            self.hal.laser = False
            self._avisar("Se perdio una condicion de armado: laser apagado.", "warn")
        algo_encendido = (
            self.hal.refrigeracion
            or self.hal.alta_tension
            or self.hal.shutter_armado
            or self.hal.fibra_alineada
            or self.hal.alineando
        )
        self._pasar_a(PREPARANDO if algo_encendido else REPOSO)

    # ── Ciclo ───────────────────────────────────────────────────────────────

    def tick(self, dt: float) -> None:
        """Un ciclo del lazo: avanza la fisica, actualiza el estado y vigila.

        Args:
            dt: Tiempo simulado transcurrido desde el ciclo anterior, en s.
        """
        if getattr(self.hal, "modo", None) == "gpio":
            if self._corriendo() and self.hal.hardware.emergencia:
                # El gestor se detuvo solo (falla de una orden o de una lectura): el
                # programa no sigue, aunque esté en una espera sin hardware.
                self._interrumpir_gpio(self._motivo_parada_gestor())
            self._actualizar_estado_gpio()
            return
        self.hal.avanzar(dt)
        self._actualizar_estado()
        if self.hal.alta_tension:
            self._evaluar_umbrales()

    def _corriendo(self) -> bool:
        return self._tarea is not None and not self._tarea.done()

    def _actualizar_estado_gpio(self) -> None:
        gestor = self.hal.hardware
        if gestor.emergencia:
            self._pasar_a(EMERGENCIA)
        elif self._corriendo():
            self._pasar_a(GRABANDO)
        else:
            self._pasar_a(REPOSO if gestor.todo_seguro() else PREPARANDO)

    def _motivo_parada_gestor(self) -> str:
        """Motivo de una parada que el gestor decidió solo, con lo que falló."""
        causa = self.hal.hardware.causa_parada
        motivo = "Hardware detenido durante el programa"
        return f"{motivo}: {causa}" if causa else f"{motivo}."

    def _interrumpir_gpio(self, motivo: str) -> None:
        """Emergencia en GPIO: cancela el programa al instante y cierra el registro.

        El gestor ya cortó las salidas (``detener``). En GPIO no se emite el evento
        ``emergencia``, que acreditaría un tiempo de respuesta que no se midió;
        el registro se cierra con ``abortado``.
        """
        ejecutor, ejecucion = self._ejecutor, self.ejecucion
        if not self._corriendo() or ejecutor is None or ejecucion is None:
            return
        self._tarea.cancel()
        self.ejecucion = None
        self._ejecutor = None
        self._emitir(
            p.EV_ABORTADO,
            {"registro": ejecucion["registro"], "marcas": len(ejecutor.marcas), "motivo": motivo},
        )
        self._avisar(
            f"Programa abortado después de {len(ejecutor.marcas)} marcas: {motivo}", "warn"
        )

    def _evaluar_umbrales(self) -> None:
        lect = self.hal.lecturas()
        bloquear = False
        for u in self.umbrales:
            valor = lect.get(u["variable"])
            if valor is None:
                continue
            fuera = not (u["valor_min"] <= valor <= u["valor_max"])
            if not fuera:
                self._excursiones.discard(u["variable"])
                continue
            if u["accion"] == "BLOQUEAR_COMANDO":
                bloquear = True
            if u["variable"] in self._excursiones:
                continue  # ya se aviso de esta excursion: una alerta por evento
            self._excursiones.add(u["variable"])
            self._emitir(p.EV_ALERTA, {"variable": u["variable"], "valor": valor})
            if u["accion"] == "PARADA_EMERGENCIA":
                rango = f"[{u['valor_min']}, {u['valor_max']}]"
                self._parada(
                    origen="UMBRAL",
                    descripcion=f"{u['variable']} = {valor} fuera de {rango}.",
                    t_origen=time.time(),
                )
                return
        if bloquear != self.bloqueado:
            self.bloqueado = bloquear
            self.hubo_cambio = True

    # ── Comandos ────────────────────────────────────────────────────────────

    async def manejar(self, comando: dict) -> None:
        """Ejecuta un comando. Si no corresponde, lo rechaza con un aviso.

        Args:
            comando: ``{"accion": ..., ...}`` tal como llega por el stream.
        """
        accion = comando.get("accion")
        if getattr(self.hal, "modo", None) == "gpio" and accion not in (p.INICIAR, p.ABORTAR):
            gestor = self.hal.hardware
            try:
                if accion in (p.EMERGENCIA, p.APAGAR):
                    gestor.detener()
                    self._interrumpir_gpio(
                        comando.get("motivo") or "Parada de emergencia durante el programa."
                    )
                    self._pasar_a(EMERGENCIA)
                    self._avisar("Parada solicitada. Esperando apagado de los componentes.", "warn")
                elif self._corriendo() and accion in (p.COMPONENTE, p.REARMAR):
                    raise ValueError("Hay un programa en ejecución: abórtelo antes de operar.")
                elif accion == p.COMPONENTE:
                    resultado = gestor.encolar(comando)
                    detalle = resultado.get("detalle") or comando["componente"]
                    self._avisar(f"{resultado['estado']}: {detalle}")
                elif accion == p.REARMAR:
                    gestor.rearmar()
                    self._pasar_a(REPOSO)
                elif accion == p.APLICAR_HARDWARE:
                    gestor.intentada = None
                else:
                    raise ValueError(
                        "Modo GPIO: opere por componente o inicie un programa por pasos."
                    )
            except ValueError as exc:
                self._avisar(str(exc), "error")
            return
        try:
            manejador = self._manejadores()[accion]
        except KeyError:
            self._avisar(f"Comando desconocido: {accion}", "error")
            return
        try:
            resultado = manejador(comando)
            if asyncio.iscoroutine(resultado):
                await resultado
        except ComandoRechazadoError as exc:
            self._avisar(f"Comando rechazado: {exc}", "error")
            if accion == p.INICIAR and comando.get("registro"):
                # Django ya creo el registro EN_CURSO antes de mandar el comando.
                # Si no se avisa, quedaria "en curso" para siempre.
                self._emitir(
                    p.EV_ABORTADO,
                    {"registro": comando["registro"], "marcas": 0, "motivo": f"Rechazado: {exc}"},
                )

    def _manejadores(self) -> dict[str, Callable[[dict], object]]:
        return {
            p.ENCENDER_REFRIGERACION: self._encender_refrigeracion,
            p.APAGAR_REFRIGERACION: self._apagar_refrigeracion,
            p.HABILITAR_AT: self._habilitar_at,
            p.DESHABILITAR_AT: self._deshabilitar_at,
            p.ALINEAR_FIBRA: self._alinear_fibra,
            p.ARMAR_SHUTTER: self._armar_shutter,
            p.DESARMAR_SHUTTER: self._desarmar_shutter,
            p.ENCENDER_LASER: self._encender_laser,
            p.APAGAR_LASER: self._apagar_laser,
            p.MOVER_MOTOR: self._mover_motor,
            p.INICIAR: self._iniciar,
            p.ABORTAR: self._pedir_aborto,
            p.APAGAR: self._apagar,
            p.EMERGENCIA: self._emergencia,
            p.REARMAR: self._rearmar,
            p.INYECTAR_FALLA: self._inyectar_falla,
        }

    def _exigir_no(self, *estados: str) -> None:
        if self.estado in estados:
            raise ComandoRechazadoError(f"no se puede en estado {self.estado}.")

    def _encender_refrigeracion(self, _cmd: dict) -> None:
        self._exigir_no(EMERGENCIA)
        self.hal.refrigeracion = True
        self._avisar("Refrigeracion encendida. Esperando caudal y temperatura estables.")

    def _apagar_refrigeracion(self, _cmd: dict) -> None:
        if self.hal.alta_tension:
            raise ComandoRechazadoError(
                "primero deshabilite la alta tension: el tubo necesita refrigeracion."
            )
        self.hal.refrigeracion = False
        self._avisar("Refrigeracion apagada.")

    def _habilitar_at(self, _cmd: dict) -> None:
        self._exigir_no(EMERGENCIA)
        if not self.condiciones()["REFRIGERACION_OK"]:
            raise ComandoRechazadoError(
                "la refrigeracion no esta en condiciones (caudal o temperatura)."
            )
        self.hal.alta_tension = True
        self._avisar("Fuente de alta tension habilitada. Semaforo en amarillo intermitente.")

    def _deshabilitar_at(self, _cmd: dict) -> None:
        self._exigir_no(GRABANDO)
        self.hal.laser = False
        self.hal.alta_tension = False
        self._avisar("Fuente de alta tension deshabilitada.")

    def _alinear_fibra(self, _cmd: dict) -> None:
        self._exigir_no(EMERGENCIA, GRABANDO)
        self.hal.alinear()
        self._avisar("Alineando la fibra con el sensor de sombra...")

    def _armar_shutter(self, _cmd: dict) -> None:
        self._exigir_no(EMERGENCIA)
        self.hal.armar_shutter()

    def _desarmar_shutter(self, _cmd: dict) -> None:
        self._exigir_no(GRABANDO)
        if self.hal.laser:
            raise ComandoRechazadoError("apague el laser antes de desarmar el shutter.")
        self.hal.desarmar_shutter()

    def _encender_laser(self, _cmd: dict) -> None:
        if self.estado != LISTO:
            raise ComandoRechazadoError(
                "el laser solo se enciende con el sistema LISTO (4 condiciones)."
            )
        if self.bloqueado:
            raise ComandoRechazadoError("hay un umbral que bloquea los comandos.")
        self.hal.laser = True
        self._avisar("Laser encendido y lazo de potencia cerrado. Semaforo en rojo.")

    def _apagar_laser(self, _cmd: dict) -> None:
        self._exigir_no(GRABANDO)
        self.hal.laser = False
        self._avisar("Laser apagado: lazo de potencia abierto.")

    def _mover_motor(self, cmd: dict) -> None:
        self._exigir_no(GRABANDO, EMERGENCIA)
        try:
            destino = float(cmd.get("posicion_mm", 0.0))
        except (TypeError, ValueError) as exc:
            raise ComandoRechazadoError("posicion invalida.") from exc
        if not 0.0 <= destino <= p.CARRERA_MOTOR_MM:
            raise ComandoRechazadoError(
                f"la posicion tiene que estar entre 0 y {p.CARRERA_MOTOR_MM} mm."
            )
        segundos = self.hal.mover_a(destino)
        self._avisar(f"Desplazador yendo a {destino:.3f} mm ({segundos:.1f} s).")

    def _inyectar_falla(self, cmd: dict) -> None:
        falla = cmd.get("falla", p.FALLA_NINGUNA)
        if falla not in p.FALLAS:
            raise ComandoRechazadoError(f"falla desconocida: {falla}.")
        self.hal.falla = falla
        texto = (
            "Falla simulada retirada." if falla == p.FALLA_NINGUNA else f"Falla simulada: {falla}."
        )
        self._avisar(texto, "info" if falla == p.FALLA_NINGUNA else "warn")

    # ── Emergencia ──────────────────────────────────────────────────────────

    def _emergencia(self, cmd: dict) -> None:
        t_envio = cmd.get("t_envio")
        self._parada(
            origen=cmd.get("origen", "OPERADOR"),
            descripcion=cmd.get("motivo", "Parada de emergencia solicitada por el operador."),
            t_origen=t_envio / 1000 if t_envio else time.time(),
        )

    def _parada(self, origen: str, descripcion: str, t_origen: float) -> None:
        """Apagado seguro, en orden: shutter, laser, alta tension.

        Args:
            origen: ``OrigenEmergencia`` del evento.
            descripcion: Que la provoco.
            t_origen: Instante (epoch, s) desde el que se mide la respuesta: el
                envio del comando o la deteccion del umbral.
        """
        registro = self.ejecucion["registro"] if self.ejecucion else None
        if self._tarea and not self._tarea.done():
            self._tarea.cancel()
        self.hal.shutter_abierto = False
        self.hal.laser = False
        self.hal.alta_tension = False
        self.hal.potencia_objetivo_mw = 0.0
        self.ejecucion = None
        self._pasar_a(EMERGENCIA)
        respuesta = int((time.time() - t_origen) * 1000 + LATENCIA_ACTUADORES_MS)
        self._emitir(
            p.EV_EMERGENCIA,
            {
                "origen": origen,
                "descripcion": descripcion,
                "tiempo_respuesta_ms": respuesta,
                "registro": registro,
            },
        )
        self._avisar(f"PARADA DE EMERGENCIA ({respuesta} ms). {descripcion}", "error")

    def _rearmar(self, _cmd: dict) -> None:
        if self.estado != EMERGENCIA:
            raise ComandoRechazadoError("solo se rearma despues de una emergencia.")
        self._excursiones.clear()
        self.bloqueado = False
        self.estado = REPOSO
        self.hubo_cambio = True
        self._actualizar_estado()
        self._avisar("Sistema rearmado. Repita la secuencia de armado.")

    # ── Programa ────────────────────────────────────────────────────────────

    def _iniciar(self, cmd: dict) -> None:
        """Arranca un programa de forma atomica: o corre su tarea, o no queda nada.

        Si algo falla despues de los controles (al preparar la ejecucion), se
        deshace lo hecho y se rechaza; ``manejar`` emite entonces ``abortado``.
        """
        gpio = getattr(self.hal, "modo", None) == "gpio"
        if self._corriendo():
            raise ComandoRechazadoError("ya hay un programa en ejecucion.")
        if gpio:
            if self.hal.hardware.emergencia:
                raise ComandoRechazadoError("el hardware esta en emergencia: rearme primero.")
        elif self.estado != LISTO:
            raise ComandoRechazadoError("el programa solo arranca con el sistema LISTO.")
        if self.bloqueado:
            raise ComandoRechazadoError("hay un umbral que bloquea los comandos.")
        estado_previo = self.estado
        try:
            self._arrancar(cmd, gpio)
        except ComandoRechazadoError:
            raise
        except Exception as exc:  # noqa: BLE001 - se deshace y se informa al registro
            logger.exception("No se pudo iniciar el programa")
            if self._tarea is not None and not self._tarea.done():
                self._tarea.cancel()
            self.ejecucion = None
            self._ejecutor = None
            self.estado = estado_previo
            self.hubo_cambio = True
            raise ComandoRechazadoError(f"no se pudo iniciar: {exc}") from exc

    def _arrancar(self, cmd: dict, gpio: bool) -> None:
        programa = cmd["programa"]
        inventario = self.hal.inventario()
        try:
            pasos = validar_programa(programa.get("pasos"), inventario)
        except ValueError as exc:
            raise ComandoRechazadoError(str(exc)) from exc
        aviso = self._exigir_roles_criticos(inventario) if gpio else None
        modo = cmd.get("modo", "REAL")
        registro = cmd["registro"]
        inhibidos = {
            nombre
            for nombre, comp in inventario.get("componentes", {}).items()
            if comp.get("inhibir_en_prueba")
        }
        ejecutor = Ejecutor(
            self.hal,
            self._emitir_de_ejecucion(registro, resumen(pasos, inventario)["pulsos"]),
            self._avisar,
            registro=registro,
            modo=modo,
            inhibidos=inhibidos,
        )
        ejecutor.al_cambiar = self._marcar_cambio
        self._ejecutor = ejecutor
        self.ejecucion = {
            "registro": registro,
            "programa": programa.get("nombre", programa.get("codigo")),
            "modo": modo,
        }
        self._pasar_a(GRABANDO)
        self._checkpoint(registro, "TIEMPO")
        self._tarea = asyncio.create_task(self._correr_programa(ejecutor, pasos))
        grabando = f"Grabando {self.ejecucion['programa']} ({registro})."
        # Un solo ``mensaje``: el aviso de arranque va junto, para que no se pierda.
        if aviso:
            self._avisar(f"{grabando} {aviso}", "warn")
        else:
            self._avisar(grabando)

    def _exigir_roles_criticos(self, inventario: dict) -> str:
        """En GPIO: dato vigente y dentro de umbral en cada rol critico asignado.

        Un rol sin asignar no impide arrancar, pero se avisa. Siempre se avisa que
        en GPIO los umbrales no se vigilan durante la corrida. Regla provisoria
        (preguntas abiertas 1 y 5 del spec).

        Returns:
            El aviso para el operador; quien llama lo publica junto al «Grabando…».
        """
        asignados = {
            comp.get("rol")
            for comp in inventario.get("componentes", {}).values()
            if comp.get("estado") == "Vigente"
        }
        variables = self.hal.lecturas()
        sin_asignar = []
        for rol in ROLES_CRITICOS:
            if rol not in asignados:
                sin_asignar.append(rol)
                continue
            valor = variables.get(rol)
            if valor is None:
                raise ComandoRechazadoError(f"{rol} no tiene un dato vigente.")
            for u in self.umbrales:
                if u["variable"] == rol and not u["valor_min"] <= valor <= u["valor_max"]:
                    rango = f"[{u['valor_min']}, {u['valor_max']}]"
                    raise ComandoRechazadoError(f"{rol} = {valor} fuera de {rango}.")
        aviso = (
            "En GPIO las lecturas críticas solo se verifican al iniciar; "
            "durante el programa no se vigilan umbrales."
        )
        if sin_asignar:
            aviso = f"Sin rol asignado para {', '.join(sin_asignar)}: no se verificaron. {aviso}"
        return aviso

    def _marcar_cambio(self) -> None:
        self.hubo_cambio = True

    def _emitir_de_ejecucion(self, registro: str, pulsos: int) -> Callable[[str, dict], None]:
        """Reenvia los eventos del ejecutor y asienta un checkpoint cada cuarto de marcas."""
        cada = max(1, pulsos // 4)

        def emitir(tipo: str, datos: dict) -> None:
            self._emitir(tipo, datos)
            if tipo == p.EV_MARCA and datos["numero"] % cada == 0:
                self._checkpoint(registro, "MARCA")

        return emitir

    async def _correr_programa(self, ejecutor: Ejecutor, pasos: list) -> None:
        """Corre el ejecutor y libera la ejecucion al terminar, de cualquier forma.

        El ejecutor ya hizo la parada segura y emitio ``fin`` o ``abortado``; si
        lo cancelo una emergencia, quien cancelo ya detuvo el hardware.
        """
        try:
            await ejecutor.correr(pasos)
        finally:
            if self._ejecutor is ejecutor:
                self.ejecucion = None
                self._ejecutor = None
                self.hubo_cambio = True
                if self.estado == GRABANDO:
                    if getattr(self.hal, "modo", None) == "gpio":
                        self._actualizar_estado_gpio()
                    else:
                        self._fin_simulado(len(ejecutor.marcas))

    def _fin_simulado(self, marcas: int) -> None:
        """Vuelve a evaluar el estado sin perder el mensaje del fin.

        El fin normal desarma el shutter: se pierde LISTO y el enclavamiento apaga el
        láser. Su aviso no pisa «Programa terminado…»: va en el mismo mensaje. (Tras un
        aborto el láser ya está apagado y esto no aplica.)
        """
        laser = self.hal.laser
        self.estado = LISTO
        self._actualizar_estado()
        if laser and not self.hal.laser:
            self._avisar(
                f"Programa terminado: {marcas} marcas · shutter desarmado: "
                "el enclavamiento apagó el láser.",
                "warn",
            )

    def _pedir_aborto(self, _cmd: dict) -> None:
        if self._ejecutor is None or not self._corriendo():
            raise ComandoRechazadoError("no hay un programa en ejecucion.")
        self._ejecutor.abortar()
        self._avisar("Abortando el programa al terminar la accion en curso...", "warn")

    def _apagar(self, _cmd: dict) -> None:
        self._exigir_no(GRABANDO, EMERGENCIA)
        # Orden inverso al armado (paso 9 del procedimiento).
        self.hal.shutter_abierto = False
        self.hal.laser = False
        self.hal.alta_tension = False
        self.hal.shutter_armado = False
        self.hal.desalinear()
        self.hal.refrigeracion = False
        self._avisar("Equipo apagado en orden inverso.")

    def _checkpoint(self, registro: str, tipo: str) -> None:
        self._emitir(
            p.EV_CHECKPOINT,
            {
                "registro": registro,
                "tipo": tipo,
                "estado_sistema": self.estado,
                "valores": self.hal.lecturas(),
            },
        )

    # ── Telemetria ──────────────────────────────────────────────────────────

    def _ejecucion_publicada(self) -> dict | None:
        """Registro, programa y modo junto con el progreso del ejecutor."""
        if self.ejecucion is None or self._ejecutor is None:
            return None
        return {**self.ejecucion, **self._ejecutor.progreso}

    def telemetria(self, hz: float) -> dict:
        """Arma el mensaje de telemetria que se publica al front (ADR-0007).

        Args:
            hz: Frecuencia de publicacion, para que el front la muestre.

        Returns:
            El mensaje completo, serializable a JSON.
        """
        hal = self.hal
        if getattr(hal, "modo", None) == "gpio":
            roles = hal.roles()
            return {
                "ts": _ahora_iso(),
                "modo_hal": "gpio",
                "estado": self.estado,
                "condiciones": self.condiciones(),
                "variables": roles["variables"],
                "senales": roles["senales"],
                "actuadores": roles["actuadores"],
                "extra": {
                    "potencia_optica_mw": None,
                    **roles["extra"],
                    "frecuencia_hz": None,
                },
                "ejecucion": self._ejecucion_publicada(),
                "falla": "ninguna",
                "bloqueado": self.bloqueado,
                "mensaje": self.mensaje,
                "hz": hz,
                "hardware": hal.hardware.estado(),
            }
        return {
            "ts": _ahora_iso(),
            "modo_hal": "simulado",
            "estado": self.estado,
            "condiciones": self.condiciones(),
            "variables": hal.lecturas(),
            "senales": hal.senales(),
            "actuadores": {
                "refrigeracion": hal.refrigeracion,
                "alta_tension": hal.alta_tension,
                "laser": hal.laser,
                "lazo_cerrado": hal.laser,
                "shutter_armado": hal.shutter_armado,
                "shutter_abierto": hal.shutter_abierto,
                "alineando": hal.alineando,
            },
            "extra": {
                "potencia_optica_mw": round(hal.potencia_optica_mw, 3),
                "sensor_sombra_mw": round(hal.sensor_sombra_mw, 3),
                "temperatura_laser": round(hal.temp_laser, 2),
                "frecuencia_hz": None,
            },
            "ejecucion": self._ejecucion_publicada(),
            "falla": hal.falla,
            "bloqueado": self.bloqueado,
            "mensaje": self.mensaje,
            "hz": hz,
        }
