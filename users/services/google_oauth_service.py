"""
Servicio de autenticación con Google Sign-In mediante verificación criptográfica de ID Tokens.
"""
from dataclasses import dataclass
import logging
import re
import time
from typing import Optional

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jwt.exceptions import PyJWTError
import requests

from users.constants import ROL_PETICIONARIO, ROL_VETERINARIO
from users.models import Rol, Usuarios

logger = logging.getLogger(__name__)

_GOOGLE_ISSUERS = frozenset({'accounts.google.com', 'https://accounts.google.com'})
_JWT_SEGMENT_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_MAX_ID_TOKEN_LENGTH = 8192
_MAX_IAT_FUTURE_SKEW_SECONDS = 300
_DEFAULT_ALLOWED_LINK_ROLES = (ROL_VETERINARIO, ROL_PETICIONARIO)

_transport_request: Optional[google_requests.Request] = None


class GoogleAuthConfigError(Exception):
    """El servidor no tiene configurados los Client IDs autorizados de Google."""


class GoogleAuthUnavailableError(Exception):
    """No fue posible consultar las llaves públicas de Google para verificar el token."""


class GoogleTokenInvalidError(Exception):
    """El ID Token de Google es inválido, expiró, tiene audiencia/emisor incorrecto o correo sin verificar."""


class GoogleAccountPolicyError(Exception):
    """La cuenta existente está inactiva o su rol no admite vinculación automática por Google."""


@dataclass(frozen=True)
class GoogleIdentity:
    """Identidad autenticada extraída exclusivamente de un ID Token verificado de Google."""
    sub: str
    email: str
    email_verified: bool
    nombre: str
    apellido: str
    foto_perfil: Optional[str]
    hosted_domain: str = ''


def _get_transport_request() -> google_requests.Request:
    global _transport_request
    if _transport_request is None:
        _transport_request = google_requests.Request()
    return _transport_request


def es_formato_google_id_token_valido(raw_token: str) -> bool:
    """Verifica que el token tenga estructura de JWT compacto de 3 segmentos y no sea un marcador local."""
    if not isinstance(raw_token, str):
        return False
    token = raw_token.strip()
    if not token or len(token) > _MAX_ID_TOKEN_LENGTH:
        return False
    if token.lower().startswith('android_account:'):
        return False
    if any(ch.isspace() for ch in token):
        return False
    partes = token.split('.')
    if len(partes) != 3:
        return False
    return all(bool(p) and bool(_JWT_SEGMENT_RE.match(p)) for p in partes)


def _limpiar_texto(valor: Optional[str], *, max_len: int) -> str:
    if not valor:
        return ''
    limpio = re.sub(r'\s+', ' ', str(valor)).strip()
    return limpio[:max_len]


def _nombre_desde_email(email: str) -> str:
    local = (email or '').split('@', 1)[0]
    limpio = re.sub(r'[^A-Za-zÀ-ÿ]+', ' ', local).strip()
    if limpio:
        return limpio[:150]
    return 'Usuario'


