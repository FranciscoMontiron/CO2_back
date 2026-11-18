from django.db import migrations


def dejar_sin_validar(apps, schema_editor):
    """Los programas viejos no nombran componentes: no se convierten solos.

    Quedan legibles pero sin validar, a la espera de que se rearmen con pasos.
    """
    apps.get_model("programas", "Programa").objects.update(validado=False)


class Migration(migrations.Migration):

    dependencies = [
        ("programas", "0005_pasos_en_arbol"),
    ]

    operations = [
        migrations.RunPython(dejar_sin_validar, migrations.RunPython.noop),
    ]
