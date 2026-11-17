"""
Envío FCM (HTTP v1) best-effort, solo para notificaciones persistentes (NP).

- Apagado por defecto (FCM_HABILITADO=False) y sin credenciales no hace nada.
- Credenciales de cuenta de servicio: FCM_CREDENTIALS_JSON (contenido) o FCM_CREDENTIALS_FILE (ruta).
  Nunca se registran.
- Se llama después del COMMIT. La consulta de tokens se hace en el hilo de la petición y el
  envío HTTP en un pool de 2 hilos, para no bloquear a gunicorn.
- Envío por token individual; los tokens UNREGISTERED se desactivan.
- Si FCM falla, Android recupera por REST al volver a primer plano (contrato §7).
"""
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('realtime')

SCOPE = 'https://www.googleapis.com/auth/firebase.messaging'
CANAL_ANDROID = 'vetericano_notificaciones'
TIMEOUT_SEGUNDOS = 5

# En pruebas se pone en True para ejecutar el envío en el mismo hilo.
EJECUTAR_EN_LINEA = False

_lock = threading.Lock()
_pool = None
_credenciales_cache = {}


def _info_credenciales():
    if settings.FCM_CREDENTIALS_JSON:
        return json.loads(settings.FCM_CREDENTIALS_JSON)
    if settings.FCM_CREDENTIALS_FILE:
        with open(settings.FCM_CREDENTIALS_FILE, encoding='utf-8') as fh:
            return json.load(fh)
    return None


def tablas_disponibles() -> bool:
    """dispositivos_fcm solo existe tras la migración `realtime` (mismo flag que notificaciones_sistema)."""
    return bool(getattr(settings, 'NOTIFICACIONES_SISTEMA_HABILITADAS', False))


def habilitado() -> bool:
    return tablas_disponibles() and bool(getattr(settings, 'FCM_HABILITADO', False)) and bool(
        getattr(settings, 'FCM_CREDENTIALS_JSON', '') or getattr(settings, 'FCM_CREDENTIALS_FILE', '')
    )


def _credenciales():
    with _lock:
        if 'cred' not in _credenciales_cache:
            from google.oauth2 import service_account

            info = _info_credenciales()
            cred = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
            _credenciales_cache['cred'] = cred
            _credenciales_cache['project_id'] = settings.FCM_PROJECT_ID or info.get('project_id')
        return _credenciales_cache['cred'], _credenciales_cache['project_id']


def _ejecutor():
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='fcm')
        return _pool


def construir_mensaje(token: str, item: dict) -> dict:
    """Mensaje HTTP v1 (contrato §7). Los valores de `data` deben ser strings."""
    return {
        'message': {
            'token': token,
            'notification': {
                'title': (item.get('titulo') or 'Vetericano')[:200],
                'body': (item.get('mensaje') or '')[:1000],
            },
            'data': {
                'event_type': str(item.get('event_type') or ''),
                'clave': str(item.get('clave') or ''),
                'modulo': str(item.get('modulo') or ''),
                'schema_version': '1',
                'referencia': json.dumps(item.get('referencia') or {}, ensure_ascii=False),
            },
            'android': {
                'priority': 'HIGH',
                'notification': {'channel_id': CANAL_ANDROID},
            },
        }
    }


def _es_token_invalido(respuesta) -> bool:
    if respuesta.status_code == 404:
        return True
    if respuesta.status_code != 400:
        return False
    try:
        error = respuesta.json().get('error', {})
    except ValueError:
        return False
    for detalle in error.get('details', []) or []:
        if detalle.get('errorCode') == 'UNREGISTERED':
            return True
    return 'registration token' in str(error.get('message', '')).lower()


def _enviar_lote(trabajos, sesion=None):
    """trabajos: lista de (token, item). Se ejecuta fuera del hilo de la petición."""
    from django.db import connection

    try:
        if sesion is None:
            from google.auth.transport.requests import AuthorizedSession

            cred, project_id = _credenciales()
            sesion = AuthorizedSession(cred)
        else:
            project_id = settings.FCM_PROJECT_ID or 'proyecto-prueba'
        url = f'https://fcm.googleapis.com/v1/projects/{project_id}/messages:send'
        invalidos = []
        enviados = 0
        for token, item in trabajos:
            try:
                resp = sesion.post(url, json=construir_mensaje(token, item), timeout=TIMEOUT_SEGUNDOS)
            except Exception as exc:
                logger.warning('FCM: error de red (token …%s): %s', token[-6:], type(exc).__name__)
                continue
            if resp.status_code == 200:
                enviados += 1
            elif _es_token_invalido(resp):
                invalidos.append(token)
            else:
                logger.warning('FCM: respuesta %s (token …%s)', resp.status_code, token[-6:])
        if invalidos:
            from realtime.models import DispositivoFCM

            DispositivoFCM.objects.filter(token__in=invalidos).update(
                activo=False, ultimo_error='UNREGISTERED', actualizado_en=timezone.now(),
            )
        logger.info('FCM: %d enviados, %d tokens desactivados', enviados, len(invalidos))
        return enviados, invalidos
    except Exception as exc:
        logger.warning('FCM: lote no enviado: %s', type(exc).__name__)
        return 0, []
    finally:
        if not EJECUTAR_EN_LINEA:
            connection.close()


def enviar_items(pares) -> int:
    """
    pares: lista de (id_usuario_destino, item unificado). Devuelve cuántos envíos se encolaron.
    Llamar después del COMMIT (lo hace realtime.notificar).
    """
    if not pares or not habilitado():
        return 0
    try:
        from realtime.models import DispositivoFCM

        ids = {uid for uid, _ in pares if uid}
        tokens = {}
        for uid, token in DispositivoFCM.objects.filter(
            id_usuario_id__in=ids, activo=True,
        ).values_list('id_usuario_id', 'token'):
            tokens.setdefault(uid, []).append(token)
        trabajos = [(tk, item) for uid, item in pares for tk in tokens.get(uid, [])]
    except Exception as exc:
        logger.warning('FCM: no se pudieron leer los dispositivos: %s', type(exc).__name__)
        return 0
    if not trabajos:
        return 0
    if EJECUTAR_EN_LINEA:
        _enviar_lote(trabajos)
    else:
        _ejecutor().submit(_enviar_lote, trabajos)
    return len(trabajos)


def desactivar_tokens_usuario(id_usuario) -> int:
    """SEG-01: al inactivar un usuario se desactivan sus dispositivos."""
    if not tablas_disponibles():
        return 0
    from realtime.models import DispositivoFCM

    return DispositivoFCM.objects.filter(id_usuario_id=id_usuario, activo=True).update(
        activo=False, ultimo_error='USUARIO_INACTIVADO', actualizado_en=timezone.now(),
    )
