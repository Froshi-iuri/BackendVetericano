import os
import logging
import requests

logger = logging.getLogger(__name__)

CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', 'aefeig5y')
CLOUDINARY_UPLOAD_PRESET = os.environ.get('CLOUDINARY_UPLOAD_PRESET', 'preset_android')


def subir_o_asegurar_cloudinary(imagen_str: str) -> str:
    """
    Recibe una imagen que puede ser:
    1. Una URL existente (http:// o https://): se conserva directamente.
    2. Una cadena base64 o data URI: la sube a Cloudinary vía REST API y retorna la secure_url.
    3. None o cadena vacía: retorna None.
    
    Garantiza que la URL resultante nunca supere los 500 caracteres (límite de ruta_archivo en BD).
    """
    if not imagen_str:
        return None

    valor = str(imagen_str).strip()
    if not valor:
        return None

    # Si ya es una URL web válida, conservarla
    if valor.startswith('http://') or valor.startswith('https://'):
        return valor[:500]

    # Si es una imagen en base64 (o data URI), subir a Cloudinary
    try:
        url_api = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/auto/upload"
        payload = {
            'file': valor,
            'upload_preset': CLOUDINARY_UPLOAD_PRESET
        }
        resp = requests.post(url_api, data=payload, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            secure_url = data.get('secure_url') or data.get('url')
            if secure_url:
                return str(secure_url)[:500]
        else:
            logger.warning(f"Error en respuesta de Cloudinary (HTTP {resp.status_code}): {resp.text[:200]}")
    except Exception as exc:
        logger.error(f"Excepción al subir imagen a Cloudinary: {exc}")

    # Fallback defensivo si era un path relativo corto
    if len(valor) <= 500 and not valor.startswith('data:'):
        return valor

    return None


def detectar_tipo_archivo(url_o_str: str) -> str:
    """
    Detecta automáticamente si un recurso es 'video' o 'imagen' basándose en:
    1. Si contiene la ruta de Cloudinary para videos (/video/upload/).
    2. Si tiene extensión clásica de video (.mp4, .mov, etc.).
    3. Si contiene prefijos de datos de video (data:video/...).
    Por defecto retorna 'imagen'.
    """
    if not url_o_str:
        return 'imagen'

    val = str(url_o_str).strip().lower()
    if val.startswith('data:video/'):
        return 'video'
    if '/video/upload/' in val:
        return 'video'

    exts_video = ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp', '.m4v')
    url_limpia = val.split('?')[0].split('#')[0]
    if any(url_limpia.endswith(ext) for ext in exts_video):
        return 'video'

    return 'imagen'


def obtener_fotos_acta_con_fallback(seguimiento) -> list:
    """
    Retorna la lista de URLs de fotos para un acta de visita (SeguimientoPeticionesVisita).

    Orden de precedencia y fallback:
    1. Evidencias registradas en la nueva tabla dedicada EvidenciaSeguimientoVisita con tipo_archivo != 'video'.
    2. Fallback histórico a EvidenciaPeticiones donde descripcion comienza con 'Acta de visita #{id_seguimiento}'.
    3. Fallback a evidencias generales de la petición asociada (EvidenciaPeticiones).
    4. Fallback a id_peticion.foto (si existe).
    5. Fallback a id_animal.foto_url (si existe).
    6. Lista vacía [].
    """
    if not seguimiento:
        return []

    from peticiones.models import EvidenciaSeguimientoVisita, EvidenciaPeticiones

    id_seguimiento = getattr(seguimiento, 'id_seguimiento', None) or getattr(seguimiento, 'pk', None)

    # 1. Consultar la tabla dedicada EvidenciaSeguimientoVisita (aprovecha prefetch si existe)
    if id_seguimiento:
        evidencias_rel = getattr(seguimiento, 'evidencias', None)
        if evidencias_rel is not None and hasattr(evidencias_rel, 'all'):
            lista_evs = list(evidencias_rel.all())
        else:
            lista_evs = list(
                EvidenciaSeguimientoVisita.objects.filter(id_seguimiento_id=id_seguimiento)
                .order_by('id_evidencia')
            )

        fotos_nuevas = [
            e.ruta_archivo for e in lista_evs
            if e.ruta_archivo and (e.tipo_archivo or 'imagen').lower() != 'video'
            and detectar_tipo_archivo(e.ruta_archivo) != 'video'
        ]
        if fotos_nuevas:
            return fotos_nuevas

    peticion = getattr(seguimiento, 'id_peticion', None)
    peticion_pk = getattr(peticion, 'pk', None) or getattr(seguimiento, 'id_peticion_id', None)

    if not peticion_pk:
        return []

    prefijo_acta = f"Acta de visita #{id_seguimiento}"

    # Si evidenciapeticiones_set ya está en cache de prefetch:
    evidencias_cache = getattr(peticion, '_prefetched_objects_cache', {}).get('evidenciapeticiones_set')
    if evidencias_cache is not None:
        todas_evidencias = [e for e in evidencias_cache if e.ruta_archivo]
    else:
        todas_evidencias = list(
            EvidenciaPeticiones.objects.filter(id_peticion_id=peticion_pk)
            .exclude(ruta_archivo__isnull=True)
            .exclude(ruta_archivo='')
        )

    # 2. Fallback histórico a fotos del acta específica en EvidenciaPeticiones
    fotos_acta = [
        e.ruta_archivo for e in todas_evidencias
        if e.descripcion and e.descripcion.startswith(prefijo_acta)
        and detectar_tipo_archivo(e.ruta_archivo) != 'video'
    ]
    if fotos_acta:
        return [f for f in fotos_acta if f]

    # 3. Fallback a cualquier evidencia de la petición
    fotos_peticion = [e.ruta_archivo for e in todas_evidencias if detectar_tipo_archivo(e.ruta_archivo) != 'video']
    if fotos_peticion:
        return [f for f in fotos_peticion if f]

    # 4. Fallback a peticion.foto
    if peticion and getattr(peticion, 'foto', None):
        foto_str = str(peticion.foto).strip()
        if foto_str and detectar_tipo_archivo(foto_str) != 'video':
            return [foto_str]

    # 5. Fallback a animal.foto_url
    animal = getattr(seguimiento, 'id_animal', None)
    if animal and getattr(animal, 'foto_url', None):
        foto_url = str(animal.foto_url).strip()
        if foto_url:
            return [foto_url]

    return []


def obtener_videos_acta(seguimiento) -> list:
    """
    Retorna la lista de URLs de videos asociados a un acta de visita (SeguimientoPeticionesVisita).
    Busca en EvidenciaSeguimientoVisita y como fallback en EvidenciaPeticiones.
    """
    if not seguimiento:
        return []

    from peticiones.models import EvidenciaSeguimientoVisita, EvidenciaPeticiones

    id_seguimiento = getattr(seguimiento, 'id_seguimiento', None) or getattr(seguimiento, 'pk', None)
    if not id_seguimiento:
        return []

    # 1. Buscar en la nueva tabla dedicada EvidenciaSeguimientoVisita
    evidencias_rel = getattr(seguimiento, 'evidencias', None)
    if evidencias_rel is not None and hasattr(evidencias_rel, 'all'):
        lista_evs = list(evidencias_rel.all())
    else:
        lista_evs = list(
            EvidenciaSeguimientoVisita.objects.filter(id_seguimiento_id=id_seguimiento)
            .order_by('id_evidencia')
        )

    videos = [
        e.ruta_archivo for e in lista_evs
        if e.ruta_archivo and (
            (e.tipo_archivo or '').lower() == 'video'
            or detectar_tipo_archivo(e.ruta_archivo) == 'video'
        )
    ]
    if videos:
        return videos

    # 2. Fallback a EvidenciaPeticiones si había videos con el prefijo
    peticion = getattr(seguimiento, 'id_peticion', None)
    peticion_pk = getattr(peticion, 'pk', None) or getattr(seguimiento, 'id_peticion_id', None)
    if peticion_pk:
        prefijo_acta = f"Acta de visita #{id_seguimiento}"
        evs_peticion = EvidenciaPeticiones.objects.filter(id_peticion_id=peticion_pk)
        videos_legados = [
            e.ruta_archivo for e in evs_peticion
            if e.ruta_archivo and e.descripcion and e.descripcion.startswith(prefijo_acta)
            and detectar_tipo_archivo(e.ruta_archivo) == 'video'
        ]
        if videos_legados:
            return videos_legados

    return []


def obtener_multimedia_acta(seguimiento) -> dict:
    """
    Retorna un diccionario consolidado con todas las fotos, videos y el detalle
    estructurado de evidencias para una visita de campo.
    """
    fotos = obtener_fotos_acta_con_fallback(seguimiento)
    videos = obtener_videos_acta(seguimiento)

    multimedia_lista = []
    for f in fotos:
        multimedia_lista.append({'url': f, 'tipo': 'imagen'})
    for v in videos:
        multimedia_lista.append({'url': v, 'tipo': 'video'})

    return {
        'foto': fotos[0] if fotos else None,
        'fotos': fotos,
        'total_fotos': len(fotos),
        'videos': videos,
        'total_videos': len(videos),
        'multimedia': multimedia_lista,
    }

