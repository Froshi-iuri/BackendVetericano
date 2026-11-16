import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from django.conf import settings

from peticiones.services import imagen_hash_service
from peticiones.utils import detectar_tipo_archivo


logger = logging.getLogger(__name__)

DINOV2_MODEL_NAME_DEFAULT = 'facebook/dinov2-small'
DINOV2_EMBEDDING_DIM = 384
DINOV2_UMBRAL_DEFAULT = 0.45
DINOV2_DEVICE_DEFAULT = 'cpu'
DINOV2_BACKEND_MICROSERVICIO = 'microservicio'
DINOV2_BACKEND_LOCAL = 'local'
TIPO_METRICA_VISUAL = 'similitud_visual_coseno_no_identidad'
ADVERTENCIA_METRICA_VISUAL_DEFAULT = (
    'Métrica de similitud visual aproximada basada en embeddings DINOv2; '
    'no constituye certeza de identidad ni confirma que se trate del mismo animal.'
)

_MODEL_LOCK = threading.Lock()
_MODEL_CACHE: Dict[Tuple[str, str], Tuple[Any, Any, Any]] = {}


class ModelDinov2NoDisponibleError(RuntimeError):
    """Error controlado cuando DINOv2, PyTorch o Transformers no están disponibles o fallan en inferencia."""


def _leer_float_rango(valor: Any, default: float, minimo: float, maximo: float) -> float:
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return max(minimo, min(maximo, num))


def _leer_int_rango(valor: Any, default: int, minimo: int, maximo: int) -> int:
    try:
        num = int(valor)
    except (TypeError, ValueError):
        return default
    return max(minimo, min(maximo, num))


def obtener_config_dinov2() -> Dict[str, Any]:
    """Obtiene la configuración vigente de DINOv2 Small con defaults seguros."""
    model_name = str(
        getattr(settings, 'DUPLICADOS_DINOV2_MODEL_NAME', DINOV2_MODEL_NAME_DEFAULT)
        or DINOV2_MODEL_NAME_DEFAULT
    ).strip()
    device = str(
        getattr(settings, 'DUPLICADOS_DINOV2_DEVICE', DINOV2_DEVICE_DEFAULT)
        or DINOV2_DEVICE_DEFAULT
    ).strip() or DINOV2_DEVICE_DEFAULT

    backend = str(
        getattr(settings, 'DUPLICADOS_DINOV2_BACKEND', DINOV2_BACKEND_MICROSERVICIO)
        or DINOV2_BACKEND_MICROSERVICIO
    ).strip().lower()
    if backend not in (DINOV2_BACKEND_MICROSERVICIO, DINOV2_BACKEND_LOCAL):
        backend = DINOV2_BACKEND_MICROSERVICIO

    return {
        'ia_activa': bool(getattr(settings, 'DUPLICADOS_IA_DINOV2_ACTIVA', False)),
        'backend': backend,
        'model_name': model_name,
        'umbral_dinov2': _leer_float_rango(
            getattr(settings, 'DUPLICADOS_UMBRAL_DINOV2', DINOV2_UMBRAL_DEFAULT),
            DINOV2_UMBRAL_DEFAULT,
            0.0,
            1.0,
        ),
        'device': device,
        'cache_bd_activa': bool(getattr(settings, 'DUPLICADOS_DINOV2_CACHE_BD_ACTIVA', False)),
        'fallback_dhash': bool(getattr(settings, 'DUPLICADOS_DINOV2_FALLBACK_DHASH', True)),
        'dimension': DINOV2_EMBEDDING_DIM,
        'inferencia_ancho': _leer_int_rango(
            getattr(settings, 'DUPLICADOS_DINOV2_INFERENCIA_ANCHO', 512),
            512,
            64,
            1024,
        ),
        'inferencia_alto': _leer_int_rango(
            getattr(settings, 'DUPLICADOS_DINOV2_INFERENCIA_ALTO', 512),
            512,
            64,
            1024,
        ),
    }


def es_dinov2_activo(usar_dinov2: Optional[bool] = None) -> bool:
    """Determina si la detección visual con DINOv2 está habilitada para la ejecución actual."""
    if usar_dinov2 is not None:
        return bool(usar_dinov2)
    return obtener_config_dinov2()['ia_activa']


