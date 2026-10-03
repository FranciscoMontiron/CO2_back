"""Pruebas de la trazabilidad de la fabricacion (RF006, RF007)."""

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from comun.enums import (
    EstadoEjecucion,
    EstadoSistema,
    ModoEjecucion,
    TipoCheckpoint,
    VariableMonitoreada,
)
from trazabilidad.models import Checkpoint, RegistroDeFabricacion


@pytest.mark.django_db
def test_una_corrida_en_curso_no_tiene_duracion(registro):
    """`None` y no cero: todavia no termino, no duro cero."""
    assert registro.fin is None
    assert registro.duracion is None


@pytest.mark.django_db
def test_la_duracion_se_deriva_de_inicio_y_fin(registro):
    """Es un atributo derivado del diagrama: no se persiste."""
    registro.inicio = datetime.datetime(2026, 10, 2, 10, 0, tzinfo=datetime.UTC)
    registro.fin = datetime.datetime(2026, 10, 2, 10, 25, tzinfo=datetime.UTC)
    registro.save()

    assert registro.duracion == 1500


@pytest.mark.django_db
def test_finalizar_cierra_la_corrida(registro):
    """El camino feliz deja estado COMPLETADO y la hora de cierre."""
    registro.finalizar()
    registro.refresh_from_db()

    assert registro.estado == EstadoEjecucion.COMPLETADO
    assert registro.fin is not None


@pytest.mark.django_db
def test_interrumpir_deja_asentado_el_motivo(registro):
    """Una corrida cortada sin motivo registrado no es trazable."""
    registro.interrumpir("Temperatura del agua fuera de rango")
    registro.refresh_from_db()

    assert registro.estado == EstadoEjecucion.INTERRUMPIDO
    assert registro.fin is not None
    assert "Temperatura del agua" in registro.observaciones


@pytest.mark.django_db
def test_en_modo_prueba_no_puede_haber_red(programa, red, configuracion):
    """Restriccion `{modo = REAL => 1 Red}`: en PRUEBA se recorre sin grabar."""
    registro = RegistroDeFabricacion(
        codigo="ENS-0002",
        programa=programa,
        red=red,
        configuracion=configuracion,
        modo=ModoEjecucion.PRUEBA,
    )

    with pytest.raises(ValidationError) as exc:
        registro.clean()
    assert "PRUEBA" in str(exc.value)


@pytest.mark.django_db
def test_una_corrida_de_prueba_sin_red_es_valida(programa, configuracion):
    """El otro lado de la restriccion: PRUEBA sin red esta bien."""
    registro = RegistroDeFabricacion(
        codigo="ENS-0003",
        programa=programa,
        configuracion=configuracion,
        modo=ModoEjecucion.PRUEBA,
    )
    registro.clean()  # no levanta


@pytest.mark.django_db
def test_el_checkpoint_guarda_las_lecturas_del_instante(registro, programa):
    """Es lo que reemplaza a la telemetria persistida (ADR-0007).

    Responde "a que temperatura y potencia se grabo esta red" con un punado de
    filas por ensayo, en vez de 86.400 por dia.
    """
    paso = programa.pasos.first()
    cp = registro.registrar_checkpoint(
        tipo=TipoCheckpoint.MARCA,
        estado_sistema=EstadoSistema.GRABANDO,
        paso=paso,
        valores={
            VariableMonitoreada.TEMPERATURA_AGUA: 21.4,
            VariableMonitoreada.POTENCIA_LASER: 12.3,
        },
    )

    assert cp.numero == 1
    assert cp.paso == paso
    assert cp.estado_sistema == EstadoSistema.GRABANDO
    assert cp.valores[VariableMonitoreada.TEMPERATURA_AGUA] == 21.4


@pytest.mark.django_db
def test_los_checkpoints_se_numeran_correlativos(registro):
    """El numero ordena la secuencia del ensayo."""
    for _ in range(3):
        registro.registrar_checkpoint(
            tipo=TipoCheckpoint.TIEMPO, estado_sistema=EstadoSistema.GRABANDO
        )

    assert [c.numero for c in registro.checkpoints.all()] == [1, 2, 3]


@pytest.mark.django_db
def test_no_se_repite_el_numero_de_checkpoint(registro):
    """Dos checkpoints con el mismo numero romperian el orden del ensayo."""
    registro.registrar_checkpoint(tipo=TipoCheckpoint.TIEMPO, estado_sistema=EstadoSistema.REPOSO)
    with pytest.raises(IntegrityError):
        Checkpoint.objects.create(
            registro=registro,
            numero=1,
            tipo=TipoCheckpoint.TIEMPO,
            estado_sistema=EstadoSistema.REPOSO,
        )


@pytest.mark.django_db
def test_el_estado_del_sistema_no_mezcla_condiciones_de_armado():
    """Verifica el resultado de haber partido `EstadoSistema` en dos enums.

    Antes convivian REPOSO y FIBRA_ALINEADA en el mismo enum, lo que hacia
    imposible representar que el sistema esta GRABANDO *y* con la fibra
    alineada, que es lo que pasa fisicamente.
    """
    valores = set(EstadoSistema.values)

    assert valores == {"REPOSO", "PREPARANDO", "LISTO", "GRABANDO", "EMERGENCIA"}
    assert "FIBRA_ALINEADA" not in valores
    assert "LASER_ACTIVO" not in valores
