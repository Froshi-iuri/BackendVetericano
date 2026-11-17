"""
Sistema centralizado de notificaciones en tiempo real de Vetericano.

- El servicio web (gunicorn WSGI) persiste las notificaciones y, después del COMMIT,
  publica los eventos en Redis (channel layer) y envía FCM. Todo best-effort.
- El servicio `vetericano-ws` (ASGI, vetericano/asgi_ws.py) solo mantiene los sockets.
- Contrato: _plan_websockets/contrato/CONTRATO_NOTIFICACIONES_V1.md
"""
