"""Aplicación FastAPI del microservicio independiente VetericanoIA."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.config import DINOV2_EMBEDDING_DIM, IASettings, cargar_configuracion
from app.dinov2_engine import ColaSaturadaError, Dinov2Engine, MotorDinov2Error
from app.security import (
    HEADER_SECRETO_INTERNO,
    AuthFaltanteError,
    AuthInvalidaError,
    AuthNoConfiguradaError,
    ImagenInvalidaError,
    PayloadDemasiadoGrandeError,
    decodificar_y_validar_imagen_bytes,
    leer_upload_con_limite,
    verificar_secreto_interno,
)

logger = logging.getLogger(__name__)


def _validar_peticion_embed_previa(headers: Any, cfg: IASettings) -> Optional[JSONResponse]:
    """Valida autenticación interna y Content-Length antes de leer el multipart."""
    try:
        verificar_secreto_interno(
            headers.get(HEADER_SECRETO_INTERNO),
            cfg.internal_shared_secret,
        )
    except AuthNoConfiguradaError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={'ok': False, 'detail': str(exc)},
        )
    except AuthFaltanteError as exc:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={'ok': False, 'detail': str(exc)},
        )
    except AuthInvalidaError as exc:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={'ok': False, 'detail': str(exc)},
        )

    content_length = headers.get('content-length')
    if content_length is not None:
        try:
            if int(content_length) > cfg.max_image_bytes + 65536:
                return JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={
                        'ok': False,
                        'detail': (
                            f'La petición supera el límite máximo permitido '
                            f'({cfg.max_image_bytes} bytes).'
                        ),
                    },
                )
        except ValueError:
            pass
    return None


def create_app(
    custom_settings: Optional[IASettings] = None,
    custom_engine: Optional[Dinov2Engine] = None,
) -> FastAPI:
    cfg = custom_settings or cargar_configuracion()
    engine = custom_engine or Dinov2Engine(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if cfg.preload_model:
            try:
                engine.cargar_modelo()
            except Exception as exc:
                logger.warning('No se pudo precargar DINOv2 al iniciar: %s', exc)
        yield

    app = FastAPI(
        title='VetericanoIA',
        description='Microservicio interno aislado para extracción de embeddings visuales DINOv2 Small (384D).',
        version='1.0.0',
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = cfg
    app.state.engine = engine

    @app.middleware('http')
    async def proteger_embed_antes_de_multipart(request: Request, call_next):
        """
        Rechaza solicitudes sin secreto interno válido, con Content-Length excedido
        o sin cupo disponible ANTES de que FastAPI parsee archivos multipart.
        """
        if request.method.upper() == 'POST' and request.url.path.rstrip('/') == '/embed':
            rechazo = _validar_peticion_embed_previa(request.headers, cfg)
            if rechazo is not None:
                return rechazo

            try:
                engine.reservar_cupo()
            except ColaSaturadaError as exc:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    headers={'Retry-After': '1'},
                    content={'ok': False, 'detail': str(exc)},
                )

            request.state.ia_cupo_pendiente = True
            try:
                return await call_next(request)
            finally:
                if getattr(request.state, 'ia_cupo_pendiente', False):
                    request.state.ia_cupo_pendiente = False
                    engine.liberar_cupo()

        return await call_next(request)

    @app.get('/health/')
    async def health_check():
        cargado = engine.model_loaded
        listo = cargado or (not cfg.preload_model and engine.last_error is None)
        payload = {
            'status': 'ok' if listo else 'degraded',
            'modelo': cfg.model_name,
            'device': cfg.device,
            'modelo_cargado': cargado,
            'dimensiones': DINOV2_EMBEDDING_DIM,
            'max_concurrencia': cfg.max_concurrent_inferences,
            'max_cola': cfg.max_pending_queue,
            'en_vuelo': engine.inflight_count,
        }
        codigo = status.HTTP_200_OK if listo else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(status_code=codigo, content=payload)

    @app.post('/embed/')
    async def generar_embedding(
        request: Request,
        imagen: UploadFile = File(...),
    ):
        rechazo = _validar_peticion_embed_previa(request.headers, cfg)
        if rechazo is not None:
            return rechazo

        if not getattr(request.state, 'ia_cupo_pendiente', False):
            try:
                engine.reservar_cupo()
                request.state.ia_cupo_pendiente = True
            except ColaSaturadaError as exc:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    headers={'Retry-After': '1'},
                    content={'ok': False, 'detail': str(exc)},
                )

        def _liberar_si_pendiente() -> None:
            if getattr(request.state, 'ia_cupo_pendiente', False):
                request.state.ia_cupo_pendiente = False
                engine.liberar_cupo()

        try:
            t_inicio = time.perf_counter()
            try:
                contenido = await leer_upload_con_limite(imagen, cfg.max_image_bytes)
                imagen_rgb = decodificar_y_validar_imagen_bytes(
                    contenido,
                    max_bytes=cfg.max_image_bytes,
                    dimension_minima=cfg.min_dimension_px,
                    max_pixeles=cfg.max_image_pixels,
                )
            except PayloadDemasiadoGrandeError as exc:
                _liberar_si_pendiente()
                logger.warning('Payload excedido en /embed/: %s', exc)
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=str(exc),
                ) from exc
            except ImagenInvalidaError as exc:
                _liberar_si_pendiente()
                logger.warning('Imagen inválida en /embed/: %s', exc)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc

            try:
                # `inferir_con_control` libera el cupo en su bloque `finally`.
                request.state.ia_cupo_pendiente = False
                embedding, duracion_ms = await engine.inferir_con_control(
                    imagen_rgb,
                    cupo_reservado=True,
                )
            except ColaSaturadaError as exc:
                logger.warning('Cola saturada en /embed/: %s', exc)
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    headers={'Retry-After': '1'},
                    content={'ok': False, 'detail': str(exc)},
                )
            except MotorDinov2Error as exc:
                logger.error('Error DINOv2 en /embed/: %s', exc)
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={'ok': False, 'detail': str(exc)},
                )
        except asyncio.CancelledError:
            _liberar_si_pendiente()
            elapsed_ms = round((time.perf_counter() - t_inicio) * 1000.0, 2)
            logger.warning(
                'Solicitud /embed/ cancelada por desconexión o timeout del cliente tras %.2f ms.',
                elapsed_ms,
            )
            raise
        except HTTPException:
            raise
        except Exception as exc:
            _liberar_si_pendiente()
            logger.exception('Error inesperado procesando /embed/: %s', exc)
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={'ok': False, 'detail': 'Fallo interno temporal en inferencia visual.'},
            )

        total_ms = round((time.perf_counter() - t_inicio) * 1000.0, 2)
        logger.info(
            'POST /embed/ OK: bytes=%d size=%sx%s inferencia_ms=%.2f total_ms=%.2f threads=%d',
            len(contenido),
            imagen_rgb.size[0],
            imagen_rgb.size[1],
            duracion_ms,
            total_ms,
            cfg.torch_num_threads,
        )
        return {
            'ok': True,
            'modelo': cfg.model_name,
            'dimensiones': DINOV2_EMBEDDING_DIM,
            'embedding': embedding,
            'tiempo_inferencia_ms': duracion_ms,
        }

    return app


app = create_app()
