#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vetericano.settings')
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        settings_arg = None
        for i, arg in enumerate(sys.argv):
            if arg.startswith('--settings='):
                settings_arg = arg.split('=', 1)[1]
            elif arg == '--settings' and i + 1 < len(sys.argv):
                settings_arg = sys.argv[i + 1]
        active_settings = settings_arg or os.environ.get('DJANGO_SETTINGS_MODULE')
        if active_settings != 'vetericano.test_settings':
            raise RuntimeError(
                "BLOQUEO DE SEGURIDAD: Se intentó ejecutar 'manage.py test' sin 'vetericano.test_settings'. "
                f"Configuración detectada: '{active_settings}'. "
                "Debe especificar explícitamente '--settings=vetericano.test_settings' "
                "o definir DJANGO_SETTINGS_MODULE=vetericano.test_settings."
            )
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