def limpiar_cache_modelo_dinov2() -> None:
    """Limpia la caché en memoria del modelo DINOv2 (útil para pruebas o liberación controlada)."""
    with _MODEL_LOCK:
        _MODEL_CACHE.clear()


def _resolver_device_seguro(torch_mod: Any, device_solicitado: str) -> str:
    dev = (device_solicitado or DINOV2_DEVICE_DEFAULT).strip()
    if dev.startswith('cuda'):
        cuda_mod = getattr(torch_mod, 'cuda', None)
        is_available = getattr(cuda_mod, 'is_available', None) if cuda_mod is not None else None
        if not callable(is_available) or not is_available():
            logger.warning("CUDA no disponible para DINOv2; usando CPU como respaldo.")
            return 'cpu'
    return dev


def _cargar_modelo_dinov2(
    model_name: Optional[str] = None,
    device: Optional[str] = None,
) -> Tuple[Any, Any, Any, str]:
    """
    Carga de forma perezosa y thread-safe `AutoImageProcessor` y `AutoModel` de
    `facebook/dinov2-small` en modo evaluación.
    """
    cfg = obtener_config_dinov2()
    nombre = (model_name or cfg['model_name']).strip()
    dev_inicial = (device or cfg['device']).strip() or DINOV2_DEVICE_DEFAULT

    try:
        import torch  # noqa: WPS433 - import perezoso intencional
        from transformers import AutoImageProcessor, AutoModel  # noqa: WPS433
    except Exception as exc:
        raise ModelDinov2NoDisponibleError(
            f"PyTorch/Transformers no disponibles para cargar {nombre}: {exc}"
        ) from exc

    dev_final = _resolver_device_seguro(torch, dev_inicial)
    clave = (nombre, dev_final)

    with _MODEL_LOCK:
        cacheado = _MODEL_CACHE.get(clave)
        if cacheado is not None:
            processor, model, torch_cached = cacheado
            return processor, model, torch_cached, dev_final

        try:
            processor = AutoImageProcessor.from_pretrained(nombre)
            model = AutoModel.from_pretrained(nombre)
            if hasattr(model, 'eval'):
                model.eval()
            if dev_final != 'cpu' and hasattr(model, 'to'):
                model.to(dev_final)
        except Exception as exc:
            raise ModelDinov2NoDisponibleError(
                f"No se pudo cargar el modelo DINOv2 '{nombre}': {exc}"
            ) from exc

        _MODEL_CACHE[clave] = (processor, model, torch)
        return processor, model, torch, dev_final


def normalizar_vector_l2(
    vector: Sequence[Any],
    *,
    dimension_esperada: Optional[int] = DINOV2_EMBEDDING_DIM,
) -> List[float]:
    """Convierte un iterable numérico a lista de floats normalizada por norma L2."""
    valores: List[float] = []
    for elem in vector or ():
        if isinstance(elem, bool):
            raise ValueError("El embedding contiene valores booleanos inválidos.")
        try:
            num = float(elem)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Componente de embedding no numérico: {elem!r}") from exc
        if not math.isfinite(num):
            raise ValueError("El embedding contiene valores NaN o infinitos.")
        valores.append(num)

    if not valores:
        raise ValueError("El embedding está vacío.")
    if dimension_esperada is not None and len(valores) != int(dimension_esperada):
        raise ValueError(
            f"Dimensión de embedding inesperada: {len(valores)} (esperada: {dimension_esperada})."
        )

    norma = math.sqrt(sum(v * v for v in valores))
    if norma <= 1e-12 or not math.isfinite(norma):
        raise ValueError("No se puede normalizar un vector de norma cero.")

    return [v / norma for v in valores]


def _extraer_vector_cls_desde_salida(outputs: Any) -> List[float]:
    last_hidden = getattr(outputs, 'last_hidden_state', None)
    if last_hidden is None and isinstance(outputs, (tuple, list)) and outputs:
        last_hidden = outputs[0]
    if last_hidden is None:
        pooler = getattr(outputs, 'pooler_output', None)
        if pooler is None:
            raise ModelDinov2NoDisponibleError("La salida de DINOv2 no contiene `last_hidden_state`.")
        cls_tensor = pooler[0] if getattr(pooler, 'ndim', 0) == 2 else pooler
    else:
        cls_tensor = last_hidden[:, 0, :] if getattr(last_hidden, 'ndim', 0) == 3 else last_hidden[0]
        if getattr(cls_tensor, 'ndim', 0) == 2:
            cls_tensor = cls_tensor[0]

    if hasattr(cls_tensor, 'detach'):
        cls_tensor = cls_tensor.detach()
    if hasattr(cls_tensor, 'cpu'):
        cls_tensor = cls_tensor.cpu()
    if hasattr(cls_tensor, 'tolist'):
        bruto = cls_tensor.tolist()
    else:
        bruto = list(cls_tensor)

    if isinstance(bruto, list) and bruto and isinstance(bruto[0], list):
        bruto = bruto[0]
    return normalizar_vector_l2(bruto, dimension_esperada=DINOV2_EMBEDDING_DIM)


