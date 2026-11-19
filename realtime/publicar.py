"""
Publicación best-effort en el channel layer (Redis) desde código síncrono (gunicorn).

Reglas:
- No hace nada si WS_PUBLICAR_EVENTOS=False o si no hay CHANNEL_LAYERS (sin CHANNEL_REDIS_URL).
- Nunca lanza hacia la vista: registra un warning y devuelve False.
- Timeout total de 3 s por publicación; tras un fallo, corta 30 s (evita sumar timeouts
  en cada petición mientras Redis está caído). El cliente se pone al día por REST.
- Las publicaciones ligadas a una transacción se hacen con `al_confirmar` (después del COMMIT).
"""
import asyncio
import logging
import threading
import time

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import transaction

from realtime import eventos

logger = logging.getLogger('realtime')

TIMEOUT_PUBLICACION_SEGUNDOS = 3.0
CORTE_TRAS_FALLO_SEGUNDOS = 30.0

_estado = {'desactivado_hasta': 0.0}
_lock = threading.Lock()


def _capa():
    if not getattr(settings, 'WS_PUBLICAR_EVENTOS', False):
        return None
    if not getattr(settings, 'CHANNEL_LAYERS', None):
        return None
    try:
        from channels.layers import get_channel_layer

        return get_channel_layer()
    except Exception as exc:  # pragma: no cover - configuración inválida
        logger.warning('Channel layer no disponible: %s', type(exc).__name__)
        return None


def reiniciar_corte():
    """Solo para pruebas."""
    with _lock:
        _estado['desactivado_hasta'] = 0.0


async def _enviar(capa, grupos, mensaje):
    await asyncio.wait_for(
        asyncio.gather(*(capa.group_send(g, mensaje) for g in grupos)),
        timeout=TIMEOUT_PUBLICACION_SEGUNDOS,
    )


def _publicar_mensaje(grupos, mensaje, etiqueta) -> bool:
    capa = _capa()
    grupos = [g for g in dict.fromkeys(grupos or []) if g]
    if capa is None or not grupos:
        return False
    if time.monotonic() < _estado['desactivado_hasta']:
        return False
    try:
        async_to_sync(_enviar)(capa, grupos, mensaje)
        return True
    except Exception as exc:
        with _lock:
            _estado['desactivado_hasta'] = time.monotonic() + CORTE_TRAS_FALLO_SEGUNDOS
        logger.warning(
            'No se pudo publicar %s en %d grupo(s): %s. Se recupera por REST.',
            etiqueta, len(grupos), type(exc).__name__,
        )
        return False


def publicar(grupos, sobre: dict) -> bool:
    """Publica un sobre v1 (ver eventos.construir_sobre) en los grupos indicados."""
    return _publicar_mensaje(
        grupos, {'type': 'evento.enviar', 'evento': sobre}, sobre.get('event_type'),
    )


def publicar_evento(event_type: str, categoria: str, data: dict, *, usuarios=(), roles=()) -> bool:
    grupos = [eventos.grupo_usuario(u) for u in usuarios if u] + [eventos.grupo_rol(r) for r in roles if r]
    return publicar(grupos, eventos.construir_sobre(event_type, categoria, data))


def publicar_a_usuario(id_usuario, event_type: str, data: dict, categoria: str = eventos.NP) -> bool:
    return publicar_evento(event_type, categoria, data, usuarios=[id_usuario])


def revocar_sesion(id_usuario, motivo: str) -> bool:
    """SEG-01: el consumer envía `sesion.revocada` y cierra con 4003."""
    return _publicar_mensaje(
        [eventos.grupo_usuario(id_usuario)],
        {'type': 'sesion.revocar', 'motivo': motivo},
        'sesion.revocada',
    )


def _seguro(func):
    try:
        func()
    except Exception as exc:
        logger.warning('Fallo en entrega post-commit: %s', type(exc).__name__)


def al_confirmar(func, clave_unica=None):
    """
    Ejecuta `func` después del COMMIT de la transacción actual (inmediatamente si no hay
    transacción). Nunca propaga errores. Si se da `clave_unica`, dentro de la misma
    transacción solo se registra una vez (coalesce de actualizaciones silenciosas).
    """
    conexion = transaction.get_connection()
    if clave_unica is not None and conexion.in_atomic_block:
        for entrada in getattr(conexion, 'run_on_commit', []):
            if getattr(entrada[1], '_clave_realtime', None) == clave_unica:
                return

    def envoltura():
        _seguro(func)

    envoltura._clave_realtime = clave_unica
    transaction.on_commit(envoltura, robust=True)
