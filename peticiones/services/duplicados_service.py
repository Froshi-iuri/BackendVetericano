"""
Servicio de detección de posibles reportes duplicados para el módulo de Peticiones.

- Fase 1: comparación textual mediante PostgreSQL `pg_trgm` (`similarity()`).
- Fase 2: comparación geográfica mediante coordenadas GPS de `Ubicaciones`,
  prefiltro SQL *bounding box* y cálculo exacto de distancia con Haversine en metros.
- Fase 3: comparación visual perceptual mediante dHash 64-bit con Pillow y distancia
  de Hamming sobre fotografías Cloudinary únicamente entre candidatos preseleccionados.

Devuelve únicamente advertencias y candidatos ordenados para revisión humana; nunca
bloquea, fusiona ni elimina reportes.
"""

import logging
import math
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from django.conf import settings
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import FloatField, Value
from django.db.models.functions import Coalesce, Length, Trim
from django.utils import timezone

from peticiones.models import Peticiones
from peticiones.services import dinov2_service
from peticiones.services.imagen_hash_service import (
    UMBRAL_HAMMING_DEFAULT,
    calcular_hashes_urls,
    comparar_conjuntos_hashes,
    obtener_config_imagen_hash,
)

logger = logging.getLogger(__name__)

UMBRAL_SIMILITUD_DEFAULT = 0.35
DIAS_VENTANA_DEFAULT = 30
MIN_LONGITUD_DESCRIPCION_DEFAULT = 10
MAX_CANDIDATOS_DEFAULT = 10
MAX_CANDIDATOS_TOPE = 50

RADIO_GPS_METROS_DEFAULT = 200.0
MAX_RADIO_GPS_METROS = 50000.0
MAX_CANDIDATOS_SQL_DEFAULT = 50
MAX_CANDIDATOS_SQL_TOPE = 100

RADIO_TIERRA_METROS = 6371000.0
METROS_POR_GRADO_LAT = 111320.0
MARGEN_BOUNDING_BOX = 1.02


class PeticionNoEncontradaError(LookupError):
    """Se lanza cuando se consulta desde un id_peticion de referencia inexistente."""


def obtener_config_duplicados() -> Dict[str, Any]:
    """
    Obtiene los parámetros configurables para la detección por texto y GPS.
    Permite sobrescribir valores desde `django.conf.settings` manteniendo defaults seguros.
    """
    try:
        umbral = float(getattr(settings, 'DUPLICADOS_UMBRAL_DESCRIPCION', UMBRAL_SIMILITUD_DEFAULT))
    except (TypeError, ValueError):
        umbral = UMBRAL_SIMILITUD_DEFAULT
    if not (0.0 < umbral <= 1.0):
        umbral = UMBRAL_SIMILITUD_DEFAULT

    try:
        dias = int(getattr(settings, 'DUPLICADOS_DIAS_VENTANA', DIAS_VENTANA_DEFAULT))
    except (TypeError, ValueError):
        dias = DIAS_VENTANA_DEFAULT
    dias = max(1, dias)

    try:
        min_longitud = int(getattr(settings, 'DUPLICADOS_MIN_LONGITUD_DESCRIPCION', MIN_LONGITUD_DESCRIPCION_DEFAULT))
    except (TypeError, ValueError):
        min_longitud = MIN_LONGITUD_DESCRIPCION_DEFAULT
    min_longitud = max(1, min_longitud)

    try:
        limite = int(getattr(settings, 'DUPLICADOS_MAX_CANDIDATOS', MAX_CANDIDATOS_DEFAULT))
    except (TypeError, ValueError):
        limite = MAX_CANDIDATOS_DEFAULT
    limite = max(1, min(limite, MAX_CANDIDATOS_TOPE))

    try:
        radio_metros = float(getattr(settings, 'DUPLICADOS_RADIO_GPS_METROS', RADIO_GPS_METROS_DEFAULT))
    except (TypeError, ValueError):
        radio_metros = RADIO_GPS_METROS_DEFAULT
    if not math.isfinite(radio_metros) or not (1.0 <= radio_metros <= MAX_RADIO_GPS_METROS):
        radio_metros = RADIO_GPS_METROS_DEFAULT

    try:
        limite_sql = int(getattr(settings, 'DUPLICADOS_MAX_CANDIDATOS_SQL', MAX_CANDIDATOS_SQL_DEFAULT))
    except (TypeError, ValueError):
        limite_sql = MAX_CANDIDATOS_SQL_DEFAULT
    limite_sql = max(limite, min(limite_sql, MAX_CANDIDATOS_SQL_TOPE))

    cfg_img = obtener_config_imagen_hash()
    cfg_dinov2 = dinov2_service.obtener_config_dinov2()

    return {
        'umbral': umbral,
        'dias': dias,
        'min_longitud': min_longitud,
        'limite': limite,
        'radio_metros': radio_metros,
        'limite_sql': limite_sql,
        'umbral_hamming': cfg_img['umbral_hamming'],
        'max_imagenes_por_peticion': cfg_img['max_imagenes'],
        'max_candidatos_imagen': cfg_img['max_candidatos'],
        'imagen_timeout': cfg_img['timeout'],
        'imagen_max_bytes': cfg_img['max_bytes'],
        'dinov2_activo': cfg_dinov2['ia_activa'],
        'umbral_dinov2': cfg_dinov2['umbral_dinov2'],
        'dinov2_model_name': cfg_dinov2['model_name'],
        'dinov2_fallback_dhash': cfg_dinov2['fallback_dhash'],
    }


def normalizar_descripcion(descripcion: Optional[str]) -> str:
    """Normaliza espacios en blanco extremos e internos para comparar descripciones."""
    if descripcion is None:
        return ''
    return ' '.join(str(descripcion).split())


def es_descripcion_valida_para_comparacion(
    descripcion: Optional[str],
    min_longitud: Optional[int] = None,
) -> bool:
    """
    Determina si la descripción tiene contenido suficiente para compararse con `pg_trgm`.
    Ignora descripciones nulas, vacías o demasiado cortas.
    """
    min_len = min_longitud if min_longitud is not None else obtener_config_duplicados()['min_longitud']
    texto = normalizar_descripcion(descripcion)
    return len(texto) >= min_len


