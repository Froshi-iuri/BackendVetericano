"""Punto de entrada con un único worker Uvicorn y límites acotados."""

from __future__ import annotations

import uvicorn

from app.config import cargar_configuracion


def main() -> None:
    cfg = cargar_configuracion()
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
