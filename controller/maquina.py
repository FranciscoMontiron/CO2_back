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
from controller.simulador import TIEMPO_SHUTTER_S, HalSimulado

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

# Tope para el criterio MANUAL, que no tiene cantidad de pulsos: el operador lo
# corta con abortar, pero un programa que no termina nunca no es aceptable.
MAXIMO_PULSOS_MANUAL = 2000


def distancia_de_marca(periodo: dict, n: int) -> float:
    """Periodo de la marca ``n``, en micrometros.

    Replica ``programas.models.Periodo.calcular_distancia``. Se duplica a
    proposito porque el controlador no puede importar Django; una prueba
    verifica que las dos den lo mismo para los tres perfiles.

    Args:
        periodo: ``{"tipo", "base_um", "incremento_um", "factor"}``.
        n: Indice de la marca, desde 0.

    Returns:
        El periodo en um.
    """
    base = periodo["base_um"]
    if periodo.get("tipo") == "LINEAL":
        return base + (periodo.get("incremento_um") or 0.0) * n
    if periodo.get("tipo") == "EXPONENCIAL":
        return base * ((periodo.get("factor") or 1.0) ** n)
    return base


def _ahora_iso() -> str:
    return datetime.now(UTC).isoformat()


class ComandoRechazadoError(Exception):
    """El comando no se puede ejecutar en el estado actual."""


