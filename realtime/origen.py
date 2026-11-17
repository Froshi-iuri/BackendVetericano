"""
Validación EXPLÍCITA del encabezado Origin del handshake WebSocket (lista blanca exacta).

CORS no aplica a WebSocket: esta es la protección contra Cross-Site WebSocket Hijacking.
Compara esquema + host + puerto exactos con WS_ALLOWED_ORIGINS (sin comodines). Sin Origin → rechazo.
"""
from urllib.parse import urlsplit

from django.conf import settings


def normalizar_origen(valor):
    if not valor:
        return None
    try:
        partes = urlsplit(valor.strip())
    except ValueError:
        return None
    if partes.scheme not in ('http', 'https') or not partes.hostname:
        return None
    puerto = partes.port
    por_defecto = 443 if partes.scheme == 'https' else 80
    host = partes.hostname.lower()
    return f'{partes.scheme}://{host}' if puerto in (None, por_defecto) else f'{partes.scheme}://{host}:{puerto}'


def origenes_permitidos():
    return {o for o in (normalizar_origen(x) for x in getattr(settings, 'WS_ALLOWED_ORIGINS', [])) if o}


class ValidadorOrigenExplicito:
    """Middleware ASGI: rechaza el handshake (403) si el Origin no está en la lista blanca."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'websocket':
            origen = None
            for nombre, valor in scope.get('headers', []):
                if nombre == b'origin':
                    origen = normalizar_origen(valor.decode('latin1'))
                    break
            if origen is None or origen not in origenes_permitidos():
                await receive()  # websocket.connect
                await send({'type': 'websocket.close', 'code': 4403})
                return
        return await self.app(scope, receive, send)