def extraer_embedding_dinov2_desde_imagen(
    imagen: Any,
    *,
    model_name: Optional[str] = None,
    device: Optional[str] = None,
) -> List[float]:
    """
    Extrae el embedding visual de 384 dimensiones normalizado L2 (token CLS)
    desde una instancia `PIL.Image.Image` usando DINOv2 Small.
    """
    if imagen is None:
        raise ValueError("Se requiere una imagen válida para extraer el embedding DINOv2.")

    imagen_rgb = imagen.convert('RGB') if hasattr(imagen, 'convert') else imagen
    processor, model, torch_mod, dev_final = _cargar_modelo_dinov2(
        model_name=model_name,
        device=device,
    )

    try:
        inputs = processor(images=imagen_rgb, return_tensors='pt')
        if dev_final != 'cpu' and hasattr(inputs, 'items'):
            inputs = {
                k: (v.to(dev_final) if hasattr(v, 'to') else v)
                for k, v in inputs.items()
            }

        with torch_mod.no_grad():
            outputs = model(**inputs)
        return _extraer_vector_cls_desde_salida(outputs)
    except ModelDinov2NoDisponibleError:
        raise
    except Exception as exc:
        raise ModelDinov2NoDisponibleError(
            f"Error ejecutando inferencia DINOv2 sobre la imagen: {exc}"
        ) from exc


def extraer_embedding_dinov2_desde_bytes(
    contenido: Optional[bytes],
    *,
    model_name: Optional[str] = None,
    device: Optional[str] = None,
    max_bytes: Optional[int] = None,
) -> Optional[List[float]]:
    """
    Valida y decodifica bytes de imagen antes de ejecutar DINOv2.
    Por defecto delega la inferencia al microservicio HTTP `VetericanoIA` para evitar
    cargar PyTorch/Transformers en Django, conservando el modo `'local'` como reversión.
    Retorna `None` si los bytes o el formato no son válidos, evitando inferencias innecesarias.
    """
    imagen = imagen_hash_service.decodificar_y_validar_imagen_bytes(
        contenido,
        max_bytes=max_bytes,
    )
    if imagen is None:
        return None

    cfg = obtener_config_dinov2()
    if cfg['backend'] == DINOV2_BACKEND_LOCAL:
        return extraer_embedding_dinov2_desde_imagen(
            imagen,
            model_name=model_name,
            device=device,
        )

    from peticiones.services import ia_client_service

    return ia_client_service.solicitar_embedding_dinov2(contenido)


def calcular_similitud_coseno(emb1: Sequence[Any], emb2: Sequence[Any]) -> float:
    """Calcula la similitud coseno en [-1.0, 1.0] entre dos embeddings visuales de 384D."""
    v1 = normalizar_vector_l2(emb1, dimension_esperada=DINOV2_EMBEDDING_DIM)
    v2 = normalizar_vector_l2(emb2, dimension_esperada=DINOV2_EMBEDDING_DIM)
    producto = sum(a * b for a, b in zip(v1, v2))
    return max(-1.0, min(1.0, float(producto)))


def calcular_porcentaje_similitud_visual(similitud_coseno: float) -> float:
    """
    Convierte una similitud coseno en porcentaje [0.0, 100.0] claramente acotado
    como métrica visual y no certeza de identidad.
    """
    acotado = max(0.0, min(1.0, float(similitud_coseno)))
    return round(acotado * 100.0, 2)


