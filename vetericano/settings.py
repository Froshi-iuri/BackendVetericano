from pathlib import Path
import logging
import os
import re
import sys
from typing import Optional
import dj_database_url
from dotenv import load_dotenv
from datetime import timedelta

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# carga las variables del archivo .env desde la raíz del proyecto
load_dotenv(BASE_DIR / '.env')

STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
# Clave SOLO para desarrollo local y pruebas: es pública (está en el repositorio), así que nunca
# se acepta en Railway (ver validación más abajo, después de detectar el entorno).
_SECRET_KEY_DESARROLLO = 'django-insecure-k8kuso8-gb$syavcny^-$li&jsmfbvd89_8#8f_oq4kbhju*9m'
_SECRET_KEY_ENTORNO = os.environ.get('SECRET_KEY', '')
# El valor se usa tal cual (sin strip): recortarlo cambiaría la clave efectiva e invalidaría los JWT.
SECRET_KEY = _SECRET_KEY_ENTORNO if _SECRET_KEY_ENTORNO.strip() else _SECRET_KEY_DESARROLLO

# SECURITY WARNING: don't run with debug turned on in production!
# Seguro por defecto: sin la variable DEBUG -> False.
# Solo se activa en desarrollo local definiendo explícitamente DEBUG=True (p. ej. en el .env local).
# En Railway (que inyecta RAILWAY_PROJECT_ID / RAILWAY_ENVIRONMENT_ID en cada despliegue) DEBUG
# se fuerza a False aunque la variable diga True: la página técnica de Django nunca llega a producción.
_DEBUG_SOLICITADO = os.environ.get('DEBUG', 'False').strip().lower() in ('true', '1', 'yes', 'on')
_EN_RAILWAY = bool(os.environ.get('RAILWAY_PROJECT_ID') or os.environ.get('RAILWAY_ENVIRONMENT_ID'))
DEBUG = _DEBUG_SOLICITADO and not _EN_RAILWAY
if _DEBUG_SOLICITADO and _EN_RAILWAY:
    sys.stderr.write('[settings] DEBUG=True ignorado: en Railway DEBUG siempre es False.\n')


def _secret_key_insegura(clave: str) -> bool:
    """Mismos criterios que el check security.W009 de Django + rechazo explícito de la clave pública."""
    return (
        not clave
        or clave == _SECRET_KEY_DESARROLLO
        or clave.startswith('django-insecure-')
        or len(clave) < 50
        or len(set(clave)) < 5
    )


# Arranque seguro (fail-closed): en Railway (servicio web y servicio WebSocket, que cargan este mismo
# archivo) el proceso NO arranca con una SECRET_KEY ausente, pública o débil, porque con ella cualquiera
# podría firmar JWT válidos de cualquier usuario. EXIGIR_SECRET_KEY_SEGURA=True aplica la misma regla
# fuera de Railway (p. ej. un staging propio). En desarrollo local y pruebas se usa la clave de desarrollo.
if (_EN_RAILWAY or os.environ.get('EXIGIR_SECRET_KEY_SEGURA', '').strip().lower() in ('true', '1', 'yes', 'si', 'sí', 'on')) \
        and _secret_key_insegura(SECRET_KEY):
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        'SECRET_KEY ausente, pública o débil: el servicio no arranca. Define en Railway una SECRET_KEY '
        'aleatoria de al menos 50 caracteres (la misma en el servicio web y en vetericano-ws) que no '
        'empiece por "django-insecure-".'
    )


# ALLOWED_HOSTS = [
#     '.onrender.com',
#     'localhost',
#     '127.0.0.1'
# ]
ALLOWED_HOSTS = ['*']



# los mas papis

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',  # <-- AGREGAR ESTA LÍNEA (Obligatorio para Tokens)
    'drf_yasg',
    'corsheaders',
    'users',
    'examenes_clinicos',
    'especies',
    'medicamentos',
    'patologias',
    'adopciones',
    'voluntariado',
    'peticiones',
    'animales',
    'clinica',
    'inventario',
    'dashboard',
    'realtime',
    'diagnostico_ia',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',#esto lo subi JAJAJAJJA
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'vetericano.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'vetericano.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv('DATABASE_URL'),
        conn_max_age=int(os.getenv('DB_CONN_MAX_AGE', '600')),
        ssl_require=True,
    )
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'America/Bogota'

