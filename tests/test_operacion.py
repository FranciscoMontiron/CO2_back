"""Pruebas de umbrales, alertas, configuracion y emergencias."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from comun.enums import OrigenEmergencia, Severidad, VariableMonitoreada
from operacion.models import Configuracion, EventoEmergencia, Umbral


@pytest.mark.django_db
def test_el_umbral_acepta_lo_que_esta_dentro_del_rango(configuracion):
    """Camino normal: la lectura esta en rango y no pasa nada."""
    umbral = configuracion.umbrales.get()
    assert umbral.evaluar(21.0) is True
    assert umbral.evaluar(15.0) is True  # los extremos son admisibles
    assert umbral.evaluar(28.0) is True


@pytest.mark.django_db
def test_el_umbral_rechaza_lo_que_se_va_de_rango(configuracion):
    """Fuera de rango por cualquiera de los dos lados."""
    umbral = configuracion.umbrales.get()
    assert umbral.evaluar(14.9) is False
    assert umbral.evaluar(28.1) is False


@pytest.mark.django_db
def test_un_umbral_desactivado_no_dispara(configuracion):
    """Desactivarlo es la forma de silenciar un sensor en mantenimiento."""
    umbral = configuracion.umbrales.get()
    umbral.activo = False
    umbral.save()

    assert umbral.evaluar(1000.0) is True


@pytest.mark.django_db
def test_disparar_genera_una_alerta_con_contexto_util(configuracion):
    """La alerta tiene que decir que paso y que hacer, no solo que fallo."""
    umbral = configuracion.umbrales.get()
    alerta = umbral.disparar(31.5)

    assert alerta.valor_medido == 31.5
    assert alerta.severidad == Severidad.CRITICA
    assert "por encima del maximo" in alerta.descripcion
    assert "chiller" in alerta.sugerencia
    assert alerta.reconocida is False


@pytest.mark.django_db
def test_la_alerta_distingue_por_que_extremo_se_fue(configuracion):
    """Saber si falto o sobro cambia que revisa el operador."""
    umbral = configuracion.umbrales.get()
    assert "por debajo del minimo" in umbral.disparar(10.0).descripcion


@pytest.mark.django_db
def test_reconocer_una_alerta_deja_quien_y_cuando(configuracion, usuario):
    """Sin quien la vio, la alerta no sirve como evidencia."""
    alerta = configuracion.umbrales.get().disparar(31.5)
    alerta.reconocer(usuario)
    alerta.refresh_from_db()

    assert alerta.reconocida is True
    assert alerta.reconocida_por == usuario
    assert alerta.reconocida_en is not None


@pytest.mark.django_db
def test_una_alerta_no_se_reconoce_dos_veces(configuracion, usuario):
    """El segundo reconocimiento pisaria quien la vio primero."""
    alerta = configuracion.umbrales.get().disparar(31.5)
    alerta.reconocer(usuario)

    with pytest.raises(ValidationError):
        alerta.reconocer(usuario)


@pytest.mark.django_db
def test_el_rango_invertido_se_rechaza_en_la_base(configuracion):
    """Un minimo mayor que el maximo nunca se cumple: el umbral quedaria mudo."""
    with pytest.raises(IntegrityError):
        Umbral.objects.create(
            configuracion=configuracion,
            variable=VariableMonitoreada.POTENCIA_LASER,
            valor_min=50.0,
            valor_max=10.0,
        )


@pytest.mark.django_db
def test_no_se_repite_la_variable_dentro_de_una_configuracion(configuracion):
    """Dos umbrales de la misma variable dejarian ambiguo cual aplica."""
    with pytest.raises(IntegrityError):
        Umbral.objects.create(
            configuracion=configuracion,
            variable=VariableMonitoreada.TEMPERATURA_AGUA,
            valor_min=0.0,
            valor_max=100.0,
        )


@pytest.mark.django_db
def test_activar_una_configuracion_desactiva_la_anterior(configuracion, usuario):
    """Dos vigentes significaria que el controlador no sabe cual cargar."""
    configuracion.activar()
    otra = Configuracion.objects.create(nombre="Operacion conservadora", definida_por=usuario)
    otra.activar()

    configuracion.refresh_from_db()
    assert configuracion.activa is False
    assert otra.activa is True
    assert Configuracion.objects.filter(activa=True).count() == 1


@pytest.mark.django_db
def test_la_base_impide_dos_configuraciones_vigentes(configuracion, usuario):
    """La garantia no puede depender de que todos usen `activar()`."""
    configuracion.activar()
    with pytest.raises(IntegrityError):
        Configuracion.objects.create(nombre="Otra", definida_por=usuario, activa=True)


@pytest.mark.django_db
def test_el_evento_de_emergencia_evidencia_rnf001():
    """`tiempoRespuestaMs` es el dato con el que se audita RNF001 (<=500 ms)."""
    dentro = EventoEmergencia.objects.create(
        origen=OrigenEmergencia.UMBRAL, tiempo_respuesta_ms=140
    )
    fuera = EventoEmergencia.objects.create(origen=OrigenEmergencia.UMBRAL, tiempo_respuesta_ms=820)

    assert dentro.cumple_rnf001 is True
    assert fuera.cumple_rnf001 is False


@pytest.mark.django_db
def test_el_rearme_es_explicito_y_queda_asentado(usuario):
    """Que el sistema se rearme solo despues de una emergencia es justo lo que no debe pasar."""
    evento = EventoEmergencia.objects.create(
        origen=OrigenEmergencia.BOTON_FISICO, tiempo_respuesta_ms=95
    )
    assert evento.rearmado_en is None

    evento.rearmar(usuario)
    evento.refresh_from_db()

    assert evento.rearmado_en is not None
    assert evento.rearmado_por == usuario


@pytest.mark.django_db
def test_no_se_rearma_dos_veces(usuario):
    """El segundo rearme borraria quien lo hizo la primera vez."""
    evento = EventoEmergencia.objects.create(
        origen=OrigenEmergencia.OPERADOR, tiempo_respuesta_ms=200
    )
    evento.rearmar(usuario)

    with pytest.raises(ValidationError):
        evento.rearmar(usuario)


def test_hay_sugerencia_para_toda_variable_analogica():
    """Una alerta sin sugerencia deja al operador adivinando que revisar.

    Si manana se agrega una variable al enum y se olvida la sugerencia, esto lo
    caza antes de que aparezca una alerta muda en produccion.
    """
    from operacion.models import _SUGERENCIAS

    faltantes = [v for v in VariableMonitoreada if not _SUGERENCIAS.get(v)]
    assert not faltantes, f"Variables sin sugerencia: {faltantes}"
