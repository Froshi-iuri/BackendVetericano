"""Configuración independiente basada en variables de entorno para VetericanoIA."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False)

DINOV2_MODEL_NAME_DEFAULT = 'facebook/dinov2-small'
DINOV2_EMBEDDING_DIM = 384
MAX_IMAGE_BYTES_DEFAULT = 2 * 1024 * 1024
MIN_DIMENSION_PX_DEFAULT = 16
MAX_IMAGE_PIXELS_DEFAULT = 16_000_000


def _leer_bool(valor: Any, default: bool) -> bool:
    if valor is None:
        return default
    texto = str(valor).strip().lower()
    if texto in ('1', 'true', 'yes', 'si', 'on'):
        return True
    if texto in ('0', 'false', 'no', 'off'):
        return False
    return default


def _leer_int_rango(valor: Any, default: int, minimo: int, maximo: int) -> int:
    try:
        num = int(valor)
    except (TypeError, ValueError):
        return default
    return max(minimo, min(maximo, num))


def _leer_float_rango(valor: Any, default: float, minimo: float, maximo: float) -> float:
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return max(minimo, min(maximo, num))


@dataclass(frozen=True)
class IASettings:
    internal_shared_secret: str
    model_name: str = DINOV2_MODEL_NAME_DEFAULT
    device: str = 'cpu'
    max_image_bytes: int = MAX_IMAGE_BYTES_DEFAULT
    min_dimension_px: int = MIN_DIMENSION_PX_DEFAULT
    max_image_pixels: int = MAX_IMAGE_PIXELS_DEFAULT
    max_concurrent_inferences: int = 1
    max_pending_queue: int = 4
    queue_wait_seconds: float = 4.0
    torch_num_threads: int = 1
    preload_model: bool = True
    host: str = '127.0.0.1'
    port: int = 8001

    @property
    def request_limit_concurrency(self) -> int:
        return self.max_concurrent_inferences + self.max_pending_queue + 2


def cargar_configuracion() -> IASettings:
    """Lee la configuración de entorno aplicando límites seguros para CPU y RAM."""
    model_name = (
        os.environ.get('IA_MODEL_NAME', DINOV2_MODEL_NAME_DEFAULT).strip()
        or DINOV2_MODEL_NAME_DEFAULT
    )
    device = os.environ.get('IA_DEVICE', 'cpu').strip() or 'cpu'
    en_railway = any(
        bool(os.environ.get(var, '').strip())
        for var in (
            'RAILWAY_ENVIRONMENT',
            'RAILWAY_PROJECT_ID',
            'RAILWAY_SERVICE_ID',
            'RAILWAY_PRIVATE_DOMAIN',
        )
    )
    host_default = '::' if en_railway else '127.0.0.1'
    host = os.environ.get('IA_HOST', '').strip() or host_default
    puerto_raw = os.environ.get('IA_PORT')
    if puerto_raw is None or not str(puerto_raw).strip():
        puerto_raw = os.environ.get('PORT')

    return IASettings(
        internal_shared_secret=os.environ.get('IA_INTERNAL_SHARED_SECRET', '').strip(),
        model_name=model_name,
        device=device,
        max_image_bytes=_leer_int_rango(
            os.environ.get('IA_MAX_IMAGE_BYTES'),
            MAX_IMAGE_BYTES_DEFAULT,
            32 * 1024,
            10 * 1024 * 1024,
        ),
        min_dimension_px=_leer_int_rango(
            os.environ.get('IA_MIN_DIMENSION_PX'),
            MIN_DIMENSION_PX_DEFAULT,
            8,
            512,
        ),
        max_image_pixels=_leer_int_rango(
            os.environ.get('IA_MAX_IMAGE_PIXELS'),
            MAX_IMAGE_PIXELS_DEFAULT,
            65_536,
            40_000_000,
        ),
        max_concurrent_inferences=_leer_int_rango(
            os.environ.get('IA_MAX_CONCURRENT_INFERENCES'),
            1,
            1,
            4,
        ),
        max_pending_queue=_leer_int_rango(
            os.environ.get('IA_MAX_PENDING_QUEUE'),
            4,
            0,
            32,
        ),
        queue_wait_seconds=_leer_float_rango(
            os.environ.get('IA_MAX_QUEUE_WAIT_SECONDS'),
            4.0,
            0.1,
            30.0,
        ),
        torch_num_threads=_leer_int_rango(
            os.environ.get('IA_TORCH_NUM_THREADS'),
            1,
            1,
            4,
        ),
        preload_model=_leer_bool(os.environ.get('IA_PRELOAD_MODEL'), True),
        host=host,
        port=_leer_int_rango(puerto_raw, 8001, 1, 65535),
    )
