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


def obtener_fotos_acta_con_fallback(seguimiento) -> list:
    """
    Retorna la lista de URLs de fotos para un acta de visita (SeguimientoPeticionesVisita).

    Orden de precedencia y fallback:
    1. Fotos registradas específicamente para esta visita en EvidenciaPeticiones:
       descripcion comienza con 'Acta de visita #{id_seguimiento}'
    2. Fallback a evidencias generales de la petición asociada (EvidenciaPeticiones)
    3. Fallback a id_peticion.foto (si existe)
    4. Fallback a id_animal.foto_url (si existe)
    5. Lista vacía []
    """
    if not seguimiento:
        return []

    from peticiones.models import EvidenciaPeticiones

    id_seguimiento = getattr(seguimiento, 'id_seguimiento', None) or getattr(seguimiento, 'pk', None)
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

    # 1. Fotos del acta específica
    fotos_acta = [e.ruta_archivo for e in todas_evidencias if e.descripcion and e.descripcion.startswith(prefijo_acta)]
    if fotos_acta:
        return [f for f in fotos_acta if f]

    # 2. Fallback a cualquier evidencia de la petición
    fotos_peticion = [e.ruta_archivo for e in todas_evidencias]
    if fotos_peticion:
        return [f for f in fotos_peticion if f]

    # 3. Fallback a peticion.foto
    if peticion and getattr(peticion, 'foto', None):
        foto_str = str(peticion.foto).strip()
        if foto_str:
            return [foto_str]

    # 4. Fallback a animal.foto_url
    animal = getattr(seguimiento, 'id_animal', None)
    if animal and getattr(animal, 'foto_url', None):
        foto_url = str(animal.foto_url).strip()
        if foto_url:
            return [foto_url]

    return []
