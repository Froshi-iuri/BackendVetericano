"""
Configuración local de Django para pruebas e integración de `triaje_ia`
contra PostgreSQL 18 local (`vetericano_triaje_local`), completamente aislada
de producción (Supabase/Railway).
"""

import os
from pathlib import Path


def _cargar_env_local(ruta: Path) -> None:
    if not ruta.is_file():
        return
    for raw in ruta.read_text(encoding='utf-8').splitlines():
        linea = raw.strip()
        if not linea or linea.startswith('#') or '=' not in linea:
            continue
        clave, valor = linea.split('=', 1)
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if clave:
            os.environ.setdefault(clave, valor)


def _leer_env_usuario_windows(nombre: str) -> str:
    if os.name != 'nt':
        return ''
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            val, _ = winreg.QueryValueEx(key, nombre)
            return str(val or '').strip()
    except OSError:
        return ''


_BASE_DIR_TRIAJE = Path(__file__).resolve().parent.parent
_cargar_env_local(_BASE_DIR_TRIAJE / '.env.triaje.local')

# Respetar valores existentes en os.environ; por defecto mantener triaje inactivo
# salvo que el proceso de verificación lo habilite explícitamente.
os.environ.setdefault('TRIAJE_N8N_ACTIVO', 'False')

from vetericano.settings import *  # noqa: E402,F401,F403

_DB_HOST = (
    os.environ.get('TRIAJE_LOCAL_DB_HOST')
    or os.environ.get('TEST_DB_HOST')
    or _leer_env_usuario_windows('TEST_DB_HOST')
    or '127.0.0.1'
)
_DB_PORT = (
    os.environ.get('TRIAJE_LOCAL_DB_PORT')
    or os.environ.get('TEST_DB_PORT')
    or _leer_env_usuario_windows('TEST_DB_PORT')
    or '5433'
)
_DB_USER = (
    os.environ.get('TRIAJE_LOCAL_DB_USER')
    or os.environ.get('TEST_DB_USER')
    or _leer_env_usuario_windows('TEST_DB_USER')
    or 'postgres'
)
_DB_PASSWORD = (
    os.environ.get('TRIAJE_LOCAL_DB_PASSWORD')
    or os.environ.get('TEST_DB_PASSWORD')
    or _leer_env_usuario_windows('TEST_DB_PASSWORD')
    or ''
)
_DB_NAME = os.environ.get('TRIAJE_LOCAL_DB_NAME') or 'vetericano_triaje_local'

if _DB_HOST not in ('127.0.0.1', 'localhost') or 'supabase' in _DB_HOST.lower():
    raise RuntimeError(
        f'settings_local_triaje bloqueado: host no local detectado ({_DB_HOST}).'
    )

DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'host.docker.internal', '*']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': _DB_NAME,
        'USER': _DB_USER,
        'PASSWORD': _DB_PASSWORD,
        'HOST': _DB_HOST,
        'PORT': _DB_PORT,
        'CONN_MAX_AGE': 0,
        'OPTIONS': {
            'sslmode': 'disable',
            'connect_timeout': 5,
        },
    }
}

# Releer secretos de triaje desde entorno/.env.triaje.local tras importar settings base
TRIAJE_N8N_WEBHOOK_URL = (
    os.environ.get('TRIAJE_N8N_WEBHOOK_URL')
    or 'http://127.0.0.1:5678/webhook/vetericano-triaje-peticion'
)
TRIAJE_N8N_WEBHOOK_SECRET = os.environ.get('TRIAJE_N8N_WEBHOOK_SECRET', TRIAJE_N8N_WEBHOOK_SECRET)
TRIAJE_SERVICE_TOKEN = os.environ.get('TRIAJE_SERVICE_TOKEN', TRIAJE_SERVICE_TOKEN)
TRIAJE_N8N_ACTIVO = os.environ.get('TRIAJE_N8N_ACTIVO', 'False').strip().lower() in ('1', 'true', 'yes', 'on')