def _leer_embedding_bd(
    url_imagen: str,
    model_name: str,
    *,
    id_peticion: Optional[int] = None,
) -> Optional[List[float]]:
    if not url_imagen or len(url_imagen) > 500:
        return None
    try:
        from peticiones.models import PeticionImagenEmbedding

        qs = PeticionImagenEmbedding.objects.filter(
            url_imagen=url_imagen,
            modelo_ia=model_name,
        )
        if id_peticion is not None:
            qs = qs.filter(peticion_id=id_peticion)
        registro = qs.order_by('-fecha_creacion').first()
        if registro is None or not registro.embedding_vector:
            return None
        return normalizar_vector_l2(
            registro.embedding_vector,
            dimension_esperada=DINOV2_EMBEDDING_DIM,
        )
    except Exception as exc:
        logger.debug("Caché BD de embeddings omitida al leer (%s): %s", url_imagen, exc)
        return None


def _guardar_embedding_bd(
    url_imagen: str,
    model_name: str,
    embedding: List[float],
    *,
    id_peticion: Optional[int] = None,
) -> None:
    if id_peticion is None or not url_imagen or len(url_imagen) > 500:
        return
    try:
        from peticiones.models import PeticionImagenEmbedding

        PeticionImagenEmbedding.objects.update_or_create(
            peticion_id=id_peticion,
            url_imagen=url_imagen,
            modelo_ia=model_name,
            defaults={
                'dimensiones': len(embedding),
                'embedding_vector': embedding,
            },
        )
    except Exception as exc:
        logger.debug("Caché BD de embeddings omitida al guardar (%s): %s", url_imagen, exc)


def obtener_embedding_desde_url(
    url: Optional[str],
    *,
    cache: Optional[Dict[str, Optional[List[float]]]] = None,
    id_peticion: Optional[int] = None,
    usar_cache_bd: Optional[bool] = None,
    timeout: Optional[float] = None,
    max_bytes: Optional[int] = None,
    dominios_permitidos: Optional[Sequence[str]] = None,
) -> Optional[List[float]]:
    """
    Reutiliza una URL segura de Cloudinary, valida formato/tamaño y extrae o reutiliza
    su embedding DINOv2 Small de 384 dimensiones.
    """
    if not url or not isinstance(url, str):
        return None

    clave = url.strip()
    if not clave or detectar_tipo_archivo(clave) != 'imagen':
        return None

    if cache is not None and clave in cache:
        return cache[clave]

    cfg = obtener_config_dinov2()
    cfg_img = imagen_hash_service.obtener_config_imagen_hash()
    dominios = tuple(dominios_permitidos) if dominios_permitidos else cfg_img['dominios_permitidos']

    url_segura = imagen_hash_service.validar_url_cloudinary_segura(clave, dominios_permitidos=dominios)
    if not url_segura:
        if cache is not None:
            cache[clave] = None
        return None

    cache_bd = cfg['cache_bd_activa'] if usar_cache_bd is None else bool(usar_cache_bd)
    if cache_bd:
        emb_bd = _leer_embedding_bd(clave, cfg['model_name'], id_peticion=id_peticion)
        if emb_bd is not None:
            if cache is not None:
                cache[clave] = emb_bd
            return emb_bd

    contenido = imagen_hash_service.descargar_imagen_segura(
        url_segura,
        timeout=timeout,
        max_bytes=max_bytes,
        dominios_permitidos=dominios,
        miniatura_ancho=cfg['inferencia_ancho'],
        miniatura_alto=cfg['inferencia_alto'],
    )
    if contenido is None:
        if cache is not None:
            cache[clave] = None
        return None

    embedding = extraer_embedding_dinov2_desde_bytes(
        contenido,
        model_name=cfg['model_name'],
        device=cfg['device'],
        max_bytes=max_bytes,
    )
    if embedding is not None and cache_bd and id_peticion is not None:
        _guardar_embedding_bd(clave, cfg['model_name'], embedding, id_peticion=id_peticion)

    if cache is not None:
        cache[clave] = embedding
    return embedding


