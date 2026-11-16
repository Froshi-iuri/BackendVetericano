"""
Cliente HTTP interno para solicitar embeddings DINOv2 Small (384D) al microservicio
independiente `VetericanoIA` sin cargar PyTorch ni Transformers en procesos Django.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

HEADER_SECRETO_INTERNO = 'X-Vetericano-IA-Secret'
TIMEOUT_MICROSERVICIO_DEFAULT = 6.0


def _leer_timeout_seguro(valor: Any, default: float = TIMEOUT_MICROSERVICIO_DEFAULT) -> float:
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return max(0.5, min(30.0, num))


def obtener_config_ia_microservicio() -> Dict[str, Any]:
    """Obtiene la configuración de conexión hacia el microservicio interno VetericanoIA."""
    url_raw = str(getattr(settings, 'DUPLICADOS_DINOV2_MICROSERVICIO_URL', '') or '').strip()
    url_base = url_raw.rstrip('/')
    secreto = str(getattr(settings, 'DUPLICADOS_DINOV2_MICROSERVICIO_SECRET', '') or '').strip()
    timeout = _leer_timeout_seguro(
        getattr(settings, 'DUPLICADOS_DINOV2_MICROSERVICIO_TIMEOUT_SEGUNDOS', TIMEOUT_MICROSERVICIO_DEFAULT)
    )
    return {
        'url_base': url_base,
        'secreto': secreto,
        'timeout': timeout,
    }


def _validar_url_microservicio(url_base: str) -> str:
    if not url_base:
        raise ValueError('DUPLICADOS_DINOV2_MICROSERVICIO_URL no está configurada.')
    parsed = urlparse(url_base)
    scheme = (parsed.scheme or '').lower()
    if scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('DUPLICADOS_DINOV2_MICROSERVICIO_URL debe ser una URL HTTP/HTTPS válida.')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('DUPLICADOS_DINOV2_MICROSERVICIO_URL no debe incluir credenciales embebidas.')
    return url_base


def solicitar_embedding_dinov2(
    contenido_imagen: bytes,
    *,
    timeout: Optional[float] = None,
) -> Optional[List[float]]:
    """
    Envía bytes de imagen ya validados a `POST /embed/` en `VetericanoIA` y
    retorna su embedding normalizado L2 de 384 dimensiones.

    - Si el microservicio responde 400/413 (imagen inválida o excedida), retorna `None`.
    - Si hay fallo de red, timeout, credencial inválida o 5xx (incluido 503),
      lanza `ModelDinov2NoDisponibleError` para activar el respaldo controlado.
    """
    from peticiones.services.dinov2_service import (
        DINOV2_EMBEDDING_DIM,
        ModelDinov2NoDisponibleError,
        normalizar_vector_l2,
    )

    if not contenido_imagen:
        return None

    cfg = obtener_config_ia_microservicio()
    try:
        url_base = _validar_url_microservicio(cfg['url_base'])
    except ValueError as exc:
        raise ModelDinov2NoDisponibleError(str(exc)) from exc

    secreto = cfg['secreto']
    if not secreto:
        raise ModelDinov2NoDisponibleError(
            'DUPLICADOS_DINOV2_MICROSERVICIO_SECRET no está configurado.'
        )

    timeout_val = _leer_timeout_seguro(timeout, cfg['timeout']) if timeout is not None else cfg['timeout']
    endpoint = f'{url_base}/embed/'

    resp = None
    try:
        resp = requests.post(
            endpoint,
            headers={HEADER_SECRETO_INTERNO: secreto},
            files={'imagen': ('imagen.bin', contenido_imagen, 'application/octet-stream')},
            timeout=timeout_val,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise ModelDinov2NoDisponibleError(
            f'Timeout ({timeout_val}s) esperando respuesta de VetericanoIA.'
        ) from exc
    except requests.RequestException as exc:
        raise ModelDinov2NoDisponibleError(
            f'No fue posible conectar con VetericanoIA: {exc}'
        ) from exc

    try:
        if resp.status_code in (400, 413):
            logger.warning(
                'VetericanoIA rechazó la imagen (HTTP %s).',
                resp.status_code,
            )
            return None

        if resp.status_code in (401, 403):
            raise ModelDinov2NoDisponibleError(
                f'Autenticación interna rechazada por VetericanoIA (HTTP {resp.status_code}).'
            )

        if resp.status_code != 200:
            raise ModelDinov2NoDisponibleError(
                f'VetericanoIA respondió estado no disponible (HTTP {resp.status_code}).'
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ModelDinov2NoDisponibleError(
                'VetericanoIA devolvió una respuesta JSON inválida.'
            ) from exc

        if not isinstance(payload, dict) or not payload.get('ok'):
            raise ModelDinov2NoDisponibleError(
                'VetericanoIA no confirmó inferencia exitosa.'
            )

        embedding_raw = payload.get('embedding')
        try:
            return normalizar_vector_l2(
                embedding_raw,
                dimension_esperada=DINOV2_EMBEDDING_DIM,
            )
        except ValueError as exc:
            raise ModelDinov2NoDisponibleError(
                f'Embedding inválido recibido desde VetericanoIA: {exc}'
            ) from exc
    finally:
        try:
            resp.close()
        except Exception:
            pass


def consultar_salud_microservicio_ia(*, timeout: Optional[float] = None) -> Dict[str, Any]:
    """Consulta `GET /health/` de VetericanoIA de forma no bloqueante."""
    cfg = obtener_config_ia_microservicio()
    url_base = _validar_url_microservicio(cfg['url_base'])
    timeout_val = _leer_timeout_seguro(timeout, min(cfg['timeout'], 3.0))
    resp = requests.get(f'{url_base}/health/', timeout=timeout_val, allow_redirects=False)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return {'status_code': resp.status_code, 'data': data}
