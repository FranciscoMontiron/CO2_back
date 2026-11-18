"""Paso «Loop de grabado»: el progreso se lee con los campos que publica el controlador.

El controlador ya no publica ``ejecucion.pulso`` (pulso en curso) ni
``ejecucion.distancia_mm``: publica ``acciones``, ``total``, ``pulsos`` (hechos),
``ubicacion`` y ``ultimo_pulso_ms``. Corrige las bases donde PROC-001 ya existe
(la semilla no pisa un procedimiento cargado). Cada texto se reemplaza solo si
sigue igual al de la semilla anterior, y la versión sube una vez si algo cambió.
"""

from django.db import migrations

CODIGO = "PROC-001"
TITULO = "Loop de grabado"
INDICADOR = "Progreso"
INDICADOR_VIEJO = "{ejecucion.pulso} / {ejecucion.pulsos} pulsos"
INDICADOR_NUEVO = "{ejecucion.pulsos} / {programa.pulsos} pulsos"
PRIMER_PULSO = "Primer pulso ejecutado correctamente"
TERMINADO = {"fuente": "ensayo.terminado", "op": "es_verdadero"}
CONDICION_VIEJA = {"alguna": [{"fuente": "ejecucion.pulso", "op": ">=", "valor": 1}, TERMINADO]}
CONDICION_NUEVA = {"alguna": [{"fuente": "ejecucion.pulsos", "op": ">=", "valor": 1}, TERMINADO]}
DETALLE_PULSO_VIEJO = (
    "Con «Iniciar grabado» arranca el loop: en cada pulso el shutter abre durante "
    "la duración configurada (p. ej. 400 ms) y la fibra se desplaza la distancia "
    "configurada (p. ej. 550 µm)."
)
DETALLE_PULSO_NUEVO = (
    "Con «Iniciar grabado» arranca el programa: cada pulso abre el shutter la "
    "duración de su paso (p. ej. 400 ms) y cada «mover» desplaza la fibra lo que "
    "indique (p. ej. 0,55 mm)."
)
FIN_VIEJO = "Criterio de finalización alcanzado"
FIN_NUEVO = "Programa completado"
DETALLE_FIN_VIEJO = (
    "El loop se repite hasta cumplir el criterio de finalización (distancia total "
    "o pulsos). Para cortarlo antes, usar «Abortar»."
)
DETALLE_FIN_NUEVO = (
    "El programa termina al ejecutar todos sus pasos, con cada bucle repetido las "
    "veces indicadas. Para cortarlo antes, usar «Abortar»."
)


def progreso_por_acciones(apps, schema_editor):
    """Pasa el indicador y la validación del primer pulso a ``ejecucion.pulsos``."""
    Procedimiento = apps.get_model("procedimientos", "ProcedimientoGuiado")
    Paso = apps.get_model("procedimientos", "PasoGuiado")
    proc = Procedimiento.objects.filter(codigo=CODIGO).first()
    paso = Paso.objects.filter(procedimiento=proc, titulo=TITULO).first() if proc else None
    if paso is None:
        return
    cambios = 0
    for indicador in paso.indicadores or []:
        if indicador.get("nombre") != INDICADOR:
            continue
        for estado in indicador.get("estados", []):
            if estado.get("texto") == INDICADOR_VIEJO:
                estado["texto"] = INDICADOR_NUEVO
                cambios += 1
    if cambios:
        paso.save(update_fields=["indicadores"])
    # El JSON se compara en Python: el filtro por igualdad de JSONField varía entre motores.
    for v in paso.validaciones.filter(texto__in=[PRIMER_PULSO, FIN_VIEJO]):
        campos = []
        if v.texto == PRIMER_PULSO:
            if v.condicion == CONDICION_VIEJA:
                v.condicion = CONDICION_NUEVA
                campos.append("condicion")
            if v.detalle == DETALLE_PULSO_VIEJO:
                v.detalle = DETALLE_PULSO_NUEVO
                campos.append("detalle")
        elif v.detalle == DETALLE_FIN_VIEJO:
            v.texto, v.detalle = FIN_NUEVO, DETALLE_FIN_NUEVO
            campos += ["texto", "detalle"]
        if campos:
            v.save(update_fields=campos)
            cambios += 1
    if cambios:
        proc.version += 1
        proc.save(update_fields=["version"])


class Migration(migrations.Migration):
    """Ver docstring del modulo."""

    dependencies = [("procedimientos", "0006_sin_criterio_de_fin")]

    operations = [migrations.RunPython(progreso_por_acciones, migrations.RunPython.noop)]
