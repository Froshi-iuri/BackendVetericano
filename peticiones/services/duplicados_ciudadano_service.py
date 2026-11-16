"""
Servicio ciudadano de detección de posibles reportes duplicados.

Envuelve al detector interno `detectar_duplicados_desde_solicitud` con parámetros
fijos en el servidor, sin persistencia en base de datos y aplicando una lista
blanca estricta de salida para no exponer información sensible de terceros.
"""

import logging
import math
import re
from typing import AbstractSet, Any, Dict, List, Mapping, Optional, Sequence, Set
from urllib.parse import urlparse

from django.conf import settings

from peticiones.services.dinov2_service import (
    ADVERTENCIA_METRICA_VISUAL_DEFAULT,
    TIPO_METRICA_VISUAL,
    es_dinov2_activo,
)
from peticiones.services.duplicados_service import detectar_duplicados_desde_solicitud
from peticiones.services.foto_autorizada_service import obtener_urls_autorizadas
from peticiones.services.imagen_hash_service import (
    obtener_config_imagen_hash,
    validar_url_cloudinary_segura,
)

logger = logging.getLogger(__name__)

MAX_CANDIDATOS_CIUDADANO = 5
UMBRAL_CERCANIA_MUY_CERCA_METROS = 50.0
UMBRAL_SIMILITUD_ALTA = 0.60
TRANSFORMACION_MINIATURA_CIUDADANO = 'e_blur_faces/c_fill,g_auto,w_480,h_360,q_auto,f_auto'
MENSAJE_CON_DUPLICADOS = 'Se encontraron reportes similares.'
MENSAJE_SIN_DUPLICADOS = 'No se encontraron reportes similares.'

_PATRON_TRANSFORMACION_CLOUDINARY = re.compile(r'^[a-zA-Z]{1,3}_')
_MARCADOR_IMAGE_UPLOAD = '/image/upload/'


