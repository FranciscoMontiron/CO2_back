"""Paso 2 del procedimiento estandar: la refrigeracion pasa a ser un interruptor.

Corrige las bases donde PROC-001 ya existe (la semilla no pisa un
procedimiento cargado), solo si el paso sigue con el boton original, y sube la
version para que el cambio quede asentado.
"""

from django.db import migrations

CODIGO = "PROC-001"
ENCENDER = {"accion": "encender_refrigeracion", "etiqueta": "Encender refrigeración"}
APAGAR = {"accion": "apagar_refrigeracion", "etiqueta": "Apagar refrigeración"}
INTERRUPTOR = {
    "tipo": "interruptor",
    "estado": {"fuente": "actuadores.refrigeracion", "op": "es_verdadero"},
    "encender": ENCENDER,
    "apagar": APAGAR,
}


def _cambiar(apps, antes: list, despues: list) -> None:
    Procedimiento = apps.get_model("procedimientos", "ProcedimientoGuiado")
    Paso = apps.get_model("procedimientos", "PasoGuiado")
    proc = Procedimiento.objects.filter(codigo=CODIGO).first()
    paso = Paso.objects.filter(procedimiento=proc, orden=2).first() if proc else None
    if paso is None or paso.acciones != antes:
        return
    paso.acciones = despues
    paso.save(update_fields=["acciones"])
    proc.version += 1
    proc.save(update_fields=["version"])


def a_interruptor(apps, schema_editor):
    """Reemplaza el boton de encender por el interruptor."""
    _cambiar(apps, [ENCENDER], [INTERRUPTOR])


def a_boton(apps, schema_editor):
    """Vuelve al boton de encender solo."""
    _cambiar(apps, [INTERRUPTOR], [ENCENDER])


class Migration(migrations.Migration):
    """Ver docstring del modulo."""

    dependencies = [("procedimientos", "0003_texto_ayuda_condicion_avance")]

    operations = [migrations.RunPython(a_interruptor, a_boton)]
