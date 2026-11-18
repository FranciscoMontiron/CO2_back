"""Paso «Parámetros base»: el programa ya no tiene criterio de finalización.

Corrige las bases donde PROC-001 ya existe (la semilla no pisa un procedimiento
cargado), solo si el paso todavía exige el criterio de fin, y sube la versión
para que el cambio quede asentado.
"""

from django.db import migrations

CODIGO = "PROC-001"
VALIDACION_VIEJA = "Criterio de finalización definido"
INDICADOR_VIEJO = "Criterio de fin"
INDICADOR_NUEVO = "Resumen del programa"
TEXTO_VIEJO = "{programa.distancia_mm:2} mm · {programa.pulsos} pulsos"
TEXTO_NUEVO = "{programa.pulsos} pulsos · {programa.duracion_min_s:1} s mínimo"
DETALLE_VIEJO = "Verificar potencia objetivo, duración de pulso y desplazamiento por pulso."
DETALLE_NUEVO = (
    "Revisar en el resumen del programa la cantidad de pulsos, el recorrido y la duración mínima."
)


def sin_criterio(apps, schema_editor):
    """Quita la validación del criterio de fin y muestra pulsos y duración mínima."""
    Procedimiento = apps.get_model("procedimientos", "ProcedimientoGuiado")
    Validacion = apps.get_model("procedimientos", "ValidacionPaso")
    proc = Procedimiento.objects.filter(codigo=CODIGO).first()
    if proc is None:
        return
    vieja = Validacion.objects.filter(paso__procedimiento=proc, texto=VALIDACION_VIEJA).first()
    if vieja is None:
        return
    paso = vieja.paso
    vieja.delete()
    for orden, v in enumerate(paso.validaciones.order_by("orden"), 1):
        if v.orden != orden:
            v.orden = orden
            v.save(update_fields=["orden"])
    paso.validaciones.filter(detalle=DETALLE_VIEJO).update(detalle=DETALLE_NUEVO)
    for indicador in paso.indicadores or []:
        if indicador.get("nombre") != INDICADOR_VIEJO:
            continue
        indicador["nombre"] = INDICADOR_NUEVO
        for estado in indicador.get("estados", []):
            if estado.get("texto") == TEXTO_VIEJO:
                estado["texto"] = TEXTO_NUEVO
    paso.save(update_fields=["indicadores"])
    proc.version += 1
    proc.save(update_fields=["version"])


class Migration(migrations.Migration):
    """Ver docstring del modulo."""

    dependencies = [("procedimientos", "0005_fuente_at_sin_boton_software")]

    operations = [migrations.RunPython(sin_criterio, migrations.RunPython.noop)]