def precargar_embeddings_bd(
    urls: Iterable[str],
    *,
    cache: Optional[Dict[str, Optional[List[float]]]] = None,
    id_peticiones: Optional[Iterable[int]] = None,
    usar_cache_bd: Optional[bool] = None,
) -> Dict[str, List[float]]:
    """
    Precarga en una sola consulta SQL los embeddings cacheados en PostgreSQL para
    un conjunto de URLs (opcionalmente acotado por `id_peticiones`), evitando N+1 queries
    y llamadas repetidas a VetericanoIA.
    """
    cfg = obtener_config_dinov2()
    cache_bd = cfg['cache_bd_activa'] if usar_cache_bd is None else bool(usar_cache_bd)
    if not cache_bd:
        return {}

    urls_pendientes: List[str] = []
    vistas = set()
    for raw in urls or ():
        if not raw or not isinstance(raw, str):
            continue
        limpia = raw.strip()
        if not limpia or len(limpia) > 500 or limpia in vistas:
            continue
        if cache is not None and limpia in cache:
            continue
        if detectar_tipo_archivo(limpia) != 'imagen':
            continue
        vistas.add(limpia)
        urls_pendientes.append(limpia)

    if not urls_pendientes:
        return {}

    ids_limpios = []
    for pid in id_peticiones or ():
        try:
            ids_limpios.append(int(pid))
        except (TypeError, ValueError):
            continue

    encontrados: Dict[str, List[float]] = {}
    try:
        from peticiones.models import PeticionImagenEmbedding

        qs = PeticionImagenEmbedding.objects.filter(
            url_imagen__in=urls_pendientes,
            modelo_ia=cfg['model_name'],
        )
        if ids_limpios:
            qs = qs.filter(peticion_id__in=ids_limpios)

        for registro in qs.order_by('-fecha_creacion'):
            url_reg = getattr(registro, 'url_imagen', None)
            vec_raw = getattr(registro, 'embedding_vector', None)
            if not url_reg or url_reg in encontrados or not vec_raw:
                continue
            try:
                emb_norm = normalizar_vector_l2(vec_raw, dimension_esperada=DINOV2_EMBEDDING_DIM)
            except ValueError:
                continue
            encontrados[url_reg] = emb_norm
            if cache is not None:
                cache[url_reg] = emb_norm
    except Exception as exc:
        logger.debug("Precarga batch de embeddings BD omitida: %s", exc)
    return encontrados


