"""
Autenticación del WebSocket por PRIMER MENSAJE (contrato §2). El token nunca viaja en la URL.

- Firma, `exp` y tipo `access` se validan con simplejwt (misma SECRET_KEY y SIMPLE_JWT que el web).
- El usuario y su rol se leen SIEMPRE de la BD (`users.Usuarios`, activo=True); nunca del token.
"""
from channels.db import database_sync_to_async
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken


def validar_access_token(token):
    """Devuelve {'user_id': int, 'exp': int} o None si el token no es un access válido."""
    if not isinstance(token, str) or not token or len(token) > 4096:
        return None
    try:
        validado = AccessToken(token)
        user_id = validado[api_settings.USER_ID_CLAIM]
        exp = int(validado['exp'])
        return {'user_id': int(user_id), 'exp': exp}
    except (TokenError, KeyError, TypeError, ValueError):
        return None


def _cargar_usuario_activo_sync(user_id):
    from users.models import Usuarios

    fila = (
        Usuarios.objects.filter(id_usuario=user_id, activo=True)
        .values_list('id_usuario', 'id_rol_id')
        .first()
    )
    if not fila:
        return None
    return {'id_usuario': fila[0], 'id_rol': fila[1]}


cargar_usuario_activo = database_sync_to_async(_cargar_usuario_activo_sync)
