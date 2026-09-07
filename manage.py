#!/usr/bin/env python
"""Utilidad de linea de comandos de Django para tareas administrativas."""

import os
import sys


def main() -> None:
    """Ejecuta la tarea administrativa indicada por los argumentos de linea."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - fallo de entorno, no de logica
        raise ImportError(
            "No se pudo importar Django. Verificar que este instalado y que el "
            "entorno virtual este activo."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
