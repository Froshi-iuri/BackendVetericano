"""
Test centinela de infraestructura y guardarrailes fail-closed.
Verifica que manage.py bloquee inmediatamente cualquier intento de correr tests
sin la configuracion explicita vetericano.test_settings.
"""

import os
import sys
import subprocess
from django.conf import settings
from django.test import SimpleTestCase


class GuardarrailSettingsTestCase(SimpleTestCase):
    """
    Test centinela de infraestructura que verifica el comportamiento fail-closed
    de manage.py ante intentos de ejecucion con settings de produccion.
    """

    def test_bloqueo_fail_closed_manage_py_con_settings_produccion(self):
        # 1. Configurar entorno del subproceso con DATABASE_URL ficticia local
        # Garantiza que bajo ninguna circunstancia se conecte a servicios externos ni a hosts remotos.
        env = os.environ.copy()
        env['DATABASE_URL'] = 'postgres://postgres:dummy@127.0.0.1:5432/dummy_db'
        env['DJANGO_SETTINGS_MODULE'] = 'vetericano.settings'

        cmd = [
            sys.executable,
            'manage.py',
            'test',
            '--settings=vetericano.settings'
        ]

        # 2. Ejecutar subproceso contra manage.py
        resultado = subprocess.run(
            cmd,
            cwd=str(settings.BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=15
        )

        # 3. Comprobar que manage.py aborte con codigo de error no cero
        self.assertNotEqual(
            resultado.returncode,
            0,
            "Fallo de guardarraíl: 'manage.py test --settings=vetericano.settings' no retorno codigo de error."
        )

        # 4. Comprobar que stderr contenga el mensaje literal de BLOQUEO DE SEGURIDAD
        stderr = resultado.stderr
        if 'BLOQUEO DE SEGURIDAD' not in stderr:
            self.fail(
                "Fallo de guardarraíl: manage.py no bloqueo la ejecucion con settings de produccion. "
                f"stderr obtenido:\n{stderr}\nstdout obtenido:\n{resultado.stdout}"
            )
