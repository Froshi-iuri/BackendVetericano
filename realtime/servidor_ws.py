"""
Punto de arranque del servicio `vetericano-ws` con sus límites versionados en el repo
(auditoría M4: no dependen de lo que se escriba en el start command de Railway).

Railway (servicio vetericano-ws): Custom Start Command = `python -m realtime.servidor_ws`
Local: `PORT=8002 python -m realtime.servidor_ws`
"""
import os

import uvicorn

WS_MAX_SIZE_BYTES = 16384      # coincide con settings.WS_MAX_MENSAJE_BYTES
WS_PING_INTERVAL_SEGUNDOS = 20
WS_PING_TIMEOUT_SEGUNDOS = 20


def opciones():
    return {
        'host': os.environ.get('WS_HOST', '0.0.0.0'),
        'port': int(os.environ.get('PORT', '8002')),
        'proxy_headers': True,
        'forwarded_allow_ips': os.environ.get('FORWARDED_ALLOW_IPS', '*'),
        'ws_max_size': WS_MAX_SIZE_BYTES,
        'ws_ping_interval': WS_PING_INTERVAL_SEGUNDOS,
        'ws_ping_timeout': WS_PING_TIMEOUT_SEGUNDOS,
        'log_level': os.environ.get('UVICORN_LOG_LEVEL', 'info'),
        'access_log': False,  # el consumer registra lo necesario sin tokens
    }


def main():
    uvicorn.run('vetericano.asgi_ws:application', **opciones())


if __name__ == '__main__':
    main()
