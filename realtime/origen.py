"""
Validación EXPLÍCITA del handshake WebSocket:
1. Navegadores (con encabezado Origin): lista blanca exacta contra WS_ALLOWED_ORIGINS (sin comodines).
   Protege contra Cross-Site WebSocket Hijacking (CSWSH).
2. Clientes nativos (sin encabezado Origin): ruta separada y explícita mediante encabezado
   X-Vetericano-Client en la lista blanca WS_ALLOWED_NATIVE_CLIENTS (sin comodines).
   Toda conexión aceptada sigue exigiendo autenticación JWT obligatoria en el primer frame.
"""
from urllib.parse import urlsplit

from django.conf import settings

HEADER_CLIENTE_NATIVO = b'x-vetericano-client'


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


def normalizar_cliente_nativo(valor):
    if not valor:
        return None
    limpio = valor.strip().lower()
    if not limpio or limpio == '*':
        return None
    return limpio


def clientes_nativos_permitidos():
    return {
        c for c in (
            normalizar_cliente_nativo(x)
            for x in getattr(settings, 'WS_ALLOWED_NATIVE_CLIENTS', [])
        ) if c
    }


class ValidadorOrigenExplicito:
    """
    Middleware ASGI:
    - Si llega Origin, exige coincidencia exacta en WS_ALLOWED_ORIGINS (nunca se omite).
    - Si no llega Origin, exige X-Vetericano-Client en WS_ALLOWED_NATIVE_CLIENTS.
    En cualquier otro caso rechaza el handshake con código 4403.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'websocket':
            raw_origin = None
            raw_cliente = None
            for nombre, valor in scope.get('headers', []):
                if nombre == b'origin':
                    raw_origin = valor.decode('latin1')
                elif nombre == HEADER_CLIENTE_NATIVO:
                    raw_cliente = valor.decode('latin1')

            if raw_origin is not None:
                origen = normalizar_origen(raw_origin)
                permitido = origen is not None and origen in origenes_permitidos()
            else:
                cliente = normalizar_cliente_nativo(raw_cliente)
                permitido = cliente is not None and cliente in clientes_nativos_permitidos()

            if not permitido:
                await receive()  # websocket.connect
                await send({'type': 'websocket.close', 'code': 4403})
                return
        return await self.app(scope, receive, send)