def calcular_embeddings_urls(
    urls: Iterable[str],
    *,
    max_imagenes: Optional[int] = None,
    cache: Optional[Dict[str, Optional[List[float]]]] = None,
    id_peticion: Optional[int] = None,
    usar_cache_bd: Optional[bool] = None,
) -> List[List[float]]:
    """
    Extrae embeddings DINOv2 únicos y válidos desde URLs Cloudinary,
    respetando el límite de imágenes por petición y filtrando no-imágenes.
    Cuando hay múltiples imágenes sin caché, paraleliza la descarga Cloudinary en I/O
    acotado y mantiene las operaciones BD/caché en el hilo actual.
    """
    cfg = obtener_config_dinov2()
    cfg_img = imagen_hash_service.obtener_config_imagen_hash()
    limite = max_imagenes if max_imagenes is not None else cfg_img['max_imagenes']

    urls_limpias: List[str] = []
    vistas = set()
    for raw in urls or ():
        if not raw or not isinstance(raw, str):
            continue
        limpia = raw.strip()
        if not limpia or limpia in vistas:
            continue
        if detectar_tipo_archivo(limpia) != 'imagen':
            continue
        vistas.add(limpia)
        urls_limpias.append(limpia)
        if len(urls_limpias) >= limite:
            break

    if not urls_limpias:
        return []

    cache_trabajo = cache if cache is not None else {}
    cache_bd = cfg['cache_bd_activa'] if usar_cache_bd is None else bool(usar_cache_bd)
    if cache_bd and len(urls_limpias) > 1:
        precargar_embeddings_bd(
            urls_limpias,
            cache=cache_trabajo,
            id_peticiones=[id_peticion] if id_peticion is not None else None,
            usar_cache_bd=usar_cache_bd,
        )

    pendientes_descarga: List[Tuple[str, str]] = []
    dominios = cfg_img['dominios_permitidos']
    for url in urls_limpias:
        if url in cache_trabajo:
            continue
        url_segura = imagen_hash_service.validar_url_cloudinary_segura(url, dominios_permitidos=dominios)
        if not url_segura:
            cache_trabajo[url] = None
            continue
        if cache_bd:
            emb_bd = _leer_embedding_bd(url, cfg['model_name'], id_peticion=id_peticion)
            if emb_bd is not None:
                cache_trabajo[url] = emb_bd
                continue
        pendientes_descarga.append((url, url_segura))

    if len(pendientes_descarga) == 1:
        url_unica, url_segura_unica = pendientes_descarga[0]
        contenido = imagen_hash_service.descargar_imagen_segura(
            url_segura_unica,
            dominios_permitidos=dominios,
            miniatura_ancho=cfg['inferencia_ancho'],
            miniatura_alto=cfg['inferencia_alto'],
        )
        if contenido is None:
            cache_trabajo[url_unica] = None
        else:
            emb = extraer_embedding_dinov2_desde_bytes(
                contenido,
                model_name=cfg['model_name'],
                device=cfg['device'],
            )
            if emb is not None and cache_bd and id_peticion is not None:
                _guardar_embedding_bd(url_unica, cfg['model_name'], emb, id_peticion=id_peticion)
            cache_trabajo[url_unica] = emb
    elif len(pendientes_descarga) > 1:
        # Solo descargamos bytes de Cloudinary en paralelo; las llamadas BD y el retorno a
        # VetericanoIA permanecen en el hilo invocador.
        def _descargar(par: Tuple[str, str]) -> Tuple[str, Optional[bytes]]:
            _, segura = par
            return (
                par[0],
                imagen_hash_service.descargar_imagen_segura(
                    segura,
                    dominios_permitidos=dominios,
                    miniatura_ancho=cfg['inferencia_ancho'],
                    miniatura_alto=cfg['inferencia_alto'],
                ),
            )

        descargas: Dict[str, Optional[bytes]] = {}
        with ThreadPoolExecutor(max_workers=min(3, len(pendientes_descarga))) as pool:
            for url_orig, contenido in pool.map(_descargar, pendientes_descarga):
                descargas[url_orig] = contenido

        for url_orig, _ in pendientes_descarga:
            contenido = descargas.get(url_orig)
            if contenido is None:
                cache_trabajo[url_orig] = None
                continue
            emb = extraer_embedding_dinov2_desde_bytes(
                contenido,
                model_name=cfg['model_name'],
                device=cfg['device'],
            )
            if emb is not None and cache_bd and id_peticion is not None:
                _guardar_embedding_bd(url_orig, cfg['model_name'], emb, id_peticion=id_peticion)
            cache_trabajo[url_orig] = emb

    embeddings: List[List[float]] = []
    for url in urls_limpias:
        emb = cache_trabajo.get(url)
        if emb is not None:
            embeddings.append(emb)
    return embeddings


def comparar_conjuntos_embeddings(
    embeddings_origen: Sequence[Sequence[Any]],
    embeddings_candidato: Sequence[Sequence[Any]],
    *,
    umbral_coseno: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compara múltiples embeddings visuales DINOv2 de la petición consultada frente a una
    petición candidata, conservando la máxima similitud coseno encontrada.
    """
    cfg = obtener_config_dinov2()
    umbral = cfg['umbral_dinov2'] if umbral_coseno is None else _leer_float_rango(
        umbral_coseno,
        cfg['umbral_dinov2'],
        0.0,
        1.0,
    )

    base = {
        'comparado': False,
        'coincidencia_imagen': False,
        'similitud_coseno': None,
        'similitud_imagen': None,
        'porcentaje_similitud_visual': None,
        'metodo_imagen': 'dinov2_small',
        'es_certeza_identidad': False,
        'tipo_metrica_visual': TIPO_METRICA_VISUAL,
        'advertencia_metrica_visual': ADVERTENCIA_METRICA_VISUAL_DEFAULT,
    }

    if not embeddings_origen or not embeddings_candidato:
        return base

    mejor_coseno: Optional[float] = None
    for e1 in embeddings_origen:
        for e2 in embeddings_candidato:
            try:
                sim = calcular_similitud_coseno(e1, e2)
            except ValueError:
                continue
            if mejor_coseno is None or sim > mejor_coseno:
                mejor_coseno = sim

    if mejor_coseno is None:
        return base

    sim_normalizada = max(0.0, min(1.0, float(mejor_coseno)))
    return {
        **base,
        'comparado': True,
        'coincidencia_imagen': mejor_coseno >= umbral,
        'similitud_coseno': round(mejor_coseno, 4),
        'similitud_imagen': round(sim_normalizada, 4),
        'porcentaje_similitud_visual': calcular_porcentaje_similitud_visual(mejor_coseno),
    }
