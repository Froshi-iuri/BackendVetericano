"""Configuración estrictamente local para pruebas E2E con PostgreSQL 18 + VetericanoIA.

Nunca usar en producción ni modificar vetericano/settings.py.
"""

from __future__ import annotations

import os
import sys

from vetericano.settings import *  # noqa: F401,F403

if sys.platform == 'win32':
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment') as _rk:
            for _var in ('TEST_DB_HOST', 'TEST_DB_PORT', 'TEST_DB_USER', 'TEST_DB_PASSWORD'):
                if not os.environ.get(_var):
                    try:
                        _val, _ = winreg.QueryValueEx(_rk, _var)
                        os.environ[_var] = str(_val)
                    except OSError:
                        pass
    except Exception:
        pass

_LOCAL_HOST = os.environ.get('TEST_DB_HOST', '127.0.0.1').strip() or '127.0.0.1'
if _LOCAL_HOST not in {'127.0.0.1', 'localhost', '::1'}:
    raise RuntimeError(f'Bloqueo de seguridad: host no local ({_LOCAL_HOST})')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('E2E_DB_NAME', 'vetericano_e2e_dinov2'),
        'USER': os.environ.get('TEST_DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('TEST_DB_PASSWORD', ''),
        'HOST': _LOCAL_HOST,
        'PORT': str(os.environ.get('TEST_DB_PORT', '5433')),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {
            'sslmode': 'disable',
        },
    }
}

DEBUG = True
ALLOWED_HOSTS = ['*']

# Activación de DINOv2 vía microservicio únicamente en este entorno local
DUPLICADOS_IA_DINOV2_ACTIVA = True
DUPLICADOS_DINOV2_BACKEND = 'microservicio'
DUPLICADOS_DINOV2_MICROSERVICIO_URL = os.environ.get(
    'DUPLICADOS_DINOV2_MICROSERVICIO_URL',
    'http://127.0.0.1:8001',
)
DUPLICADOS_DINOV2_MICROSERVICIO_SECRET = os.environ.get(
    'DUPLICADOS_DINOV2_MICROSERVICIO_SECRET',
    'vetericano-ia-local-e2e-secret-2026',
)
DUPLICADOS_DINOV2_CACHE_BD_ACTIVA = True
DUPLICADOS_UMBRAL_DINOV2 = 0.45
DUPLICADOS_RADIO_GPS_METROS = 200.0