USE_I18N = True

USE_TZ = False


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'
# STATICFILES_STORAGE se eliminó en Django 5.1: Django 6.1 lo ignoraba en silencio. Se declara STORAGES
# explícito con el almacenamiento simple (sin manifest): el build de Railway NO ejecuta collectstatic,
# y un storage con manifest sin collectstatic provocaría HTTP 500 en /admin/ y /swagger/.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
# Con DEBUG=False, WhiteNoise sirve los estáticos de admin, DRF y drf-yasg directamente desde las apps
# instaladas (finders), indexándolos una sola vez al arrancar cada worker (sin autorefresh).
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG


# Logging (los errores 500 y avisos de django.request deben verse en los logs de Railway con DEBUG=False).
# El formateador redacta JWT, cabeceras Bearer, contraseñas/claves y credenciales en URLs de BD
# antes de escribir cada línea (incluidas las trazas de excepción).
_PATRONES_SECRETOS = (
    (re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'), '[JWT-REDACTADO]'),
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+'), r'\1[REDACTADO]'),
    (re.compile(
        r'(?i)((?:password|passwd|contrase(?:ñ|n)a|secret|api[_-]?key|access|refresh|token)["\']?\s*[:=]\s*["\']?)'
        r'[^\s"\',;&}]+'
    ), r'\1[REDACTADO]'),
    (re.compile(r'(?i)((?:postgres(?:ql)?|redis|rediss)://[^:/\s@]+:)[^@\s]+@'), r'\1[REDACTADO]@'),
)


class FormateadorSinSecretos(logging.Formatter):
    """Formatter de consola que redacta secretos en el mensaje y en la traza ya formateados."""

    def format(self, record):
        texto = super().format(record)
        for patron, reemplazo in _PATRONES_SECRETOS:
            texto = patron.sub(reemplazo, texto)
        return texto


_NIVEL_LOG = os.environ.get('DJANGO_LOG_LEVEL', 'INFO').strip().upper() or 'INFO'
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'consola': {
            '()': FormateadorSinSecretos,
            'format': '{asctime} {levelname} {name} [pid={process}] {message}',
            'style': '{',
        },
    },
    'handlers': {
        'consola': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
            'formatter': 'consola',
        },
    },
    'root': {'handlers': ['consola'], 'level': _NIVEL_LOG},
    'loggers': {
        'django': {'handlers': ['consola'], 'level': 'INFO', 'propagate': False},
        # 4xx -> WARNING, 5xx -> ERROR con traza (sin cabeceras ni cuerpo de la petición).
        'django.request': {'handlers': ['consola'], 'level': 'WARNING', 'propagate': False},
        'django.security': {'handlers': ['consola'], 'level': 'WARNING', 'propagate': False},
        # Nunca volcar SQL con parámetros a los logs.
        'django.db.backends': {'handlers': ['consola'], 'level': 'WARNING', 'propagate': False},
    },
}



# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

def _parse_bool_env(nombre: str, default: bool) -> bool:
    raw = os.environ.get(nombre)
    if raw is None:
        return default
    val = str(raw).strip().lower()
    if val in ('true', '1', 'yes', 'si', 'sí', 'on'):
        return True
    if val in ('false', '0', 'no', 'off'):
        return False
    return default


# Configuración oficial de Correo en Django
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 465))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
_email_tls_req = _parse_bool_env('EMAIL_USE_TLS', EMAIL_PORT == 587)
_email_ssl_req = _parse_bool_env('EMAIL_USE_SSL', EMAIL_PORT == 465 and not _email_tls_req)
if _email_tls_req and _email_ssl_req:
    if EMAIL_PORT == 587:
        _email_ssl_req = False
    else:
        _email_tls_req = False
EMAIL_USE_TLS = _email_tls_req
EMAIL_USE_SSL = _email_ssl_req


