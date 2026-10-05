"""HAL simulado: el hardware del laboratorio como un modelo fisico simple.

Cada magnitud tiende a su valor de equilibrio con una constante de tiempo
(modelo de primer orden): el agua no se enfria de golpe, la alta tension sube
en rampa, el motor se desplaza a velocidad finita. No pretende ser un modelo
termico exacto del tubo de CO2: pretende que la telemetria se comporte como la
de verdad, con inercia, ruido y dependencias entre variables, para que el resto
del sistema se pruebe contra algo creible.

Lo que se puede romper a proposito (``falla``):

* ``sobretemperatura``: el chiller sigue bombeando pero no enfria. Con el laser
  encendido, el agua sube y cruza el umbral en unos segundos.
* ``caudal``: la bomba pierde caudal. Sin caudal, el tubo se dana.

El interrogador optico genera el espectro de la red a partir de la fisica de
una LPG: la resonancia cae en ``lambda = delta_n * periodo`` y su profundidad
crece con la cantidad de marcas (acoplamiento ``cos^2(kappa * N)``).
"""

from __future__ import annotations

import math
import random

from controller import protocolo as p

# ── Constantes del modelo ────────────────────────────────────────────────────

AMBIENTE_C = 23.0
SETPOINT_CHILLER_C = 18.0
CAUDAL_NOMINAL = 4.2  # L/min
TENSION_NOMINAL_KV = 18.0
CORRIENTE_NOMINAL_MA = 12.0
VELOCIDAD_MOTOR_MM_S = 2.0
TIEMPO_SHUTTER_S = 0.04
DURACION_ALINEACION_S = 3.0

# Diferencia de indice efectivo nucleo-revestimiento de los dos modos de
# revestimiento que acopla la red. Con un periodo de 550 um dan resonancias en
# ~1606 y ~1199 nm, del orden de las del CSV real del laboratorio (L05LPG01).
DELTA_N_PRINCIPAL = 2.92e-3
DELTA_N_SECUNDARIO = 2.18e-3
KAPPA_PRINCIPAL = 0.06  # rad por marca
KAPPA_SECUNDARIO = 0.035

# Ventana del OSA, como la del AQ6370B del laboratorio.
VENTANA_OSA_NM = (1170, 1670)
PASO_OSA_NM = 2


def _relajar(actual: float, objetivo: float, dt: float, tau: float) -> float:
    """Acerca una magnitud a su objetivo con constante de tiempo ``tau``."""
    if tau <= 0:
        return objetivo
    return actual + (objetivo - actual) * (1 - math.exp(-dt / tau))


