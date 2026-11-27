# AnaC
# Recuperación de contraseña. La lógica (códigos de un solo uso, límites de intentos y de
# solicitudes, almacenamiento compartido entre workers) vive en users/services/recuperacion_service.py.
# Contrato con Angular y Android: mismas URLs y mismos campos de entrada; respuestas con
# {'mensaje': ...} en éxito y {'error': ...} en error (400, 429 o 503).

import re
import secrets
from django.core.cache import cache
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .services import recuperacion_service

# Constantes de seguridad para verificación de correo en registro y OTP (10 minutos)
OTP_TIMEOUT_SEGUNDOS = 600          # 10 minutos de vigencia del código
OTP_COOLDOWN_SEGUNDOS = 60          # 60 segundos entre reenvíos al mismo correo
OTP_MAX_SOLICITUDES_HORA = 3        # Máximo 3 solicitudes de código por hora por correo
OTP_MAX_INTENTOS_FALLIDOS = 5       # Máximo 5 intentos al digitar el código de 6 dígitos


def generar_codigo_otp() -> str:
    """Genera un código OTP de 6 dígitos criptográficamente seguro."""
    return f"{secrets.randbelow(900000) + 100000:06d}"


def validar_complejidad_password(password: str) -> str | None:
    """
    Valida que la contraseña cumpla con las reglas de seguridad de Vetericano:
    mínimo 8 caracteres, al menos una letra mayúscula y al menos un número.
    Retorna un mensaje de error si falla, o None si es válida.
    """
    if len(password) < 8:
        return "La contraseña debe tener al menos 8 caracteres."
    if not re.search(r'[A-Z]', password):
        return "La contraseña debe incluir al menos una letra mayúscula."
    if not re.search(r'[0-9]', password):
        return "La contraseña debe incluir al menos un número."
    return None


def _datos(request):
    datos = request.data
    return datos if hasattr(datos, 'get') else {}


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def solicitar_recuperacion(request):
    resultado = recuperacion_service.solicitar_codigo(
        _datos(request).get('email'),
        ip=recuperacion_service.ip_cliente(request),
    )
    return Response(resultado.cuerpo, status=resultado.estado_http)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def confirmar_recuperacion(request):
    datos = _datos(request)
    email_raw = datos.get('email')
    resultado = recuperacion_service.confirmar_codigo(
        email_raw,
        datos.get('codigo'),
        datos.get('nueva_password'),
        ip=recuperacion_service.ip_cliente(request),
    )
    if resultado.estado_http == 200 and isinstance(email_raw, str) and email_raw.strip():
        email_key = email_raw.strip().lower()
        cache.delete(f'login_lock_{email_key}')
        cache.delete(f'login_fail_{email_key}')
    return Response(resultado.cuerpo, status=resultado.estado_http)