# Configuración base de Django REST Framework para exigir tokens por defecto
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'users.authentication.CustomJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'duplicados_ciudadano_rafaga': '10/min',
        'duplicados_ciudadano_sostenido': '60/hour',
    },
}

CORS_ALLOW_ALL_ORIGINS = True

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:4200",     
    "http://127.0.0.1:4200",
]

SIMPLE_JWT = {
    # El token de acceso dura 60 minutos. Si expira, el frontend debe usar el token de refresco.
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    # El token de refresco dura 30 días. Sirve para mantener la sesión abierta sin volver a loguearse.
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    # Al refrescar, se emite un nuevo refresh token para renovar la ventana de 30 días (rotación)
    'ROTATE_REFRESH_TOKENS': True,
    # No se usa blacklist para no requerir la app token_blacklist ni tablas adicionales
    'BLACKLIST_AFTER_ROTATION': False,
    # Le indicamos a la librería cuál es el campo de tu llave primaria personalizada
    'USER_ID_FIELD': 'id_usuario',
    'USER_ID_CLAIM': 'user_id',
}

# Configuración para despliegue (Railway / Proxies HTTPS)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

CSRF_TRUSTED_ORIGINS = [
    'https://*.railway.app',
    'https://*.up.railway.app',
]
csrf_env = os.getenv('CSRF_TRUSTED_ORIGINS')
if csrf_env:
    CSRF_TRUSTED_ORIGINS.extend([origin.strip() for origin in csrf_env.split(',') if origin.strip()])

# esto es para la configuración de almacenamiento de caché
# Verificamos si existe la variable que inyecta Railway en producción
if os.environ.get('REDIS_URL'):
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': os.environ.get('REDIS_URL'),
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            }
        }
    }
else:
    # Si la variable no existe, asumimos que estamos en desarrollo local
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }


# ─── Recuperación de contraseña: almacenamiento compartido entre workers ───────────────
# Los códigos de recuperación, sus intentos y sus límites NO pueden vivir en memoria por
# proceso: gunicorn corre 2 workers y la confirmación puede caer en otro worker distinto al
# que generó el código. Se usa un alias de caché propio ('recuperacion') sobre el Redis que
# ya existe: REDIS_URL si se define; si no, CHANNEL_REDIS_URL (el Redis del servicio
# WebSocket), en una base lógica aparte para no mezclarse con las claves 'asgi*' de Channels.
# El caché 'default' (throttles de duplicados) no cambia. Se usa el backend Redis integrado
# en Django (librería `redis`, ya presente en requirements.txt y requirements-ws.txt).
def _url_redis_con_db(url: str, db: int) -> str:
    """Devuelve la URL de Redis apuntando a la base lógica `db` (sustituye la ruta /N)."""
    from urllib.parse import urlsplit, urlunsplit
    partes = urlsplit(url)
    return urlunsplit((partes.scheme, partes.netloc, f'/{db}', partes.query, partes.fragment))


_RECUPERACION_REDIS_URL_BASE = (
    os.environ.get('REDIS_URL', '').strip() or os.environ.get('CHANNEL_REDIS_URL', '').strip()
)
try:
    _RECUPERACION_REDIS_DB = max(0, int(os.environ.get('RECUPERACION_REDIS_DB', '1')))
except (TypeError, ValueError):
    _RECUPERACION_REDIS_DB = 1

if _RECUPERACION_REDIS_URL_BASE:
    CACHES['recuperacion'] = {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': _url_redis_con_db(_RECUPERACION_REDIS_URL_BASE, _RECUPERACION_REDIS_DB),
        'KEY_PREFIX': 'vetericano-recuperacion',
        'TIMEOUT': 900,
        # Si Redis no responde, la petición falla rápido (el servicio responde 503).
        'OPTIONS': {'socket_connect_timeout': 2, 'socket_timeout': 2},
    }
else:
    # Desarrollo local y pruebas (un solo proceso).
    CACHES['recuperacion'] = {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'vetericano-recuperacion',
    }
