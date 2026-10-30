"""Paso 1 del procedimiento estandar: interrogador y comunicacion por sensor.

La semilla los cargaba como validaciones manuales; en la app anterior se
tildaban solas. Esta migracion corrige las bases donde PROC-001 ya existe (la
semilla no pisa un procedimiento cargado) y sube su version para que el cambio
quede asentado.
"""

from django.db import migrations

CODIGO = "PROC-001"
TEXTOS = ["Interrogador óptico conectado", "Comunicación establecida"]
CONECTADO = {"fuente": "controlador.conectado", "op": "es_verdadero"}


def por_sensor(apps, schema_editor):
    """Pasa a sensor las dos validaciones, si siguen como las dejo la semilla."""
    Procedimiento = apps.get_model("procedimientos", "ProcedimientoGuiado")
    Validacion = apps.get_model("procedimientos", "ValidacionPaso")
    proc = Procedimiento.objects.filter(codigo=CODIGO).first()
    if proc is None:
        return
    cambiadas = Validacion.objects.filter(
        paso__procedimiento=proc, paso__orden=1, texto__in=TEXTOS, tipo="MANUAL"
    ).update(tipo="SENSOR", condicion=CONECTADO)
    if cambiadas:
        proc.version += 1
        proc.save(update_fields=["version"])


def a_manual(apps, schema_editor):
    """Vuelve las dos validaciones a manuales."""
    Validacion = apps.get_model("procedimientos", "ValidacionPaso")
    Validacion.objects.filter(
        paso__procedimiento__codigo=CODIGO, paso__orden=1, texto__in=TEXTOS, condicion=CONECTADO
    ).update(tipo="MANUAL", condicion=None)


class Migration(migrations.Migration):
    """Ver docstring del modulo."""

    dependencies = [("procedimientos", "0001_initial")]

    operations = [migrations.RunPython(por_sensor, a_manual)]
