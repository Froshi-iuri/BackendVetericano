"""Motor de inferencia DINOv2 Small con carga única en RAM y control de concurrencia."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import anyio

from app.config import DINOV2_EMBEDDING_DIM, IASettings

logger = logging.getLogger(__name__)


class MotorDinov2Error(RuntimeError):
    """Error cuando DINOv2 Small no está disponible o falla durante inferencia."""


class ColaSaturadaError(RuntimeError):
    """Error cuando se supera la capacidad de cola o tiempo de espera para inferir."""


def normalizar_vector_l2(
    vector: Sequence[Any],
    *,
    dimension_esperada: Optional[int] = DINOV2_EMBEDDING_DIM,
) -> List[float]:
    """Convierte un vector numérico a lista de floats normalizada por norma L2."""
    valores: List[float] = []
    for elem in vector or ():
        if isinstance(elem, bool):
            raise ValueError('El embedding contiene valores booleanos inválidos.')
        try:
            num = float(elem)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Componente de embedding no numérico: {elem!r}') from exc
        if not math.isfinite(num):
            raise ValueError('El embedding contiene valores NaN o infinitos.')
        valores.append(num)

    if not valores:
        raise ValueError('El embedding está vacío.')
    if dimension_esperada is not None and len(valores) != int(dimension_esperada):
        raise ValueError(
            f'Dimensión de embedding inesperada: {len(valores)} (esperada: {dimension_esperada}).'
        )

    norma = math.sqrt(sum(v * v for v in valores))
    if norma <= 1e-12 or not math.isfinite(norma):
        raise ValueError('No se puede normalizar un vector de norma cero.')
    return [v / norma for v in valores]


def _extraer_vector_cls_desde_salida(outputs: Any) -> List[float]:
    last_hidden = getattr(outputs, 'last_hidden_state', None)
    if last_hidden is None and isinstance(outputs, (tuple, list)) and outputs:
        last_hidden = outputs[0]
    if last_hidden is None:
        pooler = getattr(outputs, 'pooler_output', None)
        if pooler is None:
            raise MotorDinov2Error('La salida de DINOv2 no contiene `last_hidden_state`.')
        cls_tensor = pooler[0] if getattr(pooler, 'ndim', 0) == 2 else pooler
    else:
        cls_tensor = last_hidden[:, 0, :] if getattr(last_hidden, 'ndim', 0) == 3 else last_hidden[0]
        if getattr(cls_tensor, 'ndim', 0) == 2:
            cls_tensor = cls_tensor[0]

    if hasattr(cls_tensor, 'detach'):
        cls_tensor = cls_tensor.detach()
    if hasattr(cls_tensor, 'cpu'):
        cls_tensor = cls_tensor.cpu()
    bruto = cls_tensor.tolist() if hasattr(cls_tensor, 'tolist') else list(cls_tensor)
    if isinstance(bruto, list) and bruto and isinstance(bruto[0], list):
        bruto = bruto[0]
    return normalizar_vector_l2(bruto, dimension_esperada=DINOV2_EMBEDDING_DIM)


class Dinov2Engine:
    """Gestiona una única instancia del modelo en memoria y acota la concurrencia."""

    def __init__(self, settings: IASettings):
        self.settings = settings
        self._model_lock = threading.Lock()
        self._model_cache: Dict[Tuple[str, str], Tuple[Any, Any, Any]] = {}
        self._last_error: Optional[str] = None

        self._gate_lock = threading.Lock()
        self._inflight = 0
        self._semaphore_loop: Optional[asyncio.AbstractEventLoop] = None
        self._semaphore: Optional[asyncio.Semaphore] = None

    @property
    def model_loaded(self) -> bool:
        with self._model_lock:
            return len(self._model_cache) > 0

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def inflight_count(self) -> int:
        with self._gate_lock:
            return self._inflight

    def limpiar_cache(self) -> None:
        with self._model_lock:
            self._model_cache.clear()
        self._last_error = None

    def _obtener_semaforo(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_inferences)
            self._semaphore_loop = loop
        return self._semaphore

    def cargar_modelo(self) -> Tuple[Any, Any, Any, str]:
        """Carga `facebook/dinov2-small` una sola vez y lo conserva en memoria."""
        nombre = self.settings.model_name
        dev_inicial = self.settings.device or 'cpu'

        with self._model_lock:
            for (cached_name, cached_dev), tupla in self._model_cache.items():
                if cached_name == nombre:
                    processor, model, torch_mod = tupla
                    return processor, model, torch_mod, cached_dev

            hilos = max(1, min(4, int(getattr(self.settings, 'torch_num_threads', 1))))
            for var in (
                'OMP_NUM_THREADS',
                'MKL_NUM_THREADS',
                'OPENBLAS_NUM_THREADS',
                'VECLIB_MAXIMUM_THREADS',
                'NUMEXPR_NUM_THREADS',
                'TORCH_NUM_THREADS',
            ):
                os.environ.setdefault(var, str(hilos))
            os.environ.setdefault('OMP_WAIT_POLICY', 'PASSIVE')
            os.environ.setdefault('KMP_BLOCKTIME', '0')

            try:
                import torch
                from transformers import AutoImageProcessor, AutoModel
            except Exception as exc:
                self._last_error = f'Dependencias PyTorch/Transformers no disponibles: {exc}'
                raise MotorDinov2Error(self._last_error) from exc

            if hasattr(torch, 'set_num_threads'):
                try:
                    torch.set_num_threads(hilos)
                except RuntimeError:
                    pass
            if hasattr(torch, 'set_num_interop_threads'):
                try:
                    torch.set_num_interop_threads(1)
                except RuntimeError:
                    pass

            dev_final = 'cpu'
            if dev_inicial.startswith('cuda'):
                if getattr(getattr(torch, 'cuda', None), 'is_available', lambda: False)():
                    dev_final = dev_inicial
                else:
                    logger.warning('CUDA no disponible; usando CPU en VetericanoIA.')

            try:
                processor = AutoImageProcessor.from_pretrained(nombre)
                model = AutoModel.from_pretrained(nombre)
                if hasattr(model, 'eval'):
                    model.eval()
                if dev_final != 'cpu' and hasattr(model, 'to'):
                    model.to(dev_final)
            except Exception as exc:
                self._last_error = f"No se pudo cargar el modelo '{nombre}': {exc}"
                raise MotorDinov2Error(self._last_error) from exc

            warmup_ms = 0.0
            try:
                from PIL import Image

                t_warm = time.perf_counter()
                dummy_img = Image.new('RGB', (32, 32), (128, 128, 128))
                warm_inputs = processor(images=dummy_img, return_tensors='pt')
                if dev_final != 'cpu' and hasattr(warm_inputs, 'items'):
                    warm_inputs = {
                        k: (v.to(dev_final) if hasattr(v, 'to') else v)
                        for k, v in warm_inputs.items()
                    }
                with torch.no_grad():
                    model(**warm_inputs)
                warmup_ms = round((time.perf_counter() - t_warm) * 1000.0, 2)
            except Exception as exc:
                logger.warning('Precalentamiento inicial DINOv2 omitido: %s', exc)

            self._model_cache[(nombre, dev_final)] = (processor, model, torch)
            self._last_error = None
            logger.info(
                'Modelo DINOv2 cargado en memoria (%s, device=%s, torch_threads=%d, warmup_ms=%.2f).',
                nombre,
                dev_final,
                hilos,
                warmup_ms,
            )
            return processor, model, torch, dev_final

    def extraer_embedding_sync(self, imagen_rgb: Any) -> Tuple[List[float], float]:
        """Ejecuta la inferencia síncrona en CPU/GPU sin descargar modelo por petición."""
        processor, model, torch_mod, dev_final = self.cargar_modelo()
        t0 = time.perf_counter()
        try:
            inputs = processor(images=imagen_rgb, return_tensors='pt')
            if dev_final != 'cpu' and hasattr(inputs, 'items'):
                inputs = {
                    k: (v.to(dev_final) if hasattr(v, 'to') else v)
                    for k, v in inputs.items()
                }
            with torch_mod.no_grad():
                outputs = model(**inputs)
            embedding = _extraer_vector_cls_desde_salida(outputs)
        except MotorDinov2Error:
            raise
        except Exception as exc:
            raise MotorDinov2Error(f'Error ejecutando inferencia DINOv2: {exc}') from exc
        duracion_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return embedding, duracion_ms

    def reservar_cupo(self) -> None:
        """Verifica inmediatamente antes de leer payloads grandes si hay espacio en cola."""
        capacidad_total = self.settings.max_concurrent_inferences + self.settings.max_pending_queue
        with self._gate_lock:
            if self._inflight >= capacidad_total:
                raise ColaSaturadaError(
                    'Microservicio IA saturado temporalmente; intente nuevamente.'
                )
            self._inflight += 1

    def liberar_cupo(self) -> None:
        with self._gate_lock:
            if self._inflight > 0:
                self._inflight -= 1

    async def inferir_con_control(self, imagen_rgb: Any, *, cupo_reservado: bool = False) -> Tuple[List[float], float]:
        """Ejecuta la inferencia bajo semáforo acotado y timeout de cola."""
        if not cupo_reservado:
            self.reservar_cupo()

        semaforo = self._obtener_semaforo()
        adquirido = False
        try:
            try:
                await asyncio.wait_for(
                    semaforo.acquire(),
                    timeout=self.settings.queue_wait_seconds,
                )
                adquirido = True
            except TimeoutError as exc:
                raise ColaSaturadaError(
                    'Tiempo de espera agotado en cola de inferencia DINOv2.'
                ) from exc

            return await anyio.to_thread.run_sync(self.extraer_embedding_sync, imagen_rgb)
        finally:
            if adquirido:
                semaforo.release()
            self.liberar_cupo()