class HalSimulado:
    """Estado fisico del arreglo de grabado, avanzado en el tiempo."""

    def __init__(self, semilla: int | None = None) -> None:
        """Arranca el arreglo apagado, a temperatura ambiente.

        Args:
            semilla: Semilla del ruido. Fijarla hace la simulacion reproducible,
                que es lo que necesitan las pruebas.
        """
        self._azar = random.Random(semilla)
        # Actuadores: lo que el controlador comanda.
        self.refrigeracion = False
        self.alta_tension = False
        self.laser = False
        self.shutter_armado = False
        self.shutter_abierto = False
        self.potencia_objetivo_mw = 0.0
        self.falla = p.FALLA_NINGUNA
        # Magnitudes fisicas: lo que los sensores miden.
        self.temp_agua = AMBIENTE_C
        self.caudal = 0.0
        self.tension_kv = 0.0
        self.corriente_ma = 0.0
        self.potencia_optica_mw = 0.0
        self.temp_laser = AMBIENTE_C
        self.posicion_mm = 0.0
        self._posicion_objetivo_mm = 0.0
        self._alineacion = 0.0  # 0 = desalineada, 1 = alineada
        self._alineando = False

    # ── Actuadores ──────────────────────────────────────────────────────────

    def alinear(self) -> None:
        """Arranca la alineacion de la fibra con el sensor de sombra."""
        self._alineando = True

    def desalinear(self) -> None:
        """Libera la fibra: el sensor de sombra deja de verla alineada."""
        self._alineando = False
        self._alineacion = 0.0

    def mover_a(self, posicion_mm: float) -> float:
        """Ordena al motor ir a una posicion.

        Args:
            posicion_mm: Posicion destino del desplazador lineal.

        Returns:
            Cuanto tarda el movimiento, en segundos de tiempo simulado.
        """
        self._posicion_objetivo_mm = posicion_mm
        return abs(posicion_mm - self.posicion_mm) / VELOCIDAD_MOTOR_MM_S

    # ── Sensores ────────────────────────────────────────────────────────────

    @property
    def potencia_laser_w(self) -> float:
        """Potencia de salida del tubo, proporcional a la corriente de descarga."""
        return 0.8 * self.corriente_ma

    @property
    def fibra_alineada(self) -> bool:
        """El sensor de sombra ve la fibra en el camino del haz."""
        return self._alineacion >= 1.0

    @property
    def alineando(self) -> bool:
        """La alineacion esta en curso."""
        return self._alineando and not self.fibra_alineada

    @property
    def sensor_sombra_mw(self) -> float:
        """Potencia residual que llega al fotodetector de sombra."""
        return 2.4 * (1 - self._alineacion) + 0.12 * self._alineacion

    @property
    def motor_en_destino(self) -> bool:
        """El desplazador llego a la posicion ordenada."""
        return abs(self.posicion_mm - self._posicion_objetivo_mm) < 1e-6

    def _ruido(self, escala: float) -> float:
        return self._azar.gauss(0, escala)

    def lecturas(self) -> dict[str, float]:
        """Lee las siete variables analogicas monitoreadas (ADR-0007), con ruido.

        Returns:
            ``{variable: valor}`` con las claves de ``VariableMonitoreada``.
        """
        encendido = self.refrigeracion
        return {
            "TEMPERATURA_AGUA": round(self.temp_agua + self._ruido(0.05), 2),
            "CAUDAL_REFRIGERANTE": round(max(0.0, self.caudal + self._ruido(0.04 * encendido)), 2),
            "POTENCIA_LASER": round(
                max(0.0, self.potencia_laser_w + self._ruido(0.05 * self.laser)), 2
            ),
            "TENSION_AT": round(
                max(0.0, self.tension_kv + self._ruido(0.03 * self.alta_tension)), 2
            ),
            "CORRIENTE_AT": round(max(0.0, self.corriente_ma + self._ruido(0.05 * self.laser)), 2),
            "POSICION_MOTOR": round(self.posicion_mm, 3),
            "TEMPERATURA_AMBIENTE": round(AMBIENTE_C + self._ruido(0.05), 2),
        }

    def senales(self) -> dict[str, bool]:
        """Lee las tres senales digitales de interlock."""
        return {
            "FIBRA_ALINEADA": self.fibra_alineada,
            "SHUTTER_ABIERTO": self.shutter_abierto,
            "SHUTTER_CERRADO": not self.shutter_abierto,
        }

    # ── Dinamica ────────────────────────────────────────────────────────────

    def avanzar(self, dt: float) -> None:
        """Avanza la fisica ``dt`` segundos de tiempo simulado.

        Args:
            dt: Paso de tiempo simulado, en segundos.
        """
        enfria = self.refrigeracion and self.falla != p.FALLA_SOBRETEMPERATURA
        if enfria:
            objetivo_agua, tau_agua = SETPOINT_CHILLER_C + 0.25 * self.potencia_laser_w, 25.0
        elif self.laser:
            # Sin enfriamiento efectivo, la descarga calienta el agua del circuito.
            objetivo_agua, tau_agua = AMBIENTE_C + 1.5 * self.potencia_laser_w, 12.0
        else:
            objetivo_agua, tau_agua = AMBIENTE_C, 60.0
        self.temp_agua = _relajar(self.temp_agua, objetivo_agua, dt, tau_agua)

        if not self.refrigeracion:
            objetivo_caudal = 0.0
        elif self.falla == p.FALLA_CAUDAL:
            objetivo_caudal = 0.8
        else:
            objetivo_caudal = CAUDAL_NOMINAL
        self.caudal = _relajar(self.caudal, objetivo_caudal, dt, 1.5)

        objetivo_kv = (TENSION_NOMINAL_KV - 0.8 * self.laser) if self.alta_tension else 0.0
        self.tension_kv = _relajar(self.tension_kv, objetivo_kv, dt, 0.6)

        hay_descarga = self.laser and self.alta_tension and self.tension_kv > 15.0
        self.corriente_ma = _relajar(
            self.corriente_ma, CORRIENTE_NOMINAL_MA if hay_descarga else 0.0, dt, 0.3
        )

        # Lazo de potencia cerrado: con el shutter abierto la potencia sobre la
        # fibra converge al objetivo del programa (RN003: solo con laser encendido).
        en_fibra = self.potencia_objetivo_mw if (self.shutter_abierto and hay_descarga) else 0.0
        self.potencia_optica_mw = _relajar(self.potencia_optica_mw, en_fibra, dt, 0.15)

        self.temp_laser = _relajar(
            self.temp_laser, self.temp_agua + (14.0 if hay_descarga else 1.0), dt, 8.0
        )

        if self._alineando and self._alineacion < 1.0:
            self._alineacion = min(1.0, self._alineacion + dt / DURACION_ALINEACION_S)

        paso = VELOCIDAD_MOTOR_MM_S * dt
        delta = self._posicion_objetivo_mm - self.posicion_mm
        self.posicion_mm = (
            self._posicion_objetivo_mm
            if abs(delta) <= paso
            else self.posicion_mm + math.copysign(paso, delta)
        )

    # ── Interrogador optico ─────────────────────────────────────────────────

    def espectro(
        self, periodo_um: float, marcas: int, eficiencia: float = 1.0
    ) -> tuple[list[float], list[float]]:
        """Mide el espectro de transmision de la red recien grabada.

        Args:
            periodo_um: Periodo de la red (el desplazamiento entre marcas).
            marcas: Cantidad de marcas grabadas.
            eficiencia: Fraccion de la potencia objetivo que efectivamente llego
                a la fibra. Una red grabada con menos potencia acopla menos.

        Returns:
            Los ejes ``(longitudes de onda nm, transmitancia dB)``.
        """
        resonancias = []
        for delta_n, kappa in (
            (DELTA_N_PRINCIPAL, KAPPA_PRINCIPAL),
            (DELTA_N_SECUNDARIO, KAPPA_SECUNDARIO),
        ):
            centro = delta_n * periodo_um * 1000  # um -> nm
            acople = math.cos(kappa * eficiencia * marcas) ** 2
            profundidad = -10 * math.log10(max(acople, 1e-3))
            # Mas marcas, red mas larga, resonancia mas angosta.
            ancho = max(5.0, 160.0 / max(marcas, 1))
            resonancias.append((centro, profundidad, ancho))

        lambdas, dbs = [], []
        for nm in range(VENTANA_OSA_NM[0], VENTANA_OSA_NM[1] + 1, PASO_OSA_NM):
            valor = -2.0 + 0.15 * math.sin(nm / 9.0) + self._ruido(0.03)
            for centro, profundidad, ancho in resonancias:
                valor -= profundidad / (1 + ((nm - centro) / (ancho / 2)) ** 2)
            lambdas.append(float(nm))
            dbs.append(round(valor, 3))
        return lambdas, dbs