def _parse_float_finito(valor: Any) -> Optional[float]:
    """Convierte valores numéricos a float finito, rechazando booleanos, nulos o NaN/Inf."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, str):
        valor = valor.strip()
        if not valor:
            return None
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def normalizar_coordenadas(latitud: Any, longitud: Any) -> Optional[Tuple[float, float]]:
    """
    Valida y normaliza coordenadas GPS.
    Ignora valores nulos, no numéricos, fuera de rango o el centinela `(0, 0)`.
    """
    lat = _parse_float_finito(latitud)
    lon = _parse_float_finito(longitud)
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    if abs(lat) < 1e-7 and abs(lon) < 1e-7:
        return None
    return (lat, lon)


def es_coordenada_valida(latitud: Any, longitud: Any) -> bool:
    """Indica si el par (latitud, longitud) corresponde a una ubicación GPS válida."""
    return normalizar_coordenadas(latitud, longitud) is not None


def calcular_distancia_haversine_metros(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Calcula la distancia geodésica real en metros entre dos coordenadas mediante Haversine."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(d_lambda / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return RADIO_TIERRA_METROS * c


def calcular_bounding_box(
    latitud: float,
    longitud: float,
    radio_metros: float,
) -> Dict[str, Optional[Decimal]]:
    """
    Calcula una caja delimitadora (*bounding box*) en `Decimal(10,7)` para prefiltrar en SQL
    antes de aplicar Haversine en Python.
    """
    delta_lat = (radio_metros / METROS_POR_GRADO_LAT) * MARGEN_BOUNDING_BOX
    cos_lat = max(abs(math.cos(math.radians(latitud))), 1e-6)
    delta_lon = (radio_metros / (METROS_POR_GRADO_LAT * cos_lat)) * MARGEN_BOUNDING_BOX

    lat_min = max(-90.0, latitud - delta_lat)
    lat_max = min(90.0, latitud + delta_lat)
    lon_min_raw = longitud - delta_lon
    lon_max_raw = longitud + delta_lon

    def _to_dec(val: float) -> Decimal:
        return Decimal(f"{val:.7f}")

    cruza_antimeridiano = lon_min_raw < -180.0 or lon_max_raw > 180.0
    return {
        'lat_min': _to_dec(lat_min),
        'lat_max': _to_dec(lat_max),
        'lon_min': None if cruza_antimeridiano else _to_dec(lon_min_raw),
        'lon_max': None if cruza_antimeridiano else _to_dec(lon_max_raw),
    }


def extraer_fotografias_peticion(peticion: Peticiones) -> Tuple[Optional[str], List[str]]:
    """
    Extrae las URLs de fotografía disponibles de una petición combinando `evidenciapeticiones_set`
    y el campo legado `Peticiones.foto` sin provocar consultas N+1 cuando está prefetched.
    """
    urls: List[str] = []
    vistas = set()

    evidencias_cache = getattr(peticion, '_prefetched_objects_cache', {}).get('evidenciapeticiones_set')
    if evidencias_cache is not None:
        for evidencia in evidencias_cache:
            ruta = getattr(evidencia, 'ruta_archivo', None)
            if ruta:
                ruta_limpia = str(ruta).strip()
                if ruta_limpia and ruta_limpia not in vistas:
                    vistas.add(ruta_limpia)
                    urls.append(ruta_limpia)
    else:
        for ruta in (
            peticion.evidenciapeticiones_set
            .exclude(ruta_archivo__isnull=True)
            .exclude(ruta_archivo='')
            .values_list('ruta_archivo', flat=True)
        ):
            ruta_limpia = str(ruta).strip()
            if ruta_limpia and ruta_limpia not in vistas:
                vistas.add(ruta_limpia)
                urls.append(ruta_limpia)

    foto_campo = getattr(peticion, 'foto', None)
    foto_campo_limpia = str(foto_campo).strip() if foto_campo else None
    if foto_campo_limpia and foto_campo_limpia not in vistas:
        urls.insert(0, foto_campo_limpia)

    foto_principal = foto_campo_limpia or (urls[0] if urls else None)
    return foto_principal, urls


def _normalizar_lista_urls_origen(
    foto: Optional[str] = None,
    fotos: Optional[Sequence[Any]] = None,
) -> List[str]:
    """Normaliza y deduplica las URLs de fotografía recibidas para la petición consultada."""
    urls: List[str] = []
    vistas = set()

    candidatos_raw: List[Any] = []
    if foto is not None:
        candidatos_raw.append(foto)
    if fotos is not None:
        if isinstance(fotos, str):
            candidatos_raw.append(fotos)
        else:
            candidatos_raw.extend(fotos)

    for item in candidatos_raw:
        if not isinstance(item, str):
            continue
        limpia = item.strip()
        if limpia and limpia not in vistas:
            vistas.add(limpia)
            urls.append(limpia)
    return urls


def _extraer_coordenadas_peticion(peticion: Peticiones) -> Optional[Tuple[float, float]]:
    """Obtiene coordenadas válidas desde `Peticiones.id_ubicacion` si existen."""
    ubicacion = getattr(peticion, 'id_ubicacion', None)
    if ubicacion is None:
        return None
    return normalizar_coordenadas(
        getattr(ubicacion, 'latitud', None),
        getattr(ubicacion, 'longitud', None),
    )


def _calcular_puntaje_combinado(
    *,
    similitud_texto: float,
    coincidencia_texto: bool,
    distancia_metros: Optional[float],
    coincidencia_gps: bool,
    radio_metros: float,
    gps_activo: bool,
    similitud_imagen: Optional[float] = None,
    coincidencia_imagen: bool = False,
    priorizar_visual_gps: bool = False,
) -> float:
    """
    Calcula el score combinado [0, 1] entre texto (`pg_trgm`), GPS (`Haversine`)
    e imagen (`DINOv2` o `dHash`). Cuando `priorizar_visual_gps=True`, prioriza imagen y GPS
    y mantiene la descripción como señal complementaria.
    """
    score_gps = 0.0
    if gps_activo and distancia_metros is not None and radio_metros > 0:
        if coincidencia_gps:
            score_gps = max(0.0, min(1.0, 1.0 - (distancia_metros / radio_metros)))
        elif distancia_metros <= radio_metros * 2:
            score_gps = max(0.0, 0.25 * (1.0 - (distancia_metros / (radio_metros * 2))))

    if priorizar_visual_gps:
        score_txt = max(0.0, min(1.0, float(similitud_texto or 0.0)))
        if similitud_imagen is not None:
            score_img = max(0.0, min(1.0, float(similitud_imagen)))
            if coincidencia_imagen:
                if coincidencia_gps and gps_activo:
                    score = (0.65 * score_img) + (0.30 * score_gps) + (0.05 * score_txt)
                else:
                    score = (0.90 * score_img) + (0.10 * score_txt)
                return round(max(0.0, min(1.0, score)), 4)

            if coincidencia_gps and gps_activo:
                score = (0.55 * score_gps) + (0.35 * score_img) + (0.10 * score_txt)
            else:
                score = (0.35 * score_img) + (0.10 * score_txt)
            return round(max(0.0, min(1.0, score)), 4)

        if coincidencia_gps and gps_activo:
            score = (0.85 * score_gps) + (0.15 * score_txt)
            return round(max(0.0, min(1.0, score)), 4)
        if coincidencia_texto:
            return round(score_txt, 4)
        return 0.0

    if coincidencia_imagen and similitud_imagen is not None:
        score_img = max(0.0, min(1.0, float(similitud_imagen)))
        componentes = []
        if coincidencia_texto:
            componentes.append(similitud_texto)
        if coincidencia_gps and gps_activo:
            componentes.append(score_gps)
        componentes.append(score_img)
        return round(sum(componentes) / len(componentes), 4)

    if not gps_activo:
        return round(similitud_texto, 4)

    if coincidencia_texto and coincidencia_gps:
        return round((similitud_texto + score_gps) / 2.0, 4)
    if coincidencia_texto:
        return round(max(similitud_texto, score_gps), 4)
    if coincidencia_gps:
        return round(max(score_gps, similitud_texto), 4)
    return 0.0


def _serializar_candidato(
    peticion: Peticiones,
    *,
    similitud_texto: float,
    coincidencia_texto: bool,
    distancia_metros: Optional[float],
    coincidencia_gps: bool,
    coincidencia_imagen: bool,
    distancia_hamming: Optional[int],
    similitud_imagen: Optional[float],
    score_combinado: float,
    incluir_evidencias: bool = True,
    porcentaje_similitud_visual: Optional[float] = None,
    similitud_visual_coseno: Optional[float] = None,
    metodo_imagen: Optional[str] = None,
    es_certeza_identidad: bool = False,
    tipo_metrica_visual: Optional[str] = None,
    advertencia_metrica_visual: Optional[str] = None,
    incluir_meta_dinov2: bool = False,
) -> Dict[str, Any]:
    """Construye la representación JSON de una petición candidata a duplicado."""
    similitud_float = max(0.0, min(1.0, float(similitud_texto or 0.0)))
    similitud_redondeada = round(similitud_float, 4)
    estado_obj = getattr(peticion, 'id_estado', None)
    ubicacion_obj = getattr(peticion, 'id_ubicacion', None)
    if incluir_evidencias:
        foto_principal, fotos_disponibles = extraer_fotografias_peticion(peticion)
    else:
        foto_principal, fotos_disponibles = None, []
    fecha_val = getattr(peticion, 'fecha', None)

    criterios: List[str] = []
    if coincidencia_texto:
        criterios.append('texto')
    if coincidencia_gps:
        criterios.append('gps')
    if coincidencia_imagen:
        criterios.append('imagen')

    coords_cand = _extraer_coordenadas_peticion(peticion)
    ubicacion_data = None
    if ubicacion_obj is not None:
        ubicacion_data = {
            'direccion': getattr(ubicacion_obj, 'direccion', None),
            'latitud': coords_cand[0] if coords_cand else None,
            'longitud': coords_cand[1] if coords_cand else None,
        }

    resultado = {
        'id_peticion': peticion.id_peticion,
        'numero_radicado': peticion.numero_radicado,
        'descripcion': peticion.descripcion,
        'fecha': fecha_val.isoformat() if hasattr(fecha_val, 'isoformat') else fecha_val,
        'similitud': similitud_redondeada,
        'similitud_texto': similitud_redondeada,
        'porcentaje_similitud': round(similitud_float * 100.0, 2),
        'coincidencia_texto': coincidencia_texto,
        'distancia_metros': round(float(distancia_metros), 2) if distancia_metros is not None else None,
        'coincidencia_gps': coincidencia_gps,
        'coincidencia_imagen': bool(coincidencia_imagen),
        'distancia_hamming': int(distancia_hamming) if distancia_hamming is not None else None,
        'similitud_imagen': round(float(similitud_imagen), 4) if similitud_imagen is not None else None,
        'posible_duplicado': (
            bool(coincidencia_gps)
            if incluir_meta_dinov2
            else bool(coincidencia_texto or coincidencia_gps or coincidencia_imagen)
        ),
        'criterios_coincidencia': criterios,
        'score_combinado': score_combinado,
        'id_estado': getattr(peticion, 'id_estado_id', None),
        'estado': getattr(estado_obj, 'nombre', None) if estado_obj else None,
        'ubicacion': ubicacion_data,
        'foto': foto_principal,
        'fotos': fotos_disponibles,
        'tiene_fotografia': bool(foto_principal or fotos_disponibles),
    }

    if incluir_meta_dinov2:
        resultado.update({
            'porcentaje_similitud_visual': (
                round(float(porcentaje_similitud_visual), 2)
                if porcentaje_similitud_visual is not None
                else None
            ),
            'similitud_visual_coseno': (
                round(float(similitud_visual_coseno), 4)
                if similitud_visual_coseno is not None
                else None
            ),
            'metodo_imagen': metodo_imagen,
            'es_certeza_identidad': bool(es_certeza_identidad),
            'tipo_metrica_visual': tipo_metrica_visual,
            'advertencia_metrica_visual': advertencia_metrica_visual,
        })

    return resultado


def _construir_respuesta(
    candidatos: List[Dict[str, Any]],
    *,
    umbral: float,
    radio_metros: float,
    dias: int,
    min_longitud: int,
    limite: int,
    excluir_id_peticion: Optional[int],
    texto_activo: bool,
    gps_activo: bool,
    mensaje: str,
    imagen_activo: bool = False,
    umbral_hamming: int = UMBRAL_HAMMING_DEFAULT,
    dinov2_activo: Optional[bool] = None,
    umbral_dinov2: Optional[float] = None,
    metodo_imagen: Optional[str] = None,
) -> Dict[str, Any]:
    hay_duplicados = len(candidatos) > 0
    parametros: Dict[str, Any] = {
        'umbral_similitud': umbral,
        'radio_metros': radio_metros,
        'dias_ventana': dias,
        'longitud_minima_descripcion': min_longitud,
        'limite': limite,
        'id_peticion_excluido': excluir_id_peticion,
        'texto_activo': texto_activo,
        'gps_activo': gps_activo,
        'imagen_activa': imagen_activo,
        'umbral_hamming': umbral_hamming,
    }
    if dinov2_activo is not None:
        parametros['dinov2_activo'] = bool(dinov2_activo)
        parametros['umbral_dinov2'] = umbral_dinov2
        parametros['metodo_imagen'] = metodo_imagen

    return {
        'advertencia_duplicado': hay_duplicados,
        'posible_duplicado': hay_duplicados,
        'total_candidatos': len(candidatos),
        'posibles_duplicados': candidatos,
        'mensaje': mensaje,
        'parametros': parametros,
    }


def _comparar_con_dhash(
    preseleccionados: List[Tuple[Tuple[Any, ...], Dict[str, Any]]],
    *,
    urls_origen: List[str],
    max_imagenes_pet: int,
    max_candidatos_img: int,
    umbral_hamming_val: int,
) -> bool:
    """Ejecuta la comparación visual dHash 64-bit sobre candidatos preseleccionados."""
    hashes_origen: List[int] = []
    cache_hashes: Dict[str, Optional[int]] = {}
    try:
        hashes_origen = calcular_hashes_urls(
            urls_origen,
            max_imagenes=max_imagenes_pet,
            cache=cache_hashes,
        )
    except Exception as exc:
        logger.warning("Comparación visual omitida por error procesando fotos de origen: %s", exc)
        hashes_origen = []

    if not hashes_origen:
        return False

    candidatos_img_procesados = 0
    for _, item in preseleccionados:
        if item.get('metodo_imagen') == 'dinov2_small':
            continue
        if candidatos_img_procesados >= max_candidatos_img:
            break

        pet = item['peticion']
        _, fotos_cand = extraer_fotografias_peticion(pet)
        if not fotos_cand:
            continue

        candidatos_img_procesados += 1
        try:
            hashes_cand = calcular_hashes_urls(
                fotos_cand,
                max_imagenes=max_imagenes_pet,
                cache=cache_hashes,
            )
            if hashes_cand:
                comp = comparar_conjuntos_hashes(
                    hashes_origen,
                    hashes_cand,
                    umbral_hamming=umbral_hamming_val,
                )
                if comp.get('comparado'):
                    item['coincidencia_imagen'] = bool(comp['coincidencia_imagen'])
                    item['distancia_hamming'] = comp['distancia_hamming']
                    item['similitud_imagen'] = comp['similitud_imagen']
                    item['metodo_imagen'] = item.get('metodo_imagen') or 'dhash'
        except Exception as exc:
            logger.warning(
                "Fallo controlado comparando fotografías para petición %s: %s",
                getattr(pet, 'id_peticion', '?'),
                exc,
            )
    return True


def _ejecutar_comparacion_visual(
    preseleccionados: List[Tuple[Tuple[Any, ...], Dict[str, Any]]],
    *,
    urls_origen: List[str],
    dinov2_habilitado: bool,
    umbral_dinov2_val: float,
    umbral_hamming_val: int,
    max_imagenes_pet: int,
    max_candidatos_img: int,
    dinov2_fallback_dhash: bool,
) -> Tuple[bool, Optional[str]]:
    """
    Orquesta la comparación visual de la Fase 3: usa DINOv2 Small cuando está habilitado
    y recurre automáticamente a dHash 64-bit como respaldo ante fallos del modelo.
    """
    if not urls_origen or not preseleccionados:
        return False, None

    if dinov2_habilitado:
        cache_embeddings: Dict[str, Optional[List[float]]] = {}
        precargar_fn = getattr(dinov2_service, 'precargar_embeddings_bd', None)
        if callable(precargar_fn):
            urls_precarga: List[str] = list(urls_origen)
            ids_precarga: List[int] = []
            candidatos_vis = 0
            for _, item in preseleccionados:
                if candidatos_vis >= max_candidatos_img:
                    break
                pet = item['peticion']
                _, fotos_cand = extraer_fotografias_peticion(pet)
                if not fotos_cand:
                    continue
                candidatos_vis += 1
                urls_precarga.extend(fotos_cand[:max_imagenes_pet])
                pid = getattr(pet, 'id_peticion', None)
                if pid is not None:
                    ids_precarga.append(pid)
            precargar_fn(urls_precarga, cache=cache_embeddings, id_peticiones=ids_precarga)

        try:
            embeddings_origen = dinov2_service.calcular_embeddings_urls(
                urls_origen,
                max_imagenes=max_imagenes_pet,
                cache=cache_embeddings,
            )
        except dinov2_service.ModelDinov2NoDisponibleError as exc:
            logger.warning("DINOv2 no disponible procesando fotos de origen; usando respaldo dHash=%s: %s", dinov2_fallback_dhash, exc)
            embeddings_origen = []
            dinov2_fallo = True
        except Exception as exc:
            logger.warning("Fallo inesperado en DINOv2 para fotos de origen; usando respaldo dHash=%s: %s", dinov2_fallback_dhash, exc)
            embeddings_origen = []
            dinov2_fallo = True
        else:
            dinov2_fallo = False

        if not dinov2_fallo and embeddings_origen:
            candidatos_img_procesados = 0
            for _, item in preseleccionados:
                if candidatos_img_procesados >= max_candidatos_img:
                    break

                pet = item['peticion']
                _, fotos_cand = extraer_fotografias_peticion(pet)
                if not fotos_cand:
                    logger.debug(
                        "Candidato petición #%s sin URLs de imagen para comparar con DINOv2.",
                        getattr(pet, 'id_peticion', '?'),
                    )
                    continue

                candidatos_img_procesados += 1
                try:
                    emb_cand = dinov2_service.calcular_embeddings_urls(
                        fotos_cand,
                        max_imagenes=max_imagenes_pet,
                        cache=cache_embeddings,
                        id_peticion=getattr(pet, 'id_peticion', None),
                    )
                except dinov2_service.ModelDinov2NoDisponibleError as exc:
                    logger.warning(
                        "Fallo de modelo DINOv2 comparando candidato %s; activando respaldo: %s",
                        getattr(pet, 'id_peticion', '?'),
                        exc,
                    )
                    dinov2_fallo = True
                    break
                except Exception as exc:
                    logger.warning(
                        "Error controlado procesando embeddings DINOv2 de candidato %s: %s",
                        getattr(pet, 'id_peticion', '?'),
                        exc,
                    )
                    continue

                if emb_cand:
                    comp = dinov2_service.comparar_conjuntos_embeddings(
                        embeddings_origen,
                        emb_cand,
                        umbral_coseno=umbral_dinov2_val,
                    )
                    if comp.get('comparado'):
                        item['coincidencia_imagen'] = bool(comp['coincidencia_imagen'])
                        item['similitud_imagen'] = comp['similitud_imagen']
                        item['porcentaje_similitud_visual'] = comp['porcentaje_similitud_visual']
                        item['similitud_visual_coseno'] = comp['similitud_coseno']
                        item['metodo_imagen'] = 'dinov2_small'
                        item['es_certeza_identidad'] = False
                        item['tipo_metrica_visual'] = comp['tipo_metrica_visual']
                        item['advertencia_metrica_visual'] = comp['advertencia_metrica_visual']
                else:
                    logger.warning(
                        "No se obtuvieron embeddings DINOv2 válidos para candidato #%s.",
                        getattr(pet, 'id_peticion', '?'),
                    )

            logger.info(
                "Comparación visual DINOv2 finalizada: origen_embs=%d, preseleccionados=%d, procesados=%d.",
                len(embeddings_origen),
                len(preseleccionados),
                candidatos_img_procesados,
            )
            if not dinov2_fallo:
                return True, 'dinov2_small'

        if not dinov2_fallo:
            return False, None
        for _, item in preseleccionados:
            if item.get('metodo_imagen') != 'dinov2_small':
                item['metodo_imagen'] = 'dhash_fallback' if dinov2_fallback_dhash else 'dinov2_no_disponible'
                item['porcentaje_similitud_visual'] = None
                item['similitud_visual_coseno'] = None
        if not dinov2_fallback_dhash:
            return False, None

        dhash_ok = _comparar_con_dhash(
            preseleccionados,
            urls_origen=urls_origen,
            max_imagenes_pet=max_imagenes_pet,
            max_candidatos_img=max_candidatos_img,
            umbral_hamming_val=umbral_hamming_val,
        )
        return dhash_ok, ('dhash_fallback' if dhash_ok else None)

    dhash_ok = _comparar_con_dhash(
        preseleccionados,
        urls_origen=urls_origen,
        max_imagenes_pet=max_imagenes_pet,
        max_candidatos_img=max_candidatos_img,
        umbral_hamming_val=umbral_hamming_val,
    )
    return dhash_ok, ('dhash' if dhash_ok else None)


def buscar_peticiones_similares_por_descripcion(
    descripcion: Optional[str] = None,
    *,
    latitud: Any = None,
    longitud: Any = None,
    foto: Optional[str] = None,
    fotos: Optional[Sequence[str]] = None,
    excluir_id_peticion: Optional[int] = None,
    umbral: Optional[float] = None,
    radio_metros: Optional[float] = None,
    umbral_hamming: Optional[int] = None,
    umbral_dinov2: Optional[float] = None,
    usar_dinov2: Optional[bool] = None,
    dias: Optional[int] = None,
    min_longitud: Optional[int] = None,
    limite: Optional[int] = None,
    incluir_imagenes: bool = True,
    incluir_evidencias: bool = True,
) -> Dict[str, Any]:
    """
    Busca peticiones existentes candidatas a duplicado combinando:
    1. Similitud textual (`pg_trgm` `similarity()`, umbral configurable default `0.35`).
    2. Proximidad GPS sobre `Ubicaciones` (prefiltro *bounding box* + Haversine en metros,
       radio configurable default `200` m).
    3. Comparación visual mediante DINOv2 Small (cuando está habilitado) o perceptual dHash
       64-bit como predeterminado/respaldo sobre fotografías Cloudinary.

    - Cuando DINOv2 está activo, prioriza coincidencia visual e inmediación GPS, manteniendo
      la descripción como señal complementaria y permitiendo consulta puramente visual.
    - Cuando DINOv2 está desactivado, conserva intacto el flujo y puntuación de Fases 1-3.
    """
    config = obtener_config_duplicados()
    dinov2_habilitado = dinov2_service.es_dinov2_activo(usar_dinov2)
    umbral_dinov2_val = (
        max(0.0, min(1.0, float(umbral_dinov2)))
        if umbral_dinov2 is not None
        else config['umbral_dinov2']
    )
    dinov2_fallback_dhash = bool(config['dinov2_fallback_dhash'])

    umbral_val = float(umbral) if umbral is not None else config['umbral']
    radio_val = float(radio_metros) if radio_metros is not None else config['radio_metros']
    umbral_hamming_val = (
        max(0, min(int(umbral_hamming), 64))
        if umbral_hamming is not None
        else config['umbral_hamming']
    )
    max_imagenes_pet = config['max_imagenes_por_peticion']
    max_candidatos_img = config['max_candidatos_imagen']
    dias_val = int(dias) if dias is not None else config['dias']
    min_len = int(min_longitud) if min_longitud is not None else config['min_longitud']
    limite_val = max(1, min(int(limite) if limite is not None else config['limite'], MAX_CANDIDATOS_TOPE))
    limite_sql = max(limite_val, min(config['limite_sql'], MAX_CANDIDATOS_SQL_TOPE))

    descripcion_norm = normalizar_descripcion(descripcion)
    texto_activo = es_descripcion_valida_para_comparacion(descripcion_norm, min_longitud=min_len)
    coords_origen = normalizar_coordenadas(latitud, longitud)
    gps_activo = coords_origen is not None
    urls_origen = _normalizar_lista_urls_origen(foto=foto, fotos=fotos) if incluir_imagenes else []

    if dinov2_habilitado and not gps_activo:
        return _construir_respuesta(
            [],
            umbral=umbral_val,
            radio_metros=radio_val,
            dias=dias_val,
            min_longitud=min_len,
            limite=limite_val,
            excluir_id_peticion=excluir_id_peticion,
            texto_activo=texto_activo,
            gps_activo=False,
            imagen_activo=False,
            umbral_hamming=umbral_hamming_val,
            dinov2_activo=True,
            umbral_dinov2=umbral_dinov2_val,
            metodo_imagen=None,
            mensaje=(
                'Se requieren coordenadas GPS válidas dentro del radio configurado '
                '(200 m) para detectar posibles duplicados.'
            ),
        )

    if not dinov2_habilitado and not texto_activo and not gps_activo:
        mensaje_omitido = (
            'Descripción vacía o demasiado corta y sin coordenadas GPS válidas; '
            'la comparación visual requiere preselección por texto o GPS.'
            if urls_origen
            else 'Descripción vacía o demasiado corta y sin coordenadas GPS válidas; se omitió la búsqueda.'
        )
        return _construir_respuesta(
            [],
            umbral=umbral_val,
            radio_metros=radio_val,
            dias=dias_val,
            min_longitud=min_len,
            limite=limite_val,
            excluir_id_peticion=excluir_id_peticion,
            texto_activo=False,
            gps_activo=False,
            imagen_activo=False,
            umbral_hamming=umbral_hamming_val,
            dinov2_activo=None,
            umbral_dinov2=None,
            metodo_imagen=None,
            mensaje=mensaje_omitido,
        )

    fecha_corte = timezone.now() - timedelta(days=dias_val)
    registros_candidatos: Dict[int, Dict[str, Any]] = {}

    # Etapa 1: Candidatos por similitud textual en PostgreSQL (pg_trgm) solo en detector clásico
    if texto_activo and not dinov2_habilitado:
        qs_texto = Peticiones.objects.select_related('id_estado', 'id_ubicacion')
        if incluir_evidencias:
            qs_texto = qs_texto.prefetch_related('evidenciapeticiones_set')
        qs_texto = (
            qs_texto
            .filter(fecha__gte=fecha_corte)
            .exclude(descripcion__isnull=True)
            .annotate(_longitud_descripcion=Length(Trim('descripcion')))
            .filter(_longitud_descripcion__gte=min_len)
        )
        if excluir_id_peticion is not None:
            qs_texto = qs_texto.exclude(id_peticion=excluir_id_peticion)

        qs_texto = (
            qs_texto
            .annotate(similitud=TrigramSimilarity('descripcion', descripcion_norm))
            .filter(similitud__gte=umbral_val)
            .order_by('-similitud', '-fecha', '-id_peticion')[:limite_sql]
        )

        for peticion in qs_texto:
            sim_val = max(0.0, min(1.0, float(getattr(peticion, 'similitud', 0.0) or 0.0)))
            registros_candidatos[peticion.id_peticion] = {
                'peticion': peticion,
                'similitud_texto': sim_val,
                'coincidencia_texto': sim_val >= umbral_val,
                'distancia_metros': None,
                'coincidencia_gps': False,
            }

    # Etapa 2: Candidatos geográficos mediante bounding box SQL + Haversine en metros
    if gps_activo and coords_origen is not None:
        lat_orig, lon_orig = coords_origen
        bbox = calcular_bounding_box(lat_orig, lon_orig, radio_val)

        filtros_bbox: Dict[str, Any] = {
            'id_ubicacion__isnull': False,
            'id_ubicacion__latitud__isnull': False,
            'id_ubicacion__longitud__isnull': False,
            'id_ubicacion__latitud__gte': bbox['lat_min'],
            'id_ubicacion__latitud__lte': bbox['lat_max'],
        }
        if bbox['lon_min'] is not None and bbox['lon_max'] is not None:
            filtros_bbox['id_ubicacion__longitud__gte'] = bbox['lon_min']
            filtros_bbox['id_ubicacion__longitud__lte'] = bbox['lon_max']

        qs_gps = Peticiones.objects.select_related('id_estado', 'id_ubicacion')
        if incluir_evidencias:
            qs_gps = qs_gps.prefetch_related('evidenciapeticiones_set')
        qs_gps = (
            qs_gps
            .filter(fecha__gte=fecha_corte)
            .filter(**filtros_bbox)
        )
        if excluir_id_peticion is not None:
            qs_gps = qs_gps.exclude(id_peticion=excluir_id_peticion)

        if texto_activo:
            qs_gps = qs_gps.annotate(
                similitud=Coalesce(
                    TrigramSimilarity('descripcion', descripcion_norm),
                    Value(0.0),
                    output_field=FloatField(),
                )
            )

        qs_gps = qs_gps.order_by('-fecha', '-id_peticion')[:limite_sql]

        for peticion in qs_gps:
            coords_cand = _extraer_coordenadas_peticion(peticion)
            if coords_cand is None:
                continue

            distancia = calcular_distancia_haversine_metros(
                lat_orig, lon_orig, coords_cand[0], coords_cand[1]
            )
            if distancia > radio_val:
                continue

            pid = peticion.id_peticion
            if pid in registros_candidatos:
                registros_candidatos[pid]['distancia_metros'] = distancia
                registros_candidatos[pid]['coincidencia_gps'] = True
            else:
                sim_val = 0.0
                if texto_activo and es_descripcion_valida_para_comparacion(
                    getattr(peticion, 'descripcion', None),
                    min_longitud=min_len,
                ):
                    sim_val = max(0.0, min(1.0, float(getattr(peticion, 'similitud', 0.0) or 0.0)))
                registros_candidatos[pid] = {
                    'peticion': peticion,
                    'similitud_texto': sim_val,
                    'coincidencia_texto': texto_activo and sim_val >= umbral_val,
                    'distancia_metros': distancia,
                    'coincidencia_gps': True,
                }

    # Calcular distancia exacta también sobre candidatos textuales que posean GPS válido
    if gps_activo and coords_origen is not None:
        lat_orig, lon_orig = coords_origen
        for item in registros_candidatos.values():
            if item['distancia_metros'] is None:
                coords_cand = _extraer_coordenadas_peticion(item['peticion'])
                if coords_cand is not None:
                    distancia = calcular_distancia_haversine_metros(
                        lat_orig, lon_orig, coords_cand[0], coords_cand[1]
                    )
                    item['distancia_metros'] = distancia
                    item['coincidencia_gps'] = distancia <= radio_val

    # Preseleccionar candidatos relevantes para comparar imágenes
    preseleccionados: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    for item in registros_candidatos.values():
        coinc_txt = bool(item['coincidencia_texto'])
        coinc_gps = bool(item['coincidencia_gps'])
        if dinov2_habilitado:
            if not coinc_gps:
                continue
        elif not coinc_txt and not coinc_gps:
            continue

        item['coincidencia_imagen'] = False
        item['distancia_hamming'] = None
        item['similitud_imagen'] = None
        item['porcentaje_similitud_visual'] = None
        item['similitud_visual_coseno'] = None
        item['metodo_imagen'] = None
        item['es_certeza_identidad'] = False
        item['tipo_metrica_visual'] = None
        item['advertencia_metrica_visual'] = None

        pet = item['peticion']
        sim_txt = float(item['similitud_texto'])
        dist_m = item['distancia_metros']
        prelim_score = _calcular_puntaje_combinado(
            similitud_texto=sim_txt,
            coincidencia_texto=coinc_txt,
            distancia_metros=dist_m,
            coincidencia_gps=coinc_gps,
            radio_metros=radio_val,
            gps_activo=gps_activo,
            priorizar_visual_gps=dinov2_habilitado,
        )
        fecha_obj = getattr(pet, 'fecha', None)
        fecha_ord = fecha_obj.timestamp() if hasattr(fecha_obj, 'timestamp') else 0.0
        dist_ord = -float(dist_m) if dist_m is not None else float('-inf')
        if dinov2_habilitado:
            prelim_tier = (1 if coinc_gps else 0, 1 if coinc_txt else 0)
        else:
            prelim_tier = (1 if coinc_txt else 0) + (1 if coinc_gps else 0)
        prelim_key = (prelim_tier, prelim_score, sim_txt, dist_ord, fecha_ord, getattr(pet, 'id_peticion', 0) or 0)
        preseleccionados.append((prelim_key, item))

    preseleccionados.sort(key=lambda par: par[0], reverse=True)

    # Etapa 3: Comparación visual (DINOv2 Small o respaldo perceptual dHash)
    imagen_activo, metodo_visual = _ejecutar_comparacion_visual(
        preseleccionados,
        urls_origen=urls_origen,
        dinov2_habilitado=dinov2_habilitado,
        umbral_dinov2_val=umbral_dinov2_val,
        umbral_hamming_val=umbral_hamming_val,
        max_imagenes_pet=max_imagenes_pet,
        max_candidatos_img=max_candidatos_img,
        dinov2_fallback_dhash=dinov2_fallback_dhash,
    )

    candidatos_con_clave: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    for _, item in preseleccionados:
        pet = item['peticion']
        coinc_txt = bool(item['coincidencia_texto'])
        coinc_gps = bool(item['coincidencia_gps'])
        coinc_img = bool(item['coincidencia_imagen'])
        if dinov2_habilitado:
            if not coinc_gps:
                continue
        elif not (coinc_txt or coinc_gps or coinc_img):
            continue

        sim_txt = float(item['similitud_texto'])
        dist_m = item['distancia_metros']
        dist_hamming = item['distancia_hamming']
        sim_img = item['similitud_imagen']

        score = _calcular_puntaje_combinado(
            similitud_texto=sim_txt,
            coincidencia_texto=coinc_txt,
            distancia_metros=dist_m,
            coincidencia_gps=coinc_gps,
            radio_metros=radio_val,
            gps_activo=gps_activo,
            similitud_imagen=sim_img,
            coincidencia_imagen=coinc_img,
            priorizar_visual_gps=dinov2_habilitado,
        )
        serializado = _serializar_candidato(
            pet,
            similitud_texto=sim_txt,
            coincidencia_texto=coinc_txt,
            distancia_metros=dist_m,
            coincidencia_gps=coinc_gps,
            coincidencia_imagen=coinc_img,
            distancia_hamming=dist_hamming,
            similitud_imagen=sim_img,
            score_combinado=score,
            incluir_evidencias=incluir_evidencias,
            porcentaje_similitud_visual=item.get('porcentaje_similitud_visual'),
            similitud_visual_coseno=item.get('similitud_visual_coseno'),
            metodo_imagen=item.get('metodo_imagen'),
            es_certeza_identidad=bool(item.get('es_certeza_identidad', False)),
            tipo_metrica_visual=item.get('tipo_metrica_visual'),
            advertencia_metrica_visual=item.get('advertencia_metrica_visual'),
            incluir_meta_dinov2=dinov2_habilitado,
        )

        fecha_obj = getattr(pet, 'fecha', None)
        fecha_ord = fecha_obj.timestamp() if hasattr(fecha_obj, 'timestamp') else 0.0
        dist_ord = -float(dist_m) if dist_m is not None else float('-inf')
        if dinov2_habilitado:
            tier = (
                1 if (coinc_img and coinc_gps) else 0,
                1 if coinc_img else 0,
                1 if coinc_gps else 0,
                1 if coinc_txt else 0,
            )
            sim_img_ord = float(sim_img) if sim_img is not None else -1.0
            clave_orden = (
                tier,
                score,
                sim_img_ord,
                dist_ord,
                sim_txt,
                fecha_ord,
                getattr(pet, 'id_peticion', 0) or 0,
            )
        else:
            tier = (1 if coinc_txt else 0) + (1 if coinc_gps else 0) + (1 if coinc_img else 0)
            hamming_ord = -int(dist_hamming) if dist_hamming is not None else float('-inf')
            clave_orden = (
                tier,
                score,
                sim_txt,
                dist_ord,
                hamming_ord,
                fecha_ord,
                getattr(pet, 'id_peticion', 0) or 0,
            )
        candidatos_con_clave.append((clave_orden, serializado))

    candidatos_con_clave.sort(key=lambda par: par[0], reverse=True)
    candidatos = [par[1] for par in candidatos_con_clave[:limite_val]]

    if candidatos:
        mensaje = (
            f"Se encontraron {len(candidatos)} posible(s) reporte(s) similar(es) "
            "para revisión humana."
        )
    else:
        mensaje = 'No se encontraron reportes similares dentro de los criterios configurados.'

    return _construir_respuesta(
        candidatos,
        umbral=umbral_val,
        radio_metros=radio_val,
        dias=dias_val,
        min_longitud=min_len,
        limite=limite_val,
        excluir_id_peticion=excluir_id_peticion,
        texto_activo=texto_activo,
        gps_activo=gps_activo,
        imagen_activo=imagen_activo,
        umbral_hamming=umbral_hamming_val,
        dinov2_activo=(True if dinov2_habilitado else None),
        umbral_dinov2=(umbral_dinov2_val if dinov2_habilitado else None),
        metodo_imagen=(metodo_visual if dinov2_habilitado else None),
        mensaje=mensaje,
    )


def detectar_duplicados_desde_solicitud(
    *,
    descripcion: Optional[str] = None,
    latitud: Any = None,
    longitud: Any = None,
    coordenadas_proporcionadas: bool = False,
    foto: Optional[str] = None,
    fotos: Optional[Sequence[str]] = None,
    fotos_proporcionadas: bool = False,
    id_peticion: Optional[int] = None,
    excluir_id_peticion: Optional[int] = None,
    umbral: Optional[float] = None,
    radio_metros: Optional[float] = None,
    umbral_hamming: Optional[int] = None,
    umbral_dinov2: Optional[float] = None,
    usar_dinov2: Optional[bool] = None,
    dias: Optional[int] = None,
    min_longitud: Optional[int] = None,
    limite: Optional[int] = None,
    incluir_imagenes: bool = True,
    incluir_evidencias: bool = True,
) -> Dict[str, Any]:
    """
    Orquesta la consulta combinada (Fase 1 + Fase 2 + Fase 3) a partir de la entrada del endpoint.
    Si se envía `id_peticion` (o `excluir_id_peticion`) sin descripción, coordenadas o fotos,
    completa los datos faltantes desde la petición, su `Ubicaciones` y sus evidencias asociadas.
    """
    id_excluir = excluir_id_peticion if excluir_id_peticion is not None else id_peticion
    descripcion_a_evaluar = descripcion
    lat_a_evaluar = latitud
    lon_a_evaluar = longitud
    urls_fotos_a_evaluar = _normalizar_lista_urls_origen(foto=foto, fotos=fotos) if incluir_imagenes else []

    necesita_desc = descripcion_a_evaluar is None
    necesita_gps = (not coordenadas_proporcionadas) and lat_a_evaluar is None and lon_a_evaluar is None
    necesita_fotos = (
        incluir_imagenes
        and (not fotos_proporcionadas)
        and not urls_fotos_a_evaluar
        and id_peticion is not None
    )

    if id_excluir is not None and (necesita_desc or necesita_gps or necesita_fotos):
        referencia = (
            Peticiones.objects
            .filter(pk=id_excluir)
            .values(
                'id_peticion',
                'descripcion',
                'id_ubicacion__latitud',
                'id_ubicacion__longitud',
                'foto',
            )
            .first()
        )
        if not referencia:
            if necesita_desc and necesita_gps and not fotos_proporcionadas:
                raise PeticionNoEncontradaError(f"La petición con id {id_excluir} no existe.")
        elif isinstance(referencia, dict):
            if necesita_desc:
                descripcion_a_evaluar = referencia.get('descripcion')
            if necesita_gps:
                lat_a_evaluar = referencia.get('id_ubicacion__latitud')
                lon_a_evaluar = referencia.get('id_ubicacion__longitud')
            if necesita_fotos:
                urls_extraidas: List[str] = []
                vistas = set()
                foto_ref = referencia.get('foto')
                if isinstance(foto_ref, str) and foto_ref.strip():
                    limpia = foto_ref.strip()
                    vistas.add(limpia)
                    urls_extraidas.append(limpia)
                for ruta in (
                    Peticiones.objects
                    .filter(pk=id_excluir, evidenciapeticiones__ruta_archivo__isnull=False)
                    .values_list('evidenciapeticiones__ruta_archivo', flat=True)
                ):
                    if isinstance(ruta, str) and ruta.strip():
                        limpia = ruta.strip()
                        if limpia not in vistas:
                            vistas.add(limpia)
                            urls_extraidas.append(limpia)
                urls_fotos_a_evaluar = urls_extraidas

    return buscar_peticiones_similares_por_descripcion(
        descripcion_a_evaluar,
        latitud=lat_a_evaluar,
        longitud=lon_a_evaluar,
        fotos=urls_fotos_a_evaluar,
        excluir_id_peticion=id_excluir,
        umbral=umbral,
        radio_metros=radio_metros,
        umbral_hamming=umbral_hamming,
        umbral_dinov2=umbral_dinov2,
        usar_dinov2=usar_dinov2,
        dias=dias,
        min_longitud=min_longitud,
        limite=limite,
        incluir_imagenes=incluir_imagenes,
        incluir_evidencias=incluir_evidencias,
    )
