"""
Arranque seguro de SECRET_KEY (vetericano/settings.py).

Cada caso importa los settings en un proceso nuevo con un entorno controlado (sin red ni BD):
en Railway el servicio no debe arrancar con una SECRET_KEY ausente, pública o débil; en local
y en pruebas debe seguir funcionando la clave de desarrollo. Ningún caso imprime claves.
"""

import os
import secrets
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BACKEND = Path(__file__).resolve().parent.parent
CLAVE_PUBLICA = 'django-insecure-k8kuso8-gb$syavcny^-$li&jsmfbvd89_8#8f_oq4kbhju*9m'
SONDA = (
    "import sys; import vetericano.settings as s; "
    "sys.stdout.write('OK' if s.SECRET_KEY == sys.argv[1] else 'DISTINTA')"
)
VARIABLES_A_LIMPIAR = (
    'SECRET_KEY', 'RAILWAY_PROJECT_ID', 'RAILWAY_ENVIRONMENT_ID', 'EXIGIR_SECRET_KEY_SEGURA',
    'DATABASE_URL', 'REDIS_URL', 'CHANNEL_REDIS_URL', 'DJANGO_SETTINGS_MODULE',
)


def _importar_settings(env_extra, esperada=''):
    env = {k: v for k, v in os.environ.items() if k not in VARIABLES_A_LIMPIAR}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, '-c', SONDA, esperada], cwd=str(BACKEND), env=env,
        capture_output=True, text=True, timeout=120,
    )


class SecretKeyArranqueSeguroTests(SimpleTestCase):
    RAILWAY = {'RAILWAY_PROJECT_ID': 'proyecto-de-prueba', 'RAILWAY_ENVIRONMENT_ID': 'entorno-de-prueba'}

    def assertNoArranca(self, resultado, clave=''):
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn('ImproperlyConfigured', resultado.stderr)
        self.assertIn('SECRET_KEY', resultado.stderr)
        if clave:
            self.assertNotIn(clave, resultado.stderr + resultado.stdout)

    def test_railway_sin_secret_key_no_arranca(self):
        self.assertNoArranca(_importar_settings(self.RAILWAY))

    def test_railway_con_la_clave_publica_no_arranca(self):
        self.assertNoArranca(_importar_settings({**self.RAILWAY, 'SECRET_KEY': CLAVE_PUBLICA}), CLAVE_PUBLICA)

    def test_railway_con_prefijo_django_insecure_no_arranca(self):
        clave = 'django-insecure-' + secrets.token_urlsafe(48)
        self.assertNoArranca(_importar_settings({**self.RAILWAY, 'SECRET_KEY': clave}), clave)

    def test_railway_con_clave_corta_o_repetitiva_no_arranca(self):
        for clave in (secrets.token_urlsafe(20), 'a' * 60):
            with self.subTest(longitud=len(clave)):
                self.assertNoArranca(_importar_settings({**self.RAILWAY, 'SECRET_KEY': clave}), clave)

    def test_railway_con_clave_fuerte_arranca(self):
        clave = secrets.token_urlsafe(64)
        resultado = _importar_settings({**self.RAILWAY, 'SECRET_KEY': clave}, esperada=clave)
        self.assertEqual(resultado.returncode, 0, resultado.stderr[-500:])
        self.assertEqual(resultado.stdout, 'OK')

    def test_local_sin_secret_key_usa_la_de_desarrollo(self):
        resultado = _importar_settings({}, esperada=CLAVE_PUBLICA)
        self.assertEqual(resultado.returncode, 0, resultado.stderr[-500:])
        self.assertEqual(resultado.stdout, 'OK')

    def test_exigir_fuera_de_railway_aplica_la_misma_regla(self):
        self.assertNoArranca(_importar_settings({'EXIGIR_SECRET_KEY_SEGURA': 'True'}))