RECUPERACION_CACHE_COMPARTIDA = bool(_RECUPERACION_REDIS_URL_BASE)
# En Railway se exige almacenamiento compartido: sin Redis la recuperación responde 503 en lugar
# de fallar de forma intermitente entre workers. Configurable para escenarios locales multiproceso.
RECUPERACION_EXIGIR_CACHE_COMPARTIDA = _parse_bool_env('RECUPERACION_EXIGIR_CACHE_COMPARTIDA', _EN_RAILWAY)

# Configuración para que Swagger pida el Token JWT en lugar de usuario/contraseña
SWAGGER_SETTINGS = {
    'SECURITY_DEFINITIONS': {
        'Bearer': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header',
            'description': "Introduce tu token de acceso en este formato: Bearer <tu_token>"
        }
    }
}


# ==============================================================================
# CONFIGURACIÓN DE DETECCIÓN AUTOMÁTICA DE PETICIONES DUPLICADAS
# ==============================================================================

_DUPLICADOS_BOOL_TRUTHY = frozenset({'true', '1', 'yes', 'si', 'sí', 'on'})
_DUPLICADOS_BOOL_FALSY = frozenset({'false', '0', 'no', 'off'})
_DUPLICADOS_AUTO_TIMEOUT_MS_DEFAULT = 1500
_DUPLICADOS_AUTO_TIMEOUT_MS_MIN = 100
_DUPLICADOS_AUTO_TIMEOUT_MS_MAX = 10000
_DUPLICADOS_AUTO_MAX_CANDIDATOS_TOPE = 50


def _leer_env_bool(nombre_var: str, default: bool = False) -> bool:
    """Convierte una variable de entorno a bool seguro sin tratar 'False' como True."""
    raw = os.environ.get(nombre_var)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    valor = str(raw).strip().lower()
    if not valor:
        return default
    if valor in _DUPLICADOS_BOOL_TRUTHY:
        return True
    if valor in _DUPLICADOS_BOOL_FALSY:
        return False
    return default


def _leer_env_duplicados_timeout_ms(default: int = _DUPLICADOS_AUTO_TIMEOUT_MS_DEFAULT) -> int:
    """
    Lee DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS conservando los límites de
    `alertas_duplicados_service.py` (100..10000 ms, 0 para deshabilitar, default 1500).
    """
    raw = os.environ.get('DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS')
    if raw is None or isinstance(raw, bool):
        return default
    texto = str(raw).strip()
    if not texto:
        return default
    try:
        valor = int(texto)
    except (TypeError, ValueError):
        return default
    if valor < 0:
        return default
    if valor == 0:
        return 0
    return max(_DUPLICADOS_AUTO_TIMEOUT_MS_MIN, min(valor, _DUPLICADOS_AUTO_TIMEOUT_MS_MAX))


def _leer_env_duplicados_max_candidatos() -> Optional[int]:
    """
    Lee DUPLICADOS_AUTO_MAX_CANDIDATOS opcional respetando los límites de
    `alertas_duplicados_service.py` (entero >= 1 acotado a 50, o None si ausente/inválido).
    """
    raw = os.environ.get('DUPLICADOS_AUTO_MAX_CANDIDATOS')
    if raw is None or isinstance(raw, bool):
        return None
    texto = str(raw).strip()
    if not texto:
        return None
    try:
        valor = int(texto)
    except (TypeError, ValueError):
        return None
    if valor < 1:
        return None
    return min(valor, _DUPLICADOS_AUTO_MAX_CANDIDATOS_TOPE)


DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA = _leer_env_bool(
    'DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA',
    default=False,
)
DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS = _leer_env_duplicados_timeout_ms()
DUPLICADOS_AUTO_MAX_CANDIDATOS = _leer_env_duplicados_max_candidatos()
DUPLICADOS_CIUDADANO_FOTO_ACTIVA = _leer_env_bool(
    'DUPLICADOS_CIUDADANO_FOTO_ACTIVA',
    default=True,
)


