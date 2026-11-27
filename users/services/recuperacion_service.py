"""
Recuperación de contraseña con código de un solo uso (6 dígitos).

Reglas de seguridad:
- Código generado con `secrets` (CSPRNG), válido 15 minutos, ligado a UNA cuenta (id_usuario)
  y guardado como HMAC-SHA256 (nunca en texto plano).
- Uso único: el código se consume con un DELETE atómico; solo una confirmación puede ganarlo.
  Cada solicitud nueva reemplaza al código anterior; una recuperación exitosa lo consume.
  (Un cambio de contraseña por otra vía, p. ej. el perfil, no invalida un código pendiente.)
- Máximo de intentos por correo, reservados ANTES de evaluar el código (atómico, también bajo
  concurrencia); al agotarlos se invalida el código y se bloquea temporalmente la confirmación
  y la emisión de nuevos códigos para ese correo.
- Límites de solicitudes por correo (15 min y diario) y por IP, y espera mínima entre códigos.
- Respuestas que no revelan si un correo está registrado; logs sin correos ni códigos.

Almacenamiento: alias de caché 'recuperacion' (ver settings). En Railway debe ser Redis compartido
entre los workers de gunicorn; si no lo es, el servicio responde 503 en lugar de fallar al azar.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.cache import caches
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from rest_framework import serializers, status

from users.models import Usuarios
from users.services.email_service import enviar_correo_transaccional_brevo

logger = logging.getLogger('users.recuperacion')

ALIAS_CACHE = 'recuperacion'

CODIGO_DIGITOS = 6
CODIGO_TTL_SEGUNDOS = 15 * 60
ESPERA_ENTRE_CODIGOS_SEGUNDOS = 60

MAX_INTENTOS_FALLIDOS = 5
VENTANA_INTENTOS_SEGUNDOS = 15 * 60
BLOQUEO_SEGUNDOS = 15 * 60

MAX_SOLICITUDES_POR_CORREO = 3
VENTANA_SOLICITUDES_CORREO_SEGUNDOS = 15 * 60
# Tope diario: acota la fuerza bruta sostenida a 6 códigos x 5 intentos = 30 conjeturas/día/correo.
MAX_SOLICITUDES_POR_CORREO_DIA = 6
VENTANA_SOLICITUDES_CORREO_DIA_SEGUNDOS = 24 * 60 * 60
MAX_SOLICITUDES_POR_IP = 10
VENTANA_SOLICITUDES_IP_SEGUNDOS = 60 * 60
MAX_CONFIRMACIONES_POR_IP = 30
VENTANA_CONFIRMACIONES_IP_SEGUNDOS = 15 * 60

MENSAJE_SOLICITUD = (
    'Si el correo está registrado, recibirás un código de recuperación válido por 15 minutos.'
)
MENSAJE_CONFIRMACION = 'Contraseña actualizada exitosamente.'
ERROR_EMAIL_REQUERIDO = 'Por favor ingresa un correo electrónico.'
ERROR_EMAIL_INVALIDO = 'Ingresa un correo electrónico válido.'
ERROR_CAMPOS_REQUERIDOS = 'Todos los campos son obligatorios.'
ERROR_CODIGO_INVALIDO = 'El código de recuperación es incorrecto o ha expirado.'
ERROR_BLOQUEADO = (
    'Demasiados intentos fallidos. Por seguridad, espera 15 minutos y solicita un nuevo código.'
)
ERROR_DEMASIADAS_SOLICITUDES = 'Demasiadas solicitudes de recuperación. Intenta de nuevo más tarde.'
ERROR_NO_DISPONIBLE = (
    'El servicio de recuperación de contraseña no está disponible en este momento. Intenta más tarde.'
)


@dataclass(frozen=True)
class Resultado:
    estado_http: int
    cuerpo: dict


class AlmacenamientoNoDisponible(Exception):
    """El almacenamiento de códigos no es seguro (no compartido) o no responde."""


# ─── Utilidades internas ──────────────────────────────────────────────────────────────────


def _cache():
    if getattr(settings, 'RECUPERACION_EXIGIR_CACHE_COMPARTIDA', False) and not getattr(
        settings, 'RECUPERACION_CACHE_COMPARTIDA', False
    ):
        raise AlmacenamientoNoDisponible('Sin almacenamiento compartido para recuperación')
    return caches[ALIAS_CACHE]


def _texto(valor) -> str:
    """Convierte un valor del cuerpo JSON a texto sin romper con números u otros tipos."""
    if valor is None:
        return ''
    if isinstance(valor, (str, int)) and not isinstance(valor, bool):
        return str(valor)
    return ''


def normalizar_email(email) -> str:
    return _texto(email).strip().lower()


def _huella(*partes) -> str:
    """HMAC con la SECRET_KEY: identificadores en caché sin exponer correos ni IPs."""
    mensaje = '|'.join(str(p) for p in partes).encode('utf-8')
    return hmac.new(settings.SECRET_KEY.encode('utf-8'), mensaje, hashlib.sha256).hexdigest()


def _clave(tipo: str, *partes) -> str:
    return f'{tipo}:{_huella(tipo, *partes)}'


def _hash_codigo(id_usuario: int, codigo: str, nonce: str) -> str:
    return _huella('codigo', id_usuario, codigo, nonce)


def _generar_codigo() -> str:
    return f'{secrets.randbelow(10 ** CODIGO_DIGITOS):0{CODIGO_DIGITOS}d}'


def _incrementar(cache, clave: str, ventana: int) -> int:
    """Contador atómico con ventana fija (Redis INCR / LocMem con lock)."""
    cache.add(clave, 0, ventana)
    try:
        valor = cache.incr(clave)
    except ValueError:  # expiró entre add() e incr(): se reinicia la ventana
        if cache.add(clave, 1, ventana):
            return 1
        try:
            valor = cache.incr(clave)
        except ValueError:
            cache.set(clave, 1, ventana)
            return 1
    if valor == 1:
        # Primer incremento de la ventana: fija la expiración aunque el contador se haya
        # recreado entre las dos operaciones (evita contadores sin TTL).
        cache.touch(clave, ventana)
    return valor


def ip_cliente(request) -> str:
    """
    IP del cliente detrás del proxy de Railway: X-Real-IP (fijada por el edge de Railway);
    si no existe, REMOTE_ADDR. No se usa la primera entrada de X-Forwarded-For porque el
    cliente puede falsificarla.
    """
    meta = getattr(request, 'META', {}) or {}
    ip = (meta.get('HTTP_X_REAL_IP') or meta.get('REMOTE_ADDR') or '').strip()
    return ip[:64] or 'desconocida'


def _cuenta_para_recuperar(email_escrito: str):
    """
    Una sola cuenta activa por correo. Si existen varias que solo difieren en mayúsculas,
    se prefiere la que coincide exactamente con lo escrito y después la de menor id.
    El código queda ligado a esa cuenta: nunca se modifican otras.
    """
    candidatos = list(
        Usuarios.objects.filter(email__iexact=email_escrito, activo=True).order_by('id_usuario')
    )
    if not candidatos:
        return None
    exactos = [u for u in candidatos if u.email == email_escrito]
    return exactos[0] if exactos else candidatos[0]


def _email_valido(email_normalizado: str) -> bool:
    """Formato de correo válido y codificable (evita 503 por entradas malformadas)."""
    try:
        email_normalizado.encode('utf-8')
        validate_email(email_normalizado)
    except (UnicodeEncodeError, DjangoValidationError):
        return False
    return len(email_normalizado) <= 254


def _bloquear(cache, email_normalizado: str, clave_codigo: str, clave_intentos: str) -> None:
    """Invalida el código pendiente y bloquea temporalmente el correo."""
    cache.delete(clave_codigo)
    cache.set(_clave('bloqueo', email_normalizado), 1, BLOQUEO_SEGUNDOS)
    cache.delete(clave_intentos)
    logger.warning('Recuperación: correo bloqueado temporalmente por intentos fallidos')


def _validar_politica_password(password: str) -> str | None:
    """Misma regla que el registro (RegisterCustomSerializer): 8+ caracteres y una mayúscula."""
    from users.api.serializers import RegisterCustomSerializer

    try:
        RegisterCustomSerializer().validate_password(password)
    except serializers.ValidationError as exc:
        detalle = exc.detail
        if isinstance(detalle, (list, tuple)) and detalle:
            return str(detalle[0])
        return str(detalle)
    if not password.strip():
        return 'La contraseña no puede estar vacía.'
    return None


def _enviar_codigo(usuario, codigo: str) -> bool:
    html = (
        '<html><body>'
        '<p>Hola,</p>'
        f'<p>Tu código de recuperación es: <strong>{codigo}</strong></p>'
        '<p>Este código es válido por 15 minutos y solo puede usarse una vez.</p>'
        '<p>Si no solicitaste este cambio, ignora este mensaje.</p>'
        '</body></html>'
    )
    try:
        resultado = enviar_correo_transaccional_brevo(
            destinatario_email=usuario.email,
            destinatario_nombre=usuario.nombre or 'Usuario',
            asunto='Código de recuperación de contraseña',
            contenido_html=html,
        )
    except Exception as exc:  # pragma: no cover - el servicio ya captura sus excepciones
        logger.error('Recuperación: excepción enviando correo a usuario id=%s: %s',
                     usuario.id_usuario, type(exc).__name__)
        return False
    if not resultado.get('exito'):
        logger.error('Recuperación: el proveedor de correo rechazó el envío a usuario id=%s',
                     usuario.id_usuario)
        return False
    return True


def _actualizar_password(id_usuario: int, password: str) -> bool:
    """Actualiza solo la cuenta ligada al código y solo si sigue activa."""
    actualizadas = Usuarios.objects.filter(id_usuario=id_usuario, activo=True).update(
        password=make_password(password)
    )
    return actualizadas == 1


# ─── Casos de uso ─────────────────────────────────────────────────────────────────────────


def solicitar_codigo(email, ip: str) -> Resultado:
    email_n = normalizar_email(email)
    if not email_n:
        return Resultado(status.HTTP_400_BAD_REQUEST, {'error': ERROR_EMAIL_REQUERIDO})
    if not _email_valido(email_n):
        return Resultado(status.HTTP_400_BAD_REQUEST, {'error': ERROR_EMAIL_INVALIDO})

    generico = Resultado(status.HTTP_200_OK, {'mensaje': MENSAJE_SOLICITUD})
    try:
        cache = _cache()
        if _incrementar(cache, _clave('solicitudes-ip', ip), VENTANA_SOLICITUDES_IP_SEGUNDOS) > MAX_SOLICITUDES_POR_IP:
            logger.warning('Recuperación: límite de solicitudes por IP superado')
            return Resultado(status.HTTP_429_TOO_MANY_REQUESTS, {'error': ERROR_DEMASIADAS_SOLICITUDES})
        # Mismos límites para correos registrados y no registrados: no revela si existen.
        if (
            _incrementar(cache, _clave('solicitudes-correo', email_n), VENTANA_SOLICITUDES_CORREO_SEGUNDOS) > MAX_SOLICITUDES_POR_CORREO
            or _incrementar(cache, _clave('solicitudes-correo-dia', email_n), VENTANA_SOLICITUDES_CORREO_DIA_SEGUNDOS) > MAX_SOLICITUDES_POR_CORREO_DIA
        ):
            logger.warning('Recuperación: límite de solicitudes por correo superado')
            return Resultado(status.HTTP_429_TOO_MANY_REQUESTS, {'error': ERROR_DEMASIADAS_SOLICITUDES})
        if cache.get(_clave('bloqueo', email_n)):
            return generico
        # Espera mínima entre códigos (SET NX): de dos solicitudes simultáneas solo una emite.
        if not cache.add(_clave('espera', email_n), 1, ESPERA_ENTRE_CODIGOS_SEGUNDOS):
            return generico

        usuario = _cuenta_para_recuperar(_texto(email).strip())
        if usuario is None:
            logger.info('Recuperación solicitada para un correo sin cuenta activa')
            return generico

        codigo = _generar_codigo()
        nonce = secrets.token_hex(16)
        clave_codigo = _clave('codigo', email_n)
        cache.set(
            clave_codigo,
            {
                'id_usuario': usuario.id_usuario,
                'nonce': nonce,
                'hash': _hash_codigo(usuario.id_usuario, codigo, nonce),
                'emitido': int(time.time()),
            },
            CODIGO_TTL_SEGUNDOS,
        )
        if not _enviar_codigo(usuario, codigo):
            cache.delete(clave_codigo)
            cache.delete(_clave('espera', email_n))  # permite reintentar sin esperar
            return generico
        logger.info('Recuperación: código emitido para usuario id=%s', usuario.id_usuario)
        return generico
    except AlmacenamientoNoDisponible:
        logger.error('Recuperación deshabilitada: falta almacenamiento compartido (REDIS_URL o CHANNEL_REDIS_URL)')
        return Resultado(status.HTTP_503_SERVICE_UNAVAILABLE, {'error': ERROR_NO_DISPONIBLE})
    except Exception as exc:
        logger.error('Recuperación: error al solicitar (%s)', type(exc).__name__, exc_info=True)
        return Resultado(status.HTTP_503_SERVICE_UNAVAILABLE, {'error': ERROR_NO_DISPONIBLE})


def confirmar_codigo(email, codigo, nueva_password, ip: str) -> Resultado:
    email_n = normalizar_email(email)
    codigo_t = _texto(codigo).strip()
    # Se recorta igual que en el registro (CharField de DRF) y en la app Android.
    password = nueva_password.strip() if isinstance(nueva_password, str) else ''
    if not email_n or not codigo_t or not password:
        return Resultado(status.HTTP_400_BAD_REQUEST, {'error': ERROR_CAMPOS_REQUERIDOS})
    if not _email_valido(email_n):
        return Resultado(status.HTTP_400_BAD_REQUEST, {'error': ERROR_CODIGO_INVALIDO})

    try:
        cache = _cache()
        if _incrementar(cache, _clave('confirmaciones-ip', ip), VENTANA_CONFIRMACIONES_IP_SEGUNDOS) > MAX_CONFIRMACIONES_POR_IP:
            logger.warning('Recuperación: límite de confirmaciones por IP superado')
            return Resultado(status.HTTP_429_TOO_MANY_REQUESTS, {'error': ERROR_DEMASIADAS_SOLICITUDES})
        if cache.get(_clave('bloqueo', email_n)):
            return Resultado(status.HTTP_429_TOO_MANY_REQUESTS, {'error': ERROR_BLOQUEADO})

        # La política se valida antes de tocar el código: una contraseña débil no gasta intentos.
        error_politica = _validar_politica_password(password)
        if error_politica:
            return Resultado(status.HTTP_400_BAD_REQUEST, {'error': error_politica})

        # El intento se reserva ANTES de evaluar el código (contador atómico): aunque lleguen
        # muchas confirmaciones a la vez, nunca se evalúan más de MAX_INTENTOS_FALLIDOS por ventana.
        clave_intentos = _clave('intentos', email_n)
        clave_codigo = _clave('codigo', email_n)
        intento = _incrementar(cache, clave_intentos, VENTANA_INTENTOS_SEGUNDOS)
        if intento > MAX_INTENTOS_FALLIDOS:
            _bloquear(cache, email_n, clave_codigo, clave_intentos)
            return Resultado(status.HTTP_429_TOO_MANY_REQUESTS, {'error': ERROR_BLOQUEADO})

        registro = cache.get(clave_codigo)
        valido = (
            isinstance(registro, dict)
            and codigo_t.isascii()
            and codigo_t.isdigit()
            and len(codigo_t) == CODIGO_DIGITOS
            and hmac.compare_digest(
                str(registro.get('hash', '')),
                _hash_codigo(registro.get('id_usuario'), codigo_t, registro.get('nonce', '')),
            )
        )
        if not valido:
            if intento >= MAX_INTENTOS_FALLIDOS:
                _bloquear(cache, email_n, clave_codigo, clave_intentos)
                return Resultado(status.HTTP_429_TOO_MANY_REQUESTS, {'error': ERROR_BLOQUEADO})
            return Resultado(status.HTTP_400_BAD_REQUEST, {'error': ERROR_CODIGO_INVALIDO})

        # Uso único: DELETE atómico. Si dos confirmaciones llegan a la vez, solo una lo consume.
        if not cache.delete(clave_codigo):
            return Resultado(status.HTTP_400_BAD_REQUEST, {'error': ERROR_CODIGO_INVALIDO})
        id_usuario = registro['id_usuario']
        if not _actualizar_password(id_usuario, password):
            logger.warning('Recuperación: la cuenta id=%s ya no está activa', id_usuario)
            return Resultado(status.HTTP_400_BAD_REQUEST, {'error': ERROR_CODIGO_INVALIDO})
        cache.delete(clave_intentos)
        logger.info('Recuperación: contraseña actualizada para usuario id=%s', id_usuario)
        return Resultado(status.HTTP_200_OK, {'mensaje': MENSAJE_CONFIRMACION})
    except AlmacenamientoNoDisponible:
        logger.error('Recuperación deshabilitada: falta almacenamiento compartido (REDIS_URL o CHANNEL_REDIS_URL)')
        return Resultado(status.HTTP_503_SERVICE_UNAVAILABLE, {'error': ERROR_NO_DISPONIBLE})
    except Exception as exc:
        logger.error('Recuperación: error al confirmar (%s)', type(exc).__name__, exc_info=True)
        return Resultado(status.HTTP_503_SERVICE_UNAVAILABLE, {'error': ERROR_NO_DISPONIBLE})
