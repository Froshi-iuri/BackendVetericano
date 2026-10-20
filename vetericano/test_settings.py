"""
Configuracion de pruebas aislada y fail-closed para el proyecto VETERICANO.
Valida estrictamente que las pruebas unicamente se ejecuten contra PostgreSQL local.
"""

import os
import sys
import urllib.parse
from django.core.exceptions import ImproperlyConfigured

# TEMPORAL: retirar cuando Antigravity herede las variables
if sys.platform == 'win32':
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as rk:
            for k in ['TEST_DB_HOST', 'TEST_DB_PORT', 'TEST_DB_USER', 'TEST_DB_PASSWORD', 'TEST_DB_TEMPLATE', 'TEST_DB_NAME']:
                if k not in os.environ:
                    try:
                        val, _ = winreg.QueryValueEx(rk, k)
                        os.environ[k] = str(val)
                    except OSError:
                        pass
    except Exception:
        pass

# 1. Sobrescritura forzada DIRECTA de DATABASE_URL antes de importar settings:
# No se utiliza setdefault() para impedir que un valor remoto preexistente en el shell o variables previas sea preservado.
test_db_user = os.getenv('TEST_DB_USER', 'postgres')
test_db_password = os.getenv('TEST_DB_PASSWORD', 'postgres')
test_db_host = os.getenv('TEST_DB_HOST', '127.0.0.1')
test_db_port = os.getenv('TEST_DB_PORT', '5432')
test_db_name = os.getenv('TEST_DB_NAME', 'test_vetericano')
test_db_template = os.getenv('TEST_DB_TEMPLATE', 'template_vetericano_test')

if test_db_name != 'test_vetericano':
    raise ImproperlyConfigured(
        f"FALLO DE SEGURIDAD: TEST_DB_NAME debe ser exactamente 'test_vetericano'. Valor detectado: '{test_db_name}'."
    )
if test_db_template != 'template_vetericano_test':
    raise ImproperlyConfigured(
        f"FALLO DE SEGURIDAD: TEST_DB_TEMPLATE debe ser exactamente 'template_vetericano_test'. Valor detectado: '{test_db_template}'."
    )

encoded_password = urllib.parse.quote_plus(test_db_password)
os.environ['DATABASE_URL'] = f"postgres://{test_db_user}:{encoded_password}@{test_db_host}:{test_db_port}/{test_db_name}"

from vetericano.settings import *

# 2. Configuracion explicita del motor de base de datos local para pruebas:
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': test_db_name,
        'USER': test_db_user,
        'PASSWORD': test_db_password,
        'HOST': test_db_host,
        'PORT': test_db_port,
        'TEST': {
            'NAME': test_db_name,
            'TEMPLATE': test_db_template,
            'MIGRATE': False,
        },
        'OPTIONS': {
            'sslmode': 'disable',
        }
    }
}

# 3. Asercion obligatoria sobre TODOS los alias configurados en DATABASES:
prohibidas = ['supabase', 'pooler', 'aws', 'railway', 'render']
for alias, config in DATABASES.items():
    host = str(config.get('HOST', '')).lower()
    db_name = str(config.get('NAME', '')).lower()
    test_name = str(config.get('TEST', {}).get('NAME', '')).lower()

    if host not in ['127.0.0.1', 'localhost', '::1']:
        raise ImproperlyConfigured(
            f"FALLO DE SEGURIDAD: El alias '{alias}' apunta a host '{host}'. "
            f"Las pruebas SOLO pueden correr contra bases de datos locales."
        )
    for prohibido in prohibidas:
        if prohibido in host or prohibido in db_name or prohibido in test_name:
            raise ImproperlyConfigured(
                f"FALLO DE SEGURIDAD: Detectada cadena prohibida '{prohibido}' en base '{alias}'. "
                f"Abortando ejecucion de pruebas para proteger datos remotos."
            )

# 4. Aislamiento de correo y test runner
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
TEST_RUNNER = 'inventario.tests.runner.VetericanoTestRunner'

# 5. Regla Estricta del Ciclo de Vida de Conexiones:
# Durante la evaluacion e importacion de test_settings.py NO se abre NINGUNA conexion a base de datos.
# Las conexiones solo se establecen cuando el test runner inicia la base de datos de test local.