def _leer_env_float_rango(
    nombre_var: str,
    *,
    default: float,
    min_val: float,
    max_val: float,
) -> float:
    """Lee un número flotante desde variable de entorno acotado a [min_val, max_val]."""
    raw = os.environ.get(nombre_var)
    if raw is None or isinstance(raw, bool):
        return default
    texto = str(raw).strip()
    if not texto:
        return default
    try:
        valor = float(texto)
    except (TypeError, ValueError):
        return default
    if not (min_val <= valor <= max_val):
        return default
    return valor


# ==============================================================================
# CONFIGURACIÓN DE IA VISUAL DINOv2 SMALL PARA DETECCIÓN DE DUPLICADOS
# ==============================================================================
# Desactivada por defecto hasta validar su funcionamiento e instalar dependencias.
DUPLICADOS_IA_DINOV2_ACTIVA = _leer_env_bool(
    'DUPLICADOS_IA_DINOV2_ACTIVA',
    default=False,
)
DUPLICADOS_DINOV2_MODEL_NAME = (
    os.environ.get('DUPLICADOS_DINOV2_MODEL_NAME', 'facebook/dinov2-small').strip()
    or 'facebook/dinov2-small'
)
DUPLICADOS_UMBRAL_DINOV2 = _leer_env_float_rango(
    'DUPLICADOS_UMBRAL_DINOV2',
    default=0.45,
    min_val=0.01,
    max_val=1.0,
)
DUPLICADOS_RADIO_GPS_METROS = _leer_env_float_rango(
    'DUPLICADOS_RADIO_GPS_METROS',
    default=200.0,
    min_val=1.0,
    max_val=50000.0,
)
DUPLICADOS_DINOV2_DEVICE = (
    os.environ.get('DUPLICADOS_DINOV2_DEVICE', 'cpu').strip()
    or 'cpu'
)
DUPLICADOS_DINOV2_CACHE_BD_ACTIVA = _leer_env_bool(
    'DUPLICADOS_DINOV2_CACHE_BD_ACTIVA',
    default=False,
)
DUPLICADOS_DINOV2_FALLBACK_DHASH = _leer_env_bool(
    'DUPLICADOS_DINOV2_FALLBACK_DHASH',
    default=True,
)
DUPLICADOS_DINOV2_BACKEND = (
    os.environ.get('DUPLICADOS_DINOV2_BACKEND', 'microservicio').strip().lower()
    or 'microservicio'
)
DUPLICADOS_DINOV2_MICROSERVICIO_URL = os.environ.get(
    'DUPLICADOS_DINOV2_MICROSERVICIO_URL',
    'http://127.0.0.1:8001',
).strip()
DUPLICADOS_DINOV2_MICROSERVICIO_SECRET = os.environ.get(
    'DUPLICADOS_DINOV2_MICROSERVICIO_SECRET',
    '',
).strip()
DUPLICADOS_DINOV2_MICROSERVICIO_TIMEOUT_SEGUNDOS = _leer_env_float_rango(
    'DUPLICADOS_DINOV2_MICROSERVICIO_TIMEOUT_SEGUNDOS',
    default=8.0,
    min_val=0.5,
    max_val=30.0,
)


# ─── Tiempo real: WebSockets (servicio vetericano-ws) y FCM ─────────────────────────────
# Todo apagado por defecto. El web publica eventos en Redis solo si WS_PUBLICAR_EVENTOS=True
# y existe CHANNEL_REDIS_URL. Se usa un nombre distinto de REDIS_URL a propósito: REDIS_URL
# cambia CACHES (OTP y throttles) y eso es una decisión aparte.
CHANNEL_REDIS_URL = os.environ.get('CHANNEL_REDIS_URL', '').strip()
if CHANNEL_REDIS_URL:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [{
                    'address': CHANNEL_REDIS_URL,
                    # Hallazgo T-01: redis-py 8.x usa socket_timeout=5 s por defecto, igual al BZPOPMIN de
                    # 5 s de channels-redis 4.3 -> el consumer del servicio ws caía al estar inactivo.
                    # Debe ser MAYOR que brpop_timeout. El web acota cada publicación a 3 s con
                    # asyncio.wait_for (realtime/publicar.py), así que no depende de este valor.
                    'socket_connect_timeout': 2,
                    'socket_timeout': 15,
                    'socket_keepalive': True,
                }],
                'expiry': 60,
                'group_expiry': 86400,
                'capacity': 200,
            },
        },
    }
