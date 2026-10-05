"""Recorre el circuito completo del laboratorio simulado, por la API.

Hace lo mismo que un operador en «Nuevo ensayo», sin navegador: arma el equipo
paso a paso, ejecuta un programa en tiempo real, muestra la telemetria mientras
graba y termina con el ensayo guardado y su red caracterizada. Con ``--falla``
ademas provoca una falla durante un segundo grabado, para ver la parada de
emergencia automatica.

Requiere el stack levantado y los datos de ejemplo cargados:

    python scripts/simular_ensayo.py
    python scripts/simular_ensayo.py --programa PRG-003 --falla sobretemperatura

Solo usa la biblioteca estandar. Verifica TLS contra el certificado de
desarrollo (docker/nginx/certs/co2.crt) en vez de desactivarlo.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CERTIFICADO = RAIZ / "docker" / "nginx" / "certs" / "co2.crt"


class Cliente:
    """Cliente minimo de la API con sesion JWT."""

    def __init__(self, base: str) -> None:
        """Prepara el contexto TLS.

        Args:
            base: URL base de la API, por ejemplo ``https://localhost:8443/api``.
        """
        self.base = base
        self.token: str | None = None
        self.tls = ssl.create_default_context(cafile=str(CERTIFICADO))

    def llamar(
        self, metodo: str, ruta: str, datos: dict | None = None
    ) -> tuple[int, dict | list | None]:
        """Hace una peticion y devuelve ``(codigo, cuerpo)``."""
        peticion = urllib.request.Request(self.base + ruta, method=metodo)
        peticion.add_header("Content-Type", "application/json")
        if self.token:
            peticion.add_header("Authorization", f"Bearer {self.token}")
        cuerpo = json.dumps(datos).encode() if datos is not None else None
        try:
            with urllib.request.urlopen(peticion, cuerpo, context=self.tls, timeout=10) as r:
                texto = r.read()
                return r.status, (json.loads(texto) if texto else None)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def estado(self) -> dict:
        """Ultima telemetria del controlador."""
        codigo, cuerpo = self.llamar("GET", "/control/estado/")
        if codigo != 200:
            sys.exit(f"El controlador no responde ({codigo}). Esta levantado el stack?")
        return cuerpo

    def comando(self, accion: str, **extra) -> None:
        """Manda un comando y aborta si la API lo rechaza."""
        codigo, cuerpo = self.llamar("POST", "/control/comandos/", {"accion": accion, **extra})
        if codigo != 202:
            sys.exit(f"Comando {accion} rechazado ({codigo}): {cuerpo}")


def esperar(cliente: Cliente, condicion, descripcion: str, tope: float = 40) -> dict:
    """Espera a que la telemetria cumpla una condicion."""
    inicio = time.time()
    while time.time() - inicio < tope:
        estado = cliente.estado()
        if condicion(estado):
            print(f"   ✓ {descripcion:<28} {time.time() - inicio:4.1f} s")
            return estado
        time.sleep(0.4)
    estado = cliente.estado()
    sys.exit(f"Tiempo agotado esperando: {descripcion}. Mensaje: {estado.get('mensaje')}")


def armar(cliente: Cliente) -> None:
    """Secuencia de armado: refrigeracion, alta tension, fibra, shutter y laser."""
    print("\n1. Armado del equipo")
    cliente.comando("encender_refrigeracion")
    esperar(cliente, lambda e: e["condiciones"]["REFRIGERACION_OK"], "refrigeracion estable")
    cliente.comando("habilitar_at")
    esperar(cliente, lambda e: e["condiciones"]["AT_ENCENDIDA"], "alta tension en rampa")
    cliente.comando("alinear_fibra")
    esperar(cliente, lambda e: e["condiciones"]["FIBRA_ALINEADA"], "fibra alineada")
    cliente.comando("armar_shutter")
    esperar(cliente, lambda e: e["estado"] == "LISTO", "sistema LISTO")
    cliente.comando("encender_laser")
    esperar(cliente, lambda e: e["actuadores"]["laser"], "laser y lazo cerrado")


def grabar(cliente: Cliente, programa: str) -> str:
    """Ejecuta un programa y muestra la telemetria mientras graba."""
    codigo, r = cliente.llamar("POST", "/control/iniciar/", {"programa": programa})
    if codigo != 201:
        sys.exit(f"No se pudo iniciar {programa} ({codigo}): {r}")
    print(f"\n2. Grabando {programa} -> ensayo {r['registro']}, red {r['red']}")
    inicio, ultimo = time.time(), -1
    while True:
        e = cliente.estado()
        ej = e["ejecucion"]
        if ej and ej["pulso"] != ultimo:
            ultimo = ej["pulso"]
            v = e["variables"]
            print(
                f"   pulso {ej['pulso']:>3}/{ej['pulsos']:<3} {ej['distancia_mm']:6.2f} mm"
                f"   agua {v['TEMPERATURA_AGUA']:5.1f} °C   laser {v['POTENCIA_LASER']:4.1f} W"
                f"   fibra {e['extra']['potencia_optica_mw']:5.2f} mW"
            )
        if e["estado"] != "GRABANDO" and time.time() - inicio > 1.5:
            print(f"   -> {e['estado']}: {e['mensaje']['texto']}")
            return r["registro"]
        time.sleep(0.3)


def mostrar_ensayo(cliente: Cliente, registro: str) -> None:
    """Muestra como quedo guardado el ensayo."""
    time.sleep(2.5)  # el servicio de eventos persiste el cierre
    _, d = cliente.llamar("GET", f"/ensayos/{registro}/")
    red = d["red"] or {}
    print(f"\n3. Ensayo {d['codigo']}: {d['estado']}")
    print(f"   red {red.get('codigo')} -> {red.get('estado')}")
    print(f"   {red.get('cantidad_marcas')} marcas, {red.get('longitud_mm')} mm")
    for r in d["resonancias"]:
        tipo = "principal" if r["principal"] else "secundaria"
        print(f"   resonancia {tipo}: {r['longitud_onda_nm']} nm, {r['transmitancia_db']} dB")
    if d["observaciones"]:
        print(f"   observaciones: {d['observaciones']}")


def main() -> None:
    """Punto de entrada."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="https://localhost:8443/api")
    parser.add_argument("--usuario", default="operador")
    parser.add_argument("--programa", default="PRG-001")
    parser.add_argument(
        "--falla",
        choices=["sobretemperatura", "caudal"],
        help="provoca una falla en un segundo grabado",
    )
    args = parser.parse_args()

    cliente = Cliente(args.base)
    codigo, r = cliente.llamar(
        "POST", "/auth/token/", {"username": args.usuario, "password": args.usuario}
    )
    if codigo != 200:
        sys.exit(
            f"No se pudo iniciar sesion como {args.usuario} ({codigo}). "
            "Faltan los datos de ejemplo?"
        )
    cliente.token = r["access"]
    print(f"Sesion iniciada como {args.usuario}. Estado del equipo: {cliente.estado()['estado']}")

    if cliente.estado()["estado"] == "EMERGENCIA":
        cliente.comando("rearmar")
        time.sleep(1)
    cliente.comando("inyectar_falla", falla="ninguna")

    armar(cliente)
    mostrar_ensayo(cliente, grabar(cliente, args.programa))

    if args.falla:
        print(f"\n4. Falla simulada: {args.falla}")
        cliente.comando("inyectar_falla", falla=args.falla)
        mostrar_ensayo(cliente, grabar(cliente, args.programa))
        _, emergencias = cliente.llamar("GET", "/emergencias/")
        e = emergencias[0]
        print(
            f"   parada: {e['origen_display']}, {e['tiempo_respuesta_ms']} ms "
            f"(cumple RNF001: {e['cumple_rnf001']})"
        )
        cliente.comando("inyectar_falla", falla="ninguna")
        cliente.comando("rearmar")
        time.sleep(1)

    print("\n5. Apagado en orden inverso")
    cliente.comando("apagar")
    esperar(cliente, lambda e: e["estado"] == "REPOSO", "equipo en reposo")


if __name__ == "__main__":
    main()