def _extraer_url_foto_segura(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url or not isinstance(raw_url, str):
        return None
    limpia = raw_url.strip()
    if limpia.startswith('https://') and len(limpia) <= 1000:
        return limpia
    return None


def _extraer_nombre_apellido_google(idinfo: dict, email: str) -> tuple[str, str]:
    given_name = _limpiar_texto(idinfo.get('given_name'), max_len=150)
    family_name = _limpiar_texto(idinfo.get('family_name'), max_len=100)
    if given_name:
        return given_name, family_name

    full_name = _limpiar_texto(idinfo.get('name'), max_len=150)
    if full_name:
        partes = full_name.split(' ', 1)
        nombre = partes[0][:150]
        apellido = family_name or (partes[1][:100] if len(partes) > 1 else '')
        return nombre, apellido

    return _nombre_desde_email(email), family_name


def _audiencia_coincide(aud_claim, allowed_ids: tuple[str, ...]) -> bool:
    if isinstance(aud_claim, str):
        return aud_claim.strip() in allowed_ids
    if isinstance(aud_claim, (list, tuple)):
        return any(isinstance(item, str) and item.strip() in allowed_ids for item in aud_claim)
    return False


def verificar_google_id_token(
    raw_token: str,
    *,
    client_ids: Optional[tuple[str, ...] | list[str]] = None,
    request_transport=None,
) -> GoogleIdentity:
    """
    Valida criptográficamente un Google ID Token (firma, emisor, audiencia, vigencia y correo verificado).
    """
    allowed_raw = client_ids if client_ids is not None else getattr(settings, 'GOOGLE_OAUTH_CLIENT_IDS', ())
    allowed_ids = tuple(str(cid).strip() for cid in (allowed_raw or ()) if str(cid).strip())
    if not allowed_ids:
        raise GoogleAuthConfigError(
            "El inicio de sesión con Google no está configurado en el servidor."
        )

    if not es_formato_google_id_token_valido(raw_token):
        raise GoogleTokenInvalidError(
            "Token de Google inválido. Debe enviar un ID Token auténtico emitido por Google."
        )

    token = raw_token.strip()
    transport = request_transport if request_transport is not None else _get_transport_request()
    audience_arg = allowed_ids[0] if len(allowed_ids) == 1 else list(allowed_ids)

    try:
        idinfo = google_id_token.verify_oauth2_token(
            token,
            transport,
            audience=audience_arg,
        )
    except (google_exceptions.TransportError, requests.RequestException) as exc:
        logger.warning("Fallo de red verificando ID Token con Google: %s", exc.__class__.__name__)
        raise GoogleAuthUnavailableError(
            "No se pudo verificar la identidad con Google en este momento. Intenta nuevamente."
        ) from exc
    except (ValueError, TypeError, KeyError, google_exceptions.GoogleAuthError, PyJWTError) as exc:
        logger.info("Rechazado ID Token de Google inválido: %s", exc.__class__.__name__)
        raise GoogleTokenInvalidError(
            "El token de Google no es válido o ha expirado."
        ) from exc

    if not isinstance(idinfo, dict):
        raise GoogleTokenInvalidError("El contenido del token de Google no es válido.")

    iss = str(idinfo.get('iss') or '').strip()
    if iss not in _GOOGLE_ISSUERS:
        raise GoogleTokenInvalidError("El emisor del token no corresponde a Google.")

    if not _audiencia_coincide(idinfo.get('aud'), allowed_ids):
        raise GoogleTokenInvalidError("La audiencia del token de Google no está autorizada.")

    ahora = time.time()
    exp = idinfo.get('exp')
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or float(exp) <= ahora:
        raise GoogleTokenInvalidError("El token de Google ha expirado.")

    iat = idinfo.get('iat')
    if iat is not None:
        if isinstance(iat, bool) or not isinstance(iat, (int, float)) or float(iat) > ahora + _MAX_IAT_FUTURE_SKEW_SECONDS:
            raise GoogleTokenInvalidError("La marca de emisión del token de Google no es válida.")

    sub = str(idinfo.get('sub') or '').strip()
    if not sub:
        raise GoogleTokenInvalidError("El token de Google no contiene el identificador de cuenta.")

    email = str(idinfo.get('email') or '').strip().lower()
    if not email or '@' not in email or len(email) > 150:
        raise GoogleTokenInvalidError("El token de Google no contiene un correo electrónico válido.")

    if idinfo.get('email_verified') is not True:
        raise GoogleTokenInvalidError("El correo de la cuenta de Google no está verificado.")

    hosted_domain = str(idinfo.get('hd') or '').strip().lower()
    allowed_domains = tuple(
        str(d).strip().lower()
        for d in getattr(settings, 'GOOGLE_OAUTH_ALLOWED_HOSTED_DOMAINS', ())
        if str(d).strip()
    )
    if allowed_domains and hosted_domain not in allowed_domains:
        raise GoogleTokenInvalidError("El dominio de la cuenta de Google no está autorizado.")

    nombre, apellido = _extraer_nombre_apellido_google(idinfo, email)
    foto_perfil = _extraer_url_foto_segura(idinfo.get('picture'))

    return GoogleIdentity(
        sub=sub,
        email=email,
        email_verified=True,
        nombre=nombre,
        apellido=apellido,
        foto_perfil=foto_perfil,
        hosted_domain=hosted_domain,
    )


def _buscar_candidatos_por_email(email: str) -> list[Usuarios]:
    return list(
        Usuarios.objects.select_related('id_rol')
        .filter(email__iexact=email)
        .order_by('-activo', 'id_usuario')
    )


def _validar_y_vincular_usuario_existente(
    candidatos: list[Usuarios],
    identidad: GoogleIdentity,
) -> Usuarios:
    usuario_activo = next((u for u in candidatos if u.activo), None)
    if usuario_activo is None:
        raise GoogleAccountPolicyError("Esta cuenta ha sido desactivada.")

    rol_obj = getattr(usuario_activo, 'id_rol', None)
    rol_id = getattr(rol_obj, 'id_rol', getattr(usuario_activo, 'id_rol_id', None))
    allowed_roles = tuple(
        getattr(settings, 'GOOGLE_OAUTH_ALLOWED_LINK_ROLES', _DEFAULT_ALLOWED_LINK_ROLES)
    )
    if rol_id not in allowed_roles:
        raise GoogleAccountPolicyError(
            "Por política de seguridad, este rol debe iniciar sesión con correo y contraseña."
        )

    campos_actualizar = []
    if not (usuario_activo.nombre or '').strip() and identidad.nombre:
        usuario_activo.nombre = identidad.nombre[:150]
        campos_actualizar.append('nombre')
    if not (usuario_activo.apellido or '').strip() and identidad.apellido:
        usuario_activo.apellido = identidad.apellido[:100]
        campos_actualizar.append('apellido')
    if not (getattr(usuario_activo, 'foto_perfil', None) or '').strip() and identidad.foto_perfil:
        usuario_activo.foto_perfil = identidad.foto_perfil
        campos_actualizar.append('foto_perfil')

    if campos_actualizar:
        usuario_activo.save(update_fields=campos_actualizar)

    return usuario_activo


def _obtener_rol_peticionario() -> Rol:
    rol = (
        Rol.objects.filter(id_rol=ROL_PETICIONARIO).first()
        or Rol.objects.filter(nombre_rol__iexact='Peticionario').first()
    )
    if rol is not None:
        return rol
    rol, _ = Rol.objects.get_or_create(
        id_rol=ROL_PETICIONARIO,
        defaults={'nombre_rol': 'Peticionario'},
    )
    return rol


def resolver_o_crear_usuario_google(
    identidad: GoogleIdentity,
    *,
    nombre_fallback: Optional[str] = None,
    apellido_fallback: Optional[str] = None,
) -> tuple[Usuarios, bool]:
    """
    Vincula una cuenta existente que cumpla la política de seguridad o registra un nuevo Peticionario
    con contraseña criptográficamente no usable (sin contraseñas predecibles).
    """
    candidatos = _buscar_candidatos_por_email(identidad.email)
    if candidatos:
        usuario = _validar_y_vincular_usuario_existente(candidatos, identidad)
        return usuario, False

    nombre_final = (
        identidad.nombre
        or _limpiar_texto(nombre_fallback, max_len=150)
        or _nombre_desde_email(identidad.email)
    )
    apellido_final = (
        identidad.apellido
        or _limpiar_texto(apellido_fallback, max_len=100)
    )

    try:
        with transaction.atomic():
            rol_peticionario = _obtener_rol_peticionario()
            nuevo_usuario = Usuarios.objects.create(
                email=identidad.email,
                password=make_password(None),
                nombre=nombre_final[:150],
                apellido=apellido_final[:100],
                id_rol=rol_peticionario,
                activo=True,
                identificacion=None,
                telefono=None,
                foto_perfil=identidad.foto_perfil,
            )
            return nuevo_usuario, True
    except IntegrityError:
        candidatos_concurrentes = _buscar_candidatos_por_email(identidad.email)
        if candidatos_concurrentes:
            usuario = _validar_y_vincular_usuario_existente(candidatos_concurrentes, identidad)
            return usuario, False
        raise
