"""Contrato de ``operar``: mismo comportamiento en HalSimulado y en HalGpio."""

import asyncio
import contextlib

import pytest

from controller.hal_base import OperacionRechazada
from controller.programa import INVENTARIO_SIMULADO, validar_programa
from controller.rpi import HalGpio
from controller.simulador import HalSimulado
from tests.test_hardware import sistema


@contextlib.asynccontextmanager
async def hal_de(modo, tmp_path):
    """Entrega un HAL listo para operar; el simulado avanza su física en segundo plano."""
    if modo == "simulado":
        hal = HalSimulado(semilla=1)
        hal.velocidad = 50.0

        async def reloj():
            while True:
                hal.avanzar(0.1)
                await asyncio.sleep(0.002)

        tarea = asyncio.create_task(reloj())
        try:
            yield hal
        finally:
            tarea.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarea
    else:
        async with sistema(tmp_path, INVENTARIO_SIMULADO["componentes"]) as (gestor, _, _):
            yield HalGpio(gestor)


@pytest.fixture(params=["simulado", "gpio"])
def modo(request):
    return request.param


def accion(componente, comando, **argumentos):
    return {
        "tipo": "ACCION",
        "componente": componente,
        "comando": comando,
        "argumentos": argumentos,
    }


@pytest.mark.asyncio
async def test_operar_shutter_pulso_y_motor_en_los_dos_hal(modo, tmp_path):
    async with hal_de(modo, tmp_path) as hal:
        await hal.operar("laser", "encender", {})
        await hal.operar("shutter", "armar", {})
        salida = await hal.operar("shutter", "pulso", {"duracion_ms": 100})
        assert salida["duracion_real_ms"] == pytest.approx(100, abs=40)
        assert salida["cancelado"] is False

        await hal.operar("motor_x", "referenciar", {"posicion": 0})
        await hal.operar("motor_x", "mover", {"delta": 1, "velocidad": 5})
        await hal.operar("motor_x", "mover_a", {"posicion": 0.5, "velocidad": 5})
        await hal.operar("motor_rotacion", "referenciar", {"posicion": 0})
        await hal.operar("motor_rotacion", "mover", {"delta": 2, "velocidad": 10})
        await hal.operar("laser", "apagar", {})
        await hal.operar("shutter", "desarmar", {})


@pytest.mark.asyncio
async def test_simulado_refleja_la_operacion_en_sus_atributos(tmp_path):
    async with hal_de("simulado", tmp_path) as hal:
        await hal.operar("laser", "encender", {})
        assert hal.laser is True
        await hal.operar("shutter", "armar", {})
        assert hal.shutter_armado is True
        await hal.operar("shutter", "abrir", {})
        assert hal.shutter_abierto is True
        await hal.operar("shutter", "cerrar", {})
        assert hal.shutter_abierto is False
        await hal.operar("motor_x", "referenciar", {"posicion": 0})
        await hal.operar("motor_x", "mover_a", {"posicion": 3, "velocidad": 5})
        assert hal.posicion_mm == pytest.approx(3)
        await hal.operar("motor_rotacion", "referenciar", {"posicion": 0})
        await hal.operar("motor_rotacion", "mover", {"delta": 90, "velocidad": 10})
        assert hal.angulo_rotacion == pytest.approx(90)


@pytest.mark.asyncio
async def test_operar_rechaza_abrir_sin_armar_en_los_dos_hal(modo, tmp_path):
    async with hal_de(modo, tmp_path) as hal:
        with pytest.raises(OperacionRechazada):
            await hal.operar("shutter", "abrir", {})
        with pytest.raises(OperacionRechazada):
            await hal.operar("shutter", "pulso", {"duracion_ms": 50})
        with pytest.raises(OperacionRechazada):  # sin referenciar
            await hal.operar("motor_x", "mover", {"delta": 1, "velocidad": 1})
        await hal.operar("motor_x", "referenciar", {"posicion": 0})
        with pytest.raises(OperacionRechazada):  # fuera de límites
            await hal.operar("motor_x", "mover_a", {"posicion": 9999, "velocidad": 1})
        with pytest.raises(OperacionRechazada):  # componente inexistente
            await hal.operar("fantasma", "encender", {})


@pytest.mark.asyncio
async def test_inventario_valida_el_programa_de_ejemplo_en_los_dos_hal(modo, tmp_path):
    ejemplo = [
        accion("shutter", "armar"),
        {
            "tipo": "BUCLE",
            "repeticiones": 200,
            "pasos": [
                {
                    "tipo": "PARALELO",
                    "pasos": [
                        accion("shutter", "pulso", duracion_ms=400),
                        accion("motor_x", "mover", delta=0.55, velocidad=0.1),
                        accion("motor_rotacion", "mover", delta=3.6, velocidad=0.9),
                    ],
                }
            ],
        },
    ]
    async with hal_de(modo, tmp_path) as hal:
        assert validar_programa(ejemplo, hal.inventario()) == ejemplo


@pytest.mark.asyncio
async def test_simulado_respeta_la_velocidad_pedida_y_pierde_referencia_al_fallar(tmp_path):
    async with hal_de("simulado", tmp_path) as hal:
        await hal.operar("motor_x", "referenciar", {"posicion": 0})
        tarea = asyncio.create_task(hal.operar("motor_x", "mover", {"delta": 4, "velocidad": 0.5}))
        await asyncio.sleep(0.01)
        assert hal._velocidad_motor_mm_s == 0.5
        await tarea
        assert hal._velocidad_motor_mm_s == 2.0  # vuelve al valor heredado
        tarea = asyncio.create_task(hal.operar("motor_x", "mover", {"delta": 4, "velocidad": 0.1}))
        await asyncio.sleep(0.01)
        tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea
        with pytest.raises(OperacionRechazada):
            await hal.operar("motor_x", "mover", {"delta": 1, "velocidad": 1})
