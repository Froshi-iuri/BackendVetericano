"""
Servicio para registrar y consultar la autorización expresa de fotografías
en el flujo ciudadano de detección de posibles duplicados.
"""

import logging
from typing import Dict, Iterable, List, Optional, Sequence, Set

from django.db import transaction

from peticiones.models import PeticionFotoAutorizada, Peticiones
from peticiones.services.imagen_hash_service import obtener_config_imagen_hash
from users.api.permissions import es_peticionario

logger = logging.getLogger(__name__)

VERSION_TEXTO_AUTORIZACION_FOTO = 'v1'
MAX_FOTOS_AUTORIZADAS_POR_PETICION = 5
MAX_LARGO_RUTA_AUTORIZADA = 500


def registrar_fotos_autorizadas(
    *,
    peticion: Optional[Peticiones],
    autorizado_por,
    urls_autorizadas: Optional[Sequence[str]],
    urls_procesadas: Optional[Sequence[str]],
) -> List[PeticionFotoAutorizada]:
    """
    Registra de forma aditiva y tolerante a fallos las URLs Cloudinary de imagen
    expresamente autorizadas por un Peticionario activo durante la radicación.

    Cualquier URL inválida, no perteneciente a `urls_procesadas` o enviada por
    un rol distinto a Peticionario activo se ignora en silencio. Un fallo en la
    persistencia no aborta la creación de la petición (fallo cerrado: sin foto).
    """
    if peticion is None or not urls_autorizadas or not urls_procesadas:
        return []

    if not es_peticionario(autorizado_por):
        return []

    urls_procesadas_set: Set[str] = {
        url.strip()
        for url in urls_procesadas
        if isinstance(url, str) and url.strip()
    }
    if not urls_procesadas_set:
        return []

    candidatas: List[str] = []
    vistas: Set[str] = set()
    for raw in urls_autorizadas:
        if not isinstance(raw, str):
            continue
        url_limpia = raw.strip()
        if not url_limpia or url_limpia in vistas:
            continue
        vistas.add(url_limpia)
        candidatas.append(url_limpia)
        if len(candidatas) >= MAX_FOTOS_AUTORIZADAS_POR_PETICION:
            break

    if not candidatas:
        return []

    from peticiones.services.duplicados_ciudadano_service import url_imagen_cloudinary_apta

    dominios_permitidos = obtener_config_imagen_hash()['dominios_permitidos']
    urls_validas: List[str] = []
    for url_cand in candidatas:
        if url_cand not in urls_procesadas_set:
            continue
        url_apta = url_imagen_cloudinary_apta(url_cand, dominios_permitidos)
        if not url_apta or len(url_apta) > MAX_LARGO_RUTA_AUTORIZADA:
            continue
        urls_validas.append(url_apta)

    if not urls_validas:
        return []

    registros: List[PeticionFotoAutorizada] = []
    try:
        with transaction.atomic():
            for url_valida in urls_validas:
                obj, _ = PeticionFotoAutorizada.objects.get_or_create(
                    id_peticion=peticion,
                    ruta_archivo=url_valida,
                    defaults={
                        'autorizado_por': autorizado_por,
                        'version_texto': VERSION_TEXTO_AUTORIZACION_FOTO,
                    },
                )
                registros.append(obj)
    except Exception:
        logger.exception(
            "Error no bloqueante al registrar autorización de fotos para petición #%s.",
            getattr(peticion, 'id_peticion', None),
        )
        return []

    return registros


def obtener_urls_autorizadas(ids_peticion: Iterable[int]) -> Dict[int, Set[str]]:
    """
    Obtiene en una única consulta las URLs con autorización vigente (no revocada)
    para los identificadores de petición indicados.
    """
    ids_limpios: List[int] = []
    for pid in ids_peticion or ():
        if pid is None or isinstance(pid, bool):
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        if pid_int > 0:
            ids_limpios.append(pid_int)

    if not ids_limpios:
        return {}

    filas = PeticionFotoAutorizada.objects.filter(
        id_peticion_id__in=ids_limpios,
        revocada_en__isnull=True,
    ).values_list('id_peticion_id', 'ruta_archivo')

    autorizadas: Dict[int, Set[str]] = {}
    for id_peticion, ruta_archivo in filas:
        ruta_limpia = ruta_archivo.strip() if isinstance(ruta_archivo, str) else ''
        if not ruta_limpia:
            continue
        autorizadas.setdefault(id_peticion, set()).add(ruta_limpia)
    return autorizadas
