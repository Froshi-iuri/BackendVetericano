"""Punto de entrada con un único worker Uvicorn y límites acotados."""

from __future__ import annotations

import logging
import os

import uvicorn

from app.config import cargar_configuracion


def _aplicar_limites_hilos_entorno(torch_num_threads: int) -> None:
    """Limita OpenMP/BLAS antes de cargar PyTorch para evitar contención en contenedores CPU."""
    hilos = str(max(1, min(4, int(torch_num_threads))))
    for var in (
        'OMP_NUM_THREADS',
        'MKL_NUM_THREADS',
        'OPENBLAS_NUM_THREADS',
        'VECLIB_MAXIMUM_THREADS',
        'NUMEXPR_NUM_THREADS',
        'TORCH_NUM_THREADS',
    ):
        os.environ.setdefault(var, hilos)
    os.environ.setdefault('OMP_WAIT_POLICY', 'PASSIVE')
    os.environ.setdefault('KMP_BLOCKTIME', '0')


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    cfg = cargar_configuracion()
    _aplicar_limites_hilos_entorno(cfg.torch_num_threads)
    uvicorn.run(
        'app.main:app',
        host=cfg.host,
        port=cfg.port,
        workers=1,
        limit_concurrency=cfg.request_limit_concurrency,
        timeout_keep_alive=15,
        log_level='info',
    )


if __name__ == '__main__':
    main()
