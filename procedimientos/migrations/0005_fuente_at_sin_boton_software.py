"""Paso «Fuente de alta tension»: la alta tension se habilita solo con el boton fisico.

Corrige las bases donde PROC-001 ya existe (la semilla no pisa un
procedimiento cargado), solo si el paso sigue ofreciendo ``habilitar_at``, y
sube la version para que el cambio quede asentado.
"""

from django.db import migrations

CODIGO = "PROC-001"
TITULO = "Fuente de alta tensión"
TENSION = {"fuente": "variables.TENSION_AT", "op": ">=", "valor": 15}
ENCENDIDA_SW = {"fuente": "condiciones.AT_ENCENDIDA", "op": "es_verdadero"}
TEXTO_NUEVO = "Fuente HV habilitada"
TEXTO_VIEJO = "Fuente HV habilitada por software"
DETALLE_NUEVO = (
    "Habilitar la fuente con el botón físico del laboratorio y esperar a que la "
    "tensión llegue a 15 kV."
)
DETALLE_VIEJO = "Confirmar el encendido desde software con «Habilitar alta tensión»."
BOTON = {"accion": "habilitar_at", "etiqueta": "Habilitar alta tensión"}


def _paso(apps):
    Procedimiento = apps.get_model("procedimientos", "ProcedimientoGuiado")
    Paso = apps.get_model("procedimientos", "PasoGuiado")
    proc = Procedimiento.objects.filter(codigo=CODIGO).first()
    paso = Paso.objects.filter(procedimiento=proc, titulo=TITULO).first() if proc else None
    return proc, paso


def sin_boton(apps, schema_editor):
    """Quita el boton de software y pasa el avance y la validacion a la tension."""
    proc, paso = _paso(apps)
    if paso is None or BOTON not in (paso.acciones or []):
        return
    paso.acciones = [a for a in paso.acciones if a != BOTON]
    paso.condicion_avance = TENSION
    for indicador in paso.indicadores or []:
        if indicador.get("nombre") == "Fuente HV (SW)":
            indicador["nombre"] = "Fuente HV"
        for estado in indicador.get("estados", []):
            if estado.get("cuando") == ENCENDIDA_SW and indicador.get("nombre") == "Fuente HV":
                estado["cuando"] = TENSION
    paso.save(update_fields=["acciones", "condicion_avance", "indicadores"])
    paso.validaciones.filter(texto=TEXTO_VIEJO).update(
        texto=TEXTO_NUEVO, detalle=DETALLE_NUEVO, tipo="SENSOR", condicion=TENSION
    )
    proc.version += 1
    proc.save(update_fields=["version"])


class Migration(migrations.Migration):
    """Ver docstring del modulo."""

    dependencies = [("procedimientos", "0004_paso2_interruptor_refrigeracion")]

    operations = [migrations.RunPython(sin_boton, migrations.RunPython.noop)]
