# El espectro pasa a guardar sus longitudes de onda en vez de reconstruirlas
# (ADR-0008).
#
# El orden de las operaciones importa y NO es el que genera makemigrations, que
# borraba primero la columna vieja: eso destruiria el dato con el que se
# reconstruye el eje de los espectros ya cargados. Aca se agrega la columna
# nueva, se completa a partir de la vieja con la formula de M1, y recien
# despues se borra la vieja.

from django.db import migrations, models


def reconstruir_longitudes(apps, schema_editor):
    """Completa `longitudes_onda` con la grilla regular que se asumia en M1."""
    Espectro = apps.get_model("fabricacion", "Espectro")
    for espectro in Espectro.objects.select_related("procedimiento"):
        paso = espectro.procedimiento.resolucion
        espectro.longitudes_onda = [
            espectro.longitud_onda_inicial + i * paso for i in range(len(espectro.transmitancias))
        ]
        espectro.save(update_fields=["longitudes_onda"])


def restaurar_inicial(apps, schema_editor):
    """Vuelta atras: la longitud inicial es el primer elemento del eje."""
    Espectro = apps.get_model("fabricacion", "Espectro")
    for espectro in Espectro.objects.all():
        espectro.longitud_onda_inicial = (
            espectro.longitudes_onda[0] if espectro.longitudes_onda else 0.0
        )
        espectro.save(update_fields=["longitud_onda_inicial"])


class Migration(migrations.Migration):

    dependencies = [
        (
            "fabricacion",
            "0002_remove_picodeatenuacion_una_sola_resonancia_principal_por_red_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="espectro",
            name="longitudes_onda",
            field=models.JSONField(default=list, verbose_name="longitudes de onda [nm]"),
        ),
        migrations.RunPython(reconstruir_longitudes, restaurar_inicial),
        migrations.RemoveField(
            model_name="espectro",
            name="longitud_onda_inicial",
        ),
    ]
