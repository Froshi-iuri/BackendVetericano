"""
ASGI del servicio independiente `vetericano-ws` (Railway). NO reemplaza al web WSGI.

- websocket /ws/notificaciones/ → ValidadorOrigenExplicito → NotificacionesConsumer
- http /health/ → 200; cualquier otra ruta HTTP → 404 (este dominio no expone la API REST)

Arranque: uvicorn vetericano.asgi_ws:application --host 0.0.0.0 --port $PORT --proxy-headers
          --forwarded-allow-ips=* --ws-max-size 16384 --ws-ping-interval 20 --ws-ping-timeout 20
"""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vetericano.settings')
django.setup(set_prefix=False)

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.urls import path  # noqa: E402

from realtime.consumers import NotificacionesConsumer  # noqa: E402
from realtime.origen import ValidadorOrigenExplicito  # noqa: E402


async def http_minimo(scope, receive, send):
    """Solo /health/. Todo lo demás 404, sin tocar Django ni la BD."""
    if scope['type'] == 'lifespan':
        while True:
            mensaje = await receive()
            if mensaje['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif mensaje['type'] == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'})
                return
    ruta = scope.get('path', '')
    estado, cuerpo = (200, b'{"status":"ok"}') if ruta in ('/health', '/health/') else (404, b'{"detail":"Not found"}')
    await send({
        'type': 'http.response.start',
        'status': estado,
        'headers': [(b'content-type', b'application/json'), (b'cache-control', b'no-store')],
    })
    await send({'type': 'http.response.body', 'body': cuerpo})


websocket_urlpatterns = [
    path('ws/notificaciones/', NotificacionesConsumer.as_asgi()),
]

application = ProtocolTypeRouter({
    'http': http_minimo,
    'lifespan': http_minimo,
    'websocket': ValidadorOrigenExplicito(URLRouter(websocket_urlpatterns)),
})