class Maquina:
    """Controlador del arreglo: estado, comandos, programa y seguridad."""

    def __init__(
        self,
        hal: HalSimulado,
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
        self.estado = REPOSO
        self.umbrales: list[dict] = list(UMBRALES_POR_DEFECTO)
        self.bloqueado = False
        self.ejecucion: dict | None = None
        self.mensaje: dict | None = None
        self.hubo_cambio = True  # pide publicar telemetria fuera de turno
        self._excursiones: set[str] = set()
        self._tarea: asyncio.Task | None = None
        self._abortar = False

    # ── Estado ──────────────────────────────────────────────────────────────

    def condiciones(self) -> dict[str, bool]:
        """Evalua las cuatro condiciones de armado (``CondicionSeguridad``)."""
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
        self.hal.avanzar(dt)
        self._actualizar_estado()
        if self.hal.alta_tension:
            self._evaluar_umbrales()

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
        self.hal.shutter_armado = True
        self.hal.shutter_abierto = False
        self._avisar("Shutter armado y cerrado.")

    def _desarmar_shutter(self, _cmd: dict) -> None:
        self._exigir_no(GRABANDO)
        if self.hal.laser:
            raise ComandoRechazadoError("apague el laser antes de desarmar el shutter.")
        self.hal.shutter_armado = False
        self._avisar("Shutter desarmado.")

    def _encender_laser(self, _cmd: dict) -> None:
        if self.estado != LISTO:
            raise ComandoRechazadoError(
                "el laser solo se enciende con el sistema LISTO (4 condiciones)."
            )
        if self.bloqueado:
            raise ComandoRechazadoError("hay un umbral que bloquea los comandos.")
        self.hal.laser = True
        self._avisar("Laser encendido y lazo de potencia cerrado (RN003). Semaforo en rojo.")

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
        if self.estado != LISTO:
            raise ComandoRechazadoError("el programa solo arranca con el sistema LISTO.")
        if not self.hal.laser:
            raise ComandoRechazadoError("encienda el laser y cierre el lazo antes de iniciar.")
        if self.bloqueado:
            raise ComandoRechazadoError("hay un umbral que bloquea los comandos.")
        programa = cmd["programa"]
        self._abortar = False
        self.ejecucion = {
            "registro": cmd["registro"],
            "programa": programa.get("nombre", programa.get("codigo")),
            "modo": cmd.get("modo", "REAL"),
            "pulso": 0,
            "pulsos": programa["pulsos"] or 0,
            "progreso": 0.0,
            "distancia_mm": 0.0,
        }
        self._pasar_a(GRABANDO)
        self._avisar(f"Grabando {self.ejecucion['programa']} ({cmd['registro']}).")
        self._tarea = asyncio.create_task(self._ejecutar(cmd))

    def _pedir_aborto(self, _cmd: dict) -> None:
        if self.estado != GRABANDO:
            raise ComandoRechazadoError("no hay un programa en ejecucion.")
        self._abortar = True
        self._avisar("Abortando el programa al terminar el pulso en curso...", "warn")

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

    async def _dormir(self, segundos_simulados: float) -> None:
        await asyncio.sleep(segundos_simulados / self.velocidad)

    async def _esperar_motor(self) -> None:
        while not self.hal.motor_en_destino:
            await self._dormir(0.01)

    async def _ejecutar(self, cmd: dict) -> None:
        """Ejecuta el loop de grabado: pulso, marca, desplazamiento.

        Una emergencia cancela esta tarea desde ``_parada``; en ese caso no se
        emite ``fin``: el registro queda interrumpido por el evento de emergencia.
        """
        prog = cmd["programa"]
        registro = cmd["registro"]
        real = cmd.get("modo", "REAL") == "REAL"
        total = prog["pulsos"] or MAXIMO_PULSOS_MANUAL
        t_pulso = prog["duracion_pulso_ms"] / 1000
        objetivo = prog["potencia_objetivo_mw"]
        self.hal.potencia_objetivo_mw = objetivo
        cada = max(1, total // 4)
        acumulado_um = 0.0
        eficiencias = []

        # Cada red arranca en el origen del eje, y no se marca nada hasta que el
        # desplazador llego: empezar antes correria todas las marcas.
        await self._dormir(self.hal.mover_a(0.0))
        await self._esperar_motor()
        self._checkpoint(registro, "TIEMPO")
        for n in range(total):
            if self._abortar:
                break
            # Pulso: en PRUEBA se recorre el programa con el laser inhibido.
            if real:
                self.hal.shutter_abierto = True
                # Publicar la apertura permite ver cada pulso, aun si dura menos
                # que el intervalo periodico de telemetria.
                self.hubo_cambio = True
            await self._dormir(t_pulso)
            if real and objetivo > 0:
                eficiencias.append(self.hal.potencia_optica_mw / objetivo)
            self.hal.shutter_abierto = False

            acumulado_um += distancia_de_marca(prog["periodo"], n)
            # Posicion absoluta desde el origen, no relativa a donde quedo el
            # motor: un error de posicionamiento no se arrastra a las marcas
            # siguientes.
            t_mov = self.hal.mover_a(acumulado_um / 1000)
            if real:
                self._emitir(
                    p.EV_MARCA,
                    {
                        "registro": registro,
                        "numero": n + 1,
                        "posicion_um": round(acumulado_um, 3),
                        "cant_pulsos": 1,
                        "ciclo_trabajo": round(100 * t_pulso / (t_pulso + t_mov), 1),
                        "tiempo_de_pulso": prog["duracion_pulso_ms"],
                    },
                )
            self.ejecucion.update(
                pulso=n + 1,
                progreso=round((n + 1) / total, 4) if prog["pulsos"] else 0.0,
                distancia_mm=round(acumulado_um / 1000, 3),
                periodo_s=round(t_pulso + t_mov, 3),
            )
            self.hubo_cambio = True
            if (n + 1) % cada == 0:
                self._checkpoint(registro, "MARCA")
            await self._dormir(t_mov)
            await self._esperar_motor()

        self.hal.potencia_objetivo_mw = 0.0
        marcas = self.ejecucion["pulso"]
        self.ejecucion = None
        self._pasar_a(LISTO)
        if self._abortar:
            self._emitir(
                p.EV_ABORTADO,
                {"registro": registro, "marcas": marcas, "motivo": "Abortado por el operador."},
            )
            self._avisar(f"Programa abortado despues de {marcas} marcas.", "warn")
            return

        datos_fin = {"registro": registro, "marcas": marcas, "espectro": None}
        if real and marcas:
            eficiencia = sum(eficiencias) / len(eficiencias) if eficiencias else 1.0
            lambdas, dbs = self.hal.espectro(prog["periodo"]["base_um"], marcas, eficiencia)
            datos_fin["espectro"] = {"longitudes_onda": lambdas, "transmitancias": dbs}
        self._emitir(p.EV_FIN, datos_fin)
        self._avisar(f"Programa terminado: {marcas} marcas. Espectro medido.")

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

    def telemetria(self, hz: float) -> dict:
        """Arma el mensaje de telemetria que se publica al front (ADR-0007).

        Args:
            hz: Frecuencia de publicacion, para que el front la muestre.

        Returns:
            El mensaje completo, serializable a JSON.
        """
        hal = self.hal
        en_curso = self.ejecucion
        frecuencia = 0.0
        if en_curso and en_curso.get("periodo_s"):
            # Pulsos por segundo: un ciclo es el pulso mas el desplazamiento.
            frecuencia = round(1 / en_curso["periodo_s"], 2)
        return {
            "ts": _ahora_iso(),
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
                "frecuencia_hz": frecuencia,
            },
            "ejecucion": dict(en_curso) if en_curso else None,
            "falla": hal.falla,
            "bloqueado": self.bloqueado,
            "mensaje": self.mensaje,
            "hz": hz,
        }