else:
    CHANNEL_LAYERS = {}

WS_PUBLICAR_EVENTOS = _leer_env_bool('WS_PUBLICAR_EVENTOS', default=False)
# Persistencia de NP fuera de peticiones (tabla notificaciones_sistema, migración realtime).
# Apagada hasta aplicar la migración en la BD: así el código puede desplegarse en oscuro.
NOTIFICACIONES_SISTEMA_HABILITADAS = _leer_env_bool('NOTIFICACIONES_SISTEMA_HABILITADAS', default=False)
WS_ALLOWED_ORIGINS = [
    o.strip().rstrip('/')
    for o in os.environ.get('WS_ALLOWED_ORIGINS', 'http://localhost:4200,http://127.0.0.1:4200').split(',')
    if o.strip()
]
# Identificadores explícitos de clientes nativos (sin header Origin de navegador; nunca comodines '*').
WS_ALLOWED_NATIVE_CLIENTS = [
    c.strip().lower()
    for c in os.environ.get('WS_ALLOWED_NATIVE_CLIENTS', 'android-petvetericano').split(',')
    if c.strip() and c.strip() != '*'
]
WS_AUTH_TIMEOUT_SEGUNDOS = 5
WS_MAX_SIN_AUTENTICAR = int(os.environ.get('WS_MAX_SIN_AUTENTICAR', '200'))
WS_MAX_MENSAJE_BYTES = 16384
WS_RECHEQUEO_SEGUNDOS = 300

FCM_HABILITADO = _leer_env_bool('FCM_HABILITADO', default=False)
FCM_PROJECT_ID = os.environ.get('FCM_PROJECT_ID', '').strip()
FCM_CREDENTIALS_JSON = os.environ.get('FCM_CREDENTIALS_JSON', '').strip()
FCM_CREDENTIALS_FILE = os.environ.get('FCM_CREDENTIALS_FILE', '').strip()


# ─── Google OAuth (Inicio de sesión móvil/web con ID Token) ───────────────────────────────
def _leer_env_lista_csv(nombre_var: str, fallback_var: str = '') -> tuple[str, ...]:
    raw = os.environ.get(nombre_var, '').strip()
    if not raw and fallback_var:
        raw = os.environ.get(fallback_var, '').strip()
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(',') if item.strip())


def _leer_env_roles_csv(nombre_var: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.environ.get(nombre_var)
    if raw is None or not str(raw).strip():
        return default
    roles = []
    for item in str(raw).split(','):
        item = item.strip()
        if not item:
            continue
        try:
            roles.append(int(item))
        except ValueError:
            continue
    return tuple(dict.fromkeys(roles))


_DEFAULT_GOOGLE_OAUTH_CLIENT_IDS = (
    '414064158617-fdj5ffqrrl19i56d51ioeb5g2k8ieak6.apps.googleusercontent.com',
    '414064158617-vqebf0gb77dtuc27jrq40s6r9ochdbef.apps.googleusercontent.com',
)
GOOGLE_OAUTH_CLIENT_IDS = (
    _leer_env_lista_csv(
        'GOOGLE_OAUTH_CLIENT_IDS',
        fallback_var='GOOGLE_CLIENT_ID',
    )
    or _DEFAULT_GOOGLE_OAUTH_CLIENT_IDS
)
# Por defecto solo vincula automáticamente roles móviles (Veterinario=3, Peticionario=4);
# Administrador (1), Jurídico (2) y Super Administrador (5) requieren login tradicional.
GOOGLE_OAUTH_ALLOWED_LINK_ROLES = _leer_env_roles_csv(
    'GOOGLE_OAUTH_ALLOWED_LINK_ROLES',
    default=(3, 4),
)
GOOGLE_OAUTH_ALLOWED_HOSTED_DOMAINS = tuple(
    d.lower() for d in _leer_env_lista_csv('GOOGLE_OAUTH_ALLOWED_HOSTED_DOMAINS')
)