def url_imagen_cloudinary_apta(
    url: Any,
    dominios_permitidos: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """
    Valida estrictamente que `url` sea una imagen Cloudinary segura apta para
    autorización y derivación de miniatura ciudadana: HTTPS, dominio permitido,
    ruta con `/image/upload/` y sin `/video/`, sin credenciales, query ni
    fragmento, y sin transformaciones ni firmas previas.

    Retorna la URL limpia tras `strip()` o `None` si no cumple algún requisito.
    """
    url_segura = validar_url_cloudinary_segura(url, dominios_permitidos=dominios_permitidos)
    if not url_segura:
        return None

    if '?' in url_segura or '#' in url_segura:
        return None

    try:
        parsed = urlparse(url_segura)
    except ValueError:
        return None

    if (
        parsed.scheme.lower() != 'https'
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None

    ruta_lower = (parsed.path or '').lower()
    if '/video/' in ruta_lower or _MARCADOR_IMAGE_UPLOAD not in parsed.path:
        return None

    _, sufijo = url_segura.split(_MARCADOR_IMAGE_UPLOAD, 1)
    if not sufijo:
        return None

    primer_segmento = sufijo.split('/', 1)[0]
    if not primer_segmento:
        return None

    if (
        ',' in primer_segmento
        or primer_segmento.startswith('s--')
        or _PATRON_TRANSFORMACION_CLOUDINARY.match(primer_segmento)
    ):
        return None

    return url_segura


def _construir_miniatura_ciudadano(
    url: Any,
    *,
    dominios_permitidos: Sequence[str],
) -> Optional[str]:
    """
    Valida una URL Cloudinary mediante `url_imagen_cloudinary_apta` y construye
    únicamente una miniatura derivada con `TRANSFORMACION_MINIATURA_CIUDADANO`
    (`e_blur_faces/c_fill,g_auto,w_480,h_360,q_auto,f_auto`).

    Nota de seguridad: el encadenado `e_blur_faces/...` difumina rostros humanos
    detectados como defensa adicional en profundidad, pero NO garantiza por sí
    solo que el contenido de la fotografía sea apto; la garantía de aptitud
    descansa en la autorización expresa del ciudadano (que certifica que solo
    aparece el animal) junto con las validaciones técnicas de URL.
    """
    url_apta = url_imagen_cloudinary_apta(url, dominios_permitidos=dominios_permitidos)
    if not url_apta:
        return None

    prefijo, sufijo = url_apta.split(_MARCADOR_IMAGE_UPLOAD, 1)
    miniatura = f"{prefijo}{_MARCADOR_IMAGE_UPLOAD}{TRANSFORMACION_MINIATURA_CIUDADANO}/{sufijo}"
    if miniatura == url_apta:
        return None
    return miniatura


def _extraer_miniatura_candidato(
    candidato: Mapping[str, Any],
    *,
    dominios_permitidos: Sequence[str],
    urls_autorizadas: Optional[AbstractSet[str]] = None,
) -> Optional[str]:
    """
    Obtiene la primera miniatura válida entre `foto` y `fotos` del candidato
    que cuente con autorización expresa vigente para esa petición.
    """
    if not urls_autorizadas:
        return None

    urls_candidatas: List[Any] = [candidato.get('foto')]
    fotos = candidato.get('fotos')
    if isinstance(fotos, (list, tuple)):
        urls_candidatas.extend(fotos)

    for url in urls_candidatas:
        if not isinstance(url, str):
            continue
        url_limpia = url.strip()
        if not url_limpia or url_limpia not in urls_autorizadas:
            continue
        miniatura = _construir_miniatura_ciudadano(
            url_limpia,
            dominios_permitidos=dominios_permitidos,
        )
        if miniatura is not None:
            return miniatura
    return None


def _clasificar_cercania(coincidencia_gps: bool, distancia_metros: Any) -> str:
    if not coincidencia_gps or distancia_metros is None or isinstance(distancia_metros, bool):
        return 'no_disponible'
    try:
        distancia = float(distancia_metros)
    except (TypeError, ValueError):
        return 'no_disponible'
    if not math.isfinite(distancia):
        return 'no_disponible'
    return 'muy_cerca' if distancia <= UMBRAL_CERCANIA_MUY_CERCA_METROS else 'cerca'


def _clasificar_similitud(coincidencia_texto: bool, similitud_texto: Any) -> str:
    if not coincidencia_texto:
        return 'no_disponible'
    if similitud_texto is None or isinstance(similitud_texto, bool):
        return 'media'
    try:
        similitud = float(similitud_texto)
    except (TypeError, ValueError):
        return 'media'
    if not math.isfinite(similitud):
        return 'media'
    return 'alta' if similitud >= UMBRAL_SIMILITUD_ALTA else 'media'


def _clasificar_similitud_visual(porcentaje: Any) -> str:
    if porcentaje is None or isinstance(porcentaje, bool):
        return 'no_disponible'
    try:
        valor = float(porcentaje)
    except (TypeError, ValueError):
        return 'no_disponible'
    if not math.isfinite(valor):
        return 'no_disponible'
    if valor >= 75.0:
        return 'alta'
    if valor >= 45.0:
        return 'media'
    return 'baja'


def _normalizar_id_peticion(valor: Any) -> Optional[int]:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        pid = int(valor)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _mapear_candidato_ciudadano(
    candidato: Mapping[str, Any],
    *,
    foto_activa: bool,
    dominios_permitidos: Sequence[str],
    urls_autorizadas: Optional[AbstractSet[str]] = None,
    incluir_visual_dinov2: bool = False,
) -> Dict[str, Any]:
    coincidencia_texto = bool(candidato.get('coincidencia_texto', False))
    coincidencia_gps = bool(candidato.get('coincidencia_gps', False))
    foto = (
        _extraer_miniatura_candidato(
            candidato,
            dominios_permitidos=dominios_permitidos,
            urls_autorizadas=urls_autorizadas,
        )
        if foto_activa
        else None
    )

    resultado: Dict[str, Any] = {
        'coincidencia_texto': coincidencia_texto,
        'coincidencia_gps': coincidencia_gps,
        'cercania': _clasificar_cercania(coincidencia_gps, candidato.get('distancia_metros')),
        'similitud': _clasificar_similitud(coincidencia_texto, candidato.get('similitud_texto')),
        'foto': foto,
        'tiene_fotografia': foto is not None,
    }

    if incluir_visual_dinov2:
        porcentaje_raw = candidato.get('porcentaje_similitud_visual')
        if porcentaje_raw is None and candidato.get('similitud_imagen') is not None:
            try:
                porcentaje_raw = round(float(candidato['similitud_imagen']) * 100.0, 2)
            except (TypeError, ValueError):
                porcentaje_raw = None
        resultado.update({
            'coincidencia_imagen': bool(candidato.get('coincidencia_imagen', False)),
            'porcentaje_similitud_visual': (
                round(float(porcentaje_raw), 2)
                if porcentaje_raw is not None and not isinstance(porcentaje_raw, bool)
                else None
            ),
            'similitud_visual': _clasificar_similitud_visual(porcentaje_raw),
            'metodo_imagen': candidato.get('metodo_imagen'),
            'es_certeza_identidad': False,
            'tipo_metrica_visual': candidato.get('tipo_metrica_visual') or TIPO_METRICA_VISUAL,
            'advertencia_metrica_visual': (
                candidato.get('advertencia_metrica_visual') or ADVERTENCIA_METRICA_VISUAL_DEFAULT
            ),
        })

    return resultado


def detectar_duplicados_ciudadano(
    *,
    descripcion: Optional[str] = '',
    latitud: Optional[float] = None,
    longitud: Optional[float] = None,
    foto: Optional[str] = None,
    fotos: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Ejecuta la detección de duplicados para el flujo ciudadano con parámetros
    controlados por el servidor y respuesta anonimizada por lista blanca.
    """
    foto_activa = bool(getattr(settings, 'DUPLICADOS_CIUDADANO_FOTO_ACTIVA', True))
    dinov2_activo = es_dinov2_activo()

    if dinov2_activo:
        coords_ok = latitud is not None and longitud is not None
        resultado_interno = detectar_duplicados_desde_solicitud(
            descripcion=descripcion,
            latitud=latitud,
            longitud=longitud,
            coordenadas_proporcionadas=coords_ok,
            foto=foto,
            fotos=fotos,
            fotos_proporcionadas=bool(foto or fotos),
            limite=MAX_CANDIDATOS_CIUDADANO,
            incluir_imagenes=True,
            incluir_evidencias=foto_activa,
            usar_dinov2=True,
        )
    else:
        resultado_interno = detectar_duplicados_desde_solicitud(
            descripcion=descripcion,
            latitud=latitud,
            longitud=longitud,
            coordenadas_proporcionadas=True,
            limite=MAX_CANDIDATOS_CIUDADANO,
            incluir_imagenes=False,
            incluir_evidencias=foto_activa,
        )

    candidatos_crudos = (resultado_interno or {}).get('posibles_duplicados') or []
    candidatos_limitados = [
        candidato
        for candidato in list(candidatos_crudos)
        if isinstance(candidato, Mapping)
        and (
            not dinov2_activo
            or (bool(candidato.get('coincidencia_imagen')) and bool(candidato.get('coincidencia_gps')))
        )
    ][:MAX_CANDIDATOS_CIUDADANO]

    dominios_permitidos: Sequence[str] = ()
    urls_autorizadas_por_peticion: Dict[int, Set[str]] = {}
    if foto_activa and candidatos_limitados:
        dominios_permitidos = obtener_config_imagen_hash()['dominios_permitidos']
        ids_candidatos = [
            pid
            for pid in (_normalizar_id_peticion(c.get('id_peticion')) for c in candidatos_limitados)
            if pid is not None
        ]
        if ids_candidatos:
            try:
                urls_autorizadas_por_peticion = obtener_urls_autorizadas(ids_candidatos) or {}
            except Exception:
                logger.exception(
                    "Error al consultar autorizaciones de fotos para el detector ciudadano; "
                    "se omiten fotografías en la respuesta."
                )
                urls_autorizadas_por_peticion = {}

    posibles_duplicados = [
        _mapear_candidato_ciudadano(
            candidato,
            foto_activa=foto_activa,
            dominios_permitidos=dominios_permitidos,
            urls_autorizadas=urls_autorizadas_por_peticion.get(
                _normalizar_id_peticion(candidato.get('id_peticion'))
            ),
            incluir_visual_dinov2=dinov2_activo,
        )
        for candidato in candidatos_limitados
    ]
    if dinov2_activo:
        posibles_duplicados = sorted(
            posibles_duplicados,
            key=lambda c: (
                not c.get('coincidencia_imagen', False),
                not c.get('coincidencia_gps', False),
                -(
                    float(c['porcentaje_similitud_visual'])
                    if c.get('porcentaje_similitud_visual') is not None
                    else -1.0
                ),
                not c['tiene_fotografia'],
            ),
        )
    else:
        posibles_duplicados = sorted(
            posibles_duplicados,
            key=lambda c: not c['tiene_fotografia'],
        )

    hay_duplicados = len(posibles_duplicados) > 0
    logger.info(
        "Detector ciudadano duplicados: dinov2=%s gps=%s fotos_req=%d crudos=%d filtrados=%d autorizadas=%d",
        dinov2_activo,
        latitud is not None and longitud is not None,
        int(bool(foto)) + len(fotos or ()),
        len(candidatos_crudos),
        len(posibles_duplicados),
        sum(1 for c in posibles_duplicados if c.get('tiene_fotografia')),
    )
    return {
        'advertencia_duplicado': hay_duplicados,
        'posible_duplicado': hay_duplicados,
        'total_candidatos': len(posibles_duplicados),
        'mensaje': MENSAJE_CON_DUPLICADOS if hay_duplicados else MENSAJE_SIN_DUPLICADOS,
        'posibles_duplicados': posibles_duplicados,
    }
