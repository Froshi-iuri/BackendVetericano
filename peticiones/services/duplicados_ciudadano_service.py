"""
Servicio ciudadano de detección de posibles reportes duplicados.

Envuelve al detector interno `detectar_duplicados_desde_solicitud` con parámetros
fijos en el servidor, sin persistencia en base de datos y aplicando una lista
blanca estricta de salida para no exponer información sensible ni URLs originales.
"""

import base64
import hashlib
import io
import json
import logging
import math
import re
from typing import AbstractSet, Any, Callable, Dict, List, Mapping, Optional, Sequence, Set
from urllib.parse import quote, urlparse

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.urls import reverse
from PIL import Image, ImageOps, UnidentifiedImageError

from peticiones.services.dinov2_service import (
    ADVERTENCIA_METRICA_VISUAL_DEFAULT,
    TIPO_METRICA_VISUAL,
    es_dinov2_activo,
)
from peticiones.services.duplicados_service import detectar_duplicados_desde_solicitud
from peticiones.services.foto_autorizada_service import (
    obtener_urls_autorizadas,
    obtener_urls_reales_peticion,
    url_foto_esta_habilitada_para_peticion,
)
from peticiones.services.imagen_hash_service import (
    FORMATOS_IMAGEN_SOPORTADOS,
    MAX_PIXELES_IMAGEN,
    MIN_DIMENSION_IMAGEN,
    _descargar_bytes_url_validada,
    obtener_config_imagen_hash,
    validar_url_cloudinary_segura,
)
from users.constants import ROL_PETICIONARIO

logger = logging.getLogger(__name__)

MAX_CANDIDATOS_CIUDADANO = 5
UMBRAL_CERCANIA_MUY_CERCA_METROS = 50.0
UMBRAL_SIMILITUD_ALTA = 0.60
TRANSFORMACION_MINIATURA_CIUDADANO = 'e_blur_faces/c_fill,g_auto,w_480,h_360,q_auto,f_auto'
MENSAJE_CON_DUPLICADOS = 'Se encontraron reportes similares.'
MENSAJE_SIN_DUPLICADOS = 'No se encontraron reportes similares.'

MINIATURA_ANCHO_MAX = 480
MINIATURA_ALTO_MAX = 360
MINIATURA_TTL_DEFAULT_SEGUNDOS = 300
MAX_LARGO_TOKEN_MINIATURA = 512

_PATRON_TRANSFORMACION_CLOUDINARY = re.compile(r'^[a-zA-Z]{1,3}_')
_PATRON_HEX_24 = re.compile(r'^[0-9a-f]{24}$')
_MARCADOR_IMAGE_UPLOAD = '/image/upload/'


def _obtener_clave_fernet_miniatura() -> bytes:
    secreto = (
        getattr(settings, 'DUPLICADOS_MINIATURA_TOKEN_SECRET', '')
        or getattr(settings, 'SECRET_KEY', '')
        or 'vetericano-fallback-secret'
    )
    digest = hashlib.sha256(
        b'vetericano-miniatura-ciudadano-v1:' + str(secreto).encode('utf-8')
    ).digest()
    return base64.urlsafe_b64encode(digest)


def _obtener_ttl_miniatura_segundos() -> int:
    try:
        raw = int(
            getattr(
                settings,
                'DUPLICADOS_CIUDADANO_MINIATURA_TTL_SEGUNDOS',
                MINIATURA_TTL_DEFAULT_SEGUNDOS,
            )
        )
    except (TypeError, ValueError):
        return MINIATURA_TTL_DEFAULT_SEGUNDOS
    return max(30, min(raw, 900))


def _hash_url_miniatura(url: str) -> str:
    return hashlib.sha256(url.strip().encode('utf-8')).hexdigest()[:24]


def generar_token_miniatura_ciudadano(
    *,
    id_peticion: int,
    url_imagen: str,
    id_usuario: Optional[int] = None,
) -> str:
    """
    Genera un token opaco cifrado y firmado (Fernet AES-128 + HMAC-SHA256 + timestamp)
    que oculta el identificador de la petición y la URL original de Cloudinary.
    """
    uid_val: Optional[int] = None
    if id_usuario is not None and not isinstance(id_usuario, bool):
        try:
            uid_int = int(id_usuario)
            if uid_int > 0:
                uid_val = uid_int
        except (TypeError, ValueError):
            uid_val = None

    payload = {
        'p': int(id_peticion),
        'h': _hash_url_miniatura(url_imagen),
        'u': uid_val,
    }
    raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return Fernet(_obtener_clave_fernet_miniatura()).encrypt(raw).decode('ascii')


def decodificar_token_miniatura_ciudadano(token: Any) -> Optional[Dict[str, Any]]:
    """
    Valida y desencripta el token efímero del proxy de miniaturas.
    Retorna None ante manipulación, formato inválido o expiración de TTL.
    """
    if not isinstance(token, str):
        return None
    token_limpio = token.strip()
    if (
        not token_limpio
        or len(token_limpio) > MAX_LARGO_TOKEN_MINIATURA
        or any(ch.isspace() for ch in token_limpio)
    ):
        return None

    try:
        raw = Fernet(_obtener_clave_fernet_miniatura()).decrypt(
            token_limpio.encode('ascii'),
            ttl=_obtener_ttl_miniatura_segundos(),
        )
        datos = json.loads(raw.decode('utf-8'))
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        return None

    if not isinstance(datos, dict):
        return None

    pid = datos.get('p')
    url_hash = datos.get('h')
    uid = datos.get('u')

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if not isinstance(url_hash, str) or not _PATRON_HEX_24.match(url_hash):
        return None
    if uid is not None and (isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0):
        return None

    return {'p': pid, 'h': url_hash, 'u': uid}


def url_imagen_cloudinary_apta(
    url: Any,
    dominios_permitidos: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """
    Valida estrictamente que `url` sea una imagen Cloudinary segura apta para
    habilitación y derivación de miniatura ciudadana: HTTPS, dominio permitido,
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


def _construir_url_cloudinary_miniatura_interna(
    url: Any,
    *,
    dominios_permitidos: Sequence[str],
) -> Optional[str]:
    """
    Construye únicamente en el servidor la URL transformada de Cloudinary con
    difuminado de rostros y recorte (`e_blur_faces/c_fill,g_auto,w_480,h_360,q_auto,f_auto`).
    Esta URL jamás se devuelve en la respuesta JSON al cliente ciudadano.
    """
    url_apta = url_imagen_cloudinary_apta(url, dominios_permitidos=dominios_permitidos)
    if not url_apta:
        return None

    prefijo, sufijo = url_apta.split(_MARCADOR_IMAGE_UPLOAD, 1)
    miniatura = f"{prefijo}{_MARCADOR_IMAGE_UPLOAD}{TRANSFORMACION_MINIATURA_CIUDADANO}/{sufijo}"
    if miniatura == url_apta:
        return None
    return miniatura


def _construir_url_proxy_por_defecto(token: str) -> str:
    ruta = f"{reverse('miniatura-duplicado-ciudadano')}?t={quote(token, safe='')}"
    base = str(getattr(settings, 'DUPLICADOS_CIUDADANO_PUBLIC_BASE_URL', '') or '').strip().rstrip('/')
    if base:
        return f"{base}{ruta}"
    return ruta


def _extraer_miniatura_candidato(
    candidato: Mapping[str, Any],
    *,
    dominios_permitidos: Sequence[str],
    urls_autorizadas: Optional[AbstractSet[str]] = None,
    id_peticion: Optional[int] = None,
    id_usuario_consulta: Optional[int] = None,
    miniatura_url_builder: Optional[Callable[[str], str]] = None,
) -> Optional[str]:
    """
    Obtiene una URL de proxy firmada/cifrada para la primera fotografía válida del
    candidato cuya publicación esté vigente y habilitada en `PeticionFotoAutorizada`.
    """
    if not urls_autorizadas or id_peticion is None:
        return None

    urls_candidatas: List[Any] = [candidato.get('foto')]
    fotos = candidato.get('fotos')
    if isinstance(fotos, (list, tuple)):
        urls_candidatas.extend(fotos)

    builder = miniatura_url_builder or _construir_url_proxy_por_defecto

    for url in urls_candidatas:
        if not isinstance(url, str):
            continue
        url_limpia = url.strip()
        if not url_limpia or url_limpia not in urls_autorizadas:
            continue
        if _construir_url_cloudinary_miniatura_interna(
            url_limpia,
            dominios_permitidos=dominios_permitidos,
        ) is None:
            continue
        token = generar_token_miniatura_ciudadano(
            id_peticion=id_peticion,
            url_imagen=url_limpia,
            id_usuario=id_usuario_consulta,
        )
        return builder(token)
    return None


def _sanitizar_miniatura_bytes(contenido: Optional[bytes], *, max_bytes: int) -> Optional[bytes]:
    """
    Valida dimensiones/formato con Pillow, normaliza orientación, redimensiona a
    480x360 máximo y re-codifica a JPEG limpio eliminando EXIF, GPS, ICC y XMP.
    """
    if not contenido or len(contenido) > max_bytes:
        return None

    formatos_upper = {str(fmt).upper() for fmt in FORMATOS_IMAGEN_SOPORTADOS if fmt}
    anterior_max = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_PIXELES_IMAGEN
    try:
        with Image.open(io.BytesIO(contenido)) as img:
            formato = (img.format or '').upper()
            if formatos_upper and formato not in formatos_upper:
                return None

            img.load()
            ancho, alto = img.size
            if ancho < MIN_DIMENSION_IMAGEN or alto < MIN_DIMENSION_IMAGEN:
                return None
            if (ancho * alto) > MAX_PIXELES_IMAGEN:
                return None

            transpuesta = ImageOps.exif_transpose(img)
            if transpuesta.mode in ('RGBA', 'LA') or (
                transpuesta.mode == 'P' and 'transparency' in transpuesta.info
            ):
                rgba = transpuesta.convert('RGBA')
                fondo = Image.new('RGB', rgba.size, (245, 245, 245))
                fondo.paste(rgba, mask=rgba.split()[-1])
                procesada = fondo
            else:
                procesada = transpuesta.convert('RGB')

            resample = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', Image.LANCZOS)
            procesada.thumbnail((MINIATURA_ANCHO_MAX, MINIATURA_ALTO_MAX), resample)

            salida = io.BytesIO()
            procesada.save(salida, format='JPEG', quality=82, optimize=True)
            return salida.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        logger.warning("Miniatura descartada al sanitizar con Pillow: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Error inesperado sanitizando miniatura con Pillow: %s", exc)
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = anterior_max


def obtener_miniatura_sanitizada_desde_token(token: Any) -> Optional[bytes]:
    """
    Resuelve un token cifrado del proxy ciudadano re-validando en vivo:
    1. Token íntegro y vigente dentro de TTL.
    2. Feature flag de fotos activa.
    3. Usuario consultante (si viene en el token) activo con rol Peticionario.
    4. URL coincidente por hash perteneciente realmente a esa petición y habilitada en BD
       (sin revocación ni moderación ni estado bloqueado).
    5. Descarga segura desde Cloudinary solo de la variante `e_blur_faces` (fail-closed).
    6. Re-codificación JPEG sin metadatos EXIF/personales.
    """
    if not bool(getattr(settings, 'DUPLICADOS_CIUDADANO_FOTO_ACTIVA', True)):
        return None

    datos = decodificar_token_miniatura_ciudadano(token)
    if not datos:
        return None

    id_peticion = datos['p']
    url_hash = datos['h']
    uid = datos['u']

    if uid is not None:
        from users.models import Usuarios

        try:
            usuario_ok = Usuarios.objects.filter(
                pk=uid,
                activo=True,
                id_rol_id=ROL_PETICIONARIO,
            ).exists()
        except Exception:
            logger.exception("Error verificando usuario solicitante del proxy de miniatura.")
            return None
        if not usuario_ok:
            return None

    try:
        urls_reales = obtener_urls_reales_peticion(id_peticion)
    except Exception:
        logger.exception("Error consultando evidencias para proxy de miniatura.")
        return None

    url_original = None
    for candidata in urls_reales:
        if _hash_url_miniatura(candidata) == url_hash:
            url_original = candidata
            break
    if not url_original:
        return None

    try:
        habilitada = url_foto_esta_habilitada_para_peticion(id_peticion, url_original)
    except Exception:
        logger.exception("Error verificando habilitación vigente de miniatura.")
        return None
    if not habilitada:
        return None

    cfg = obtener_config_imagen_hash()
    dominios = cfg['dominios_permitidos']
    url_cloudinary_derivada = _construir_url_cloudinary_miniatura_interna(
        url_original,
        dominios_permitidos=dominios,
    )
    if not url_cloudinary_derivada:
        return None

    raw_bytes = _descargar_bytes_url_validada(
        url_cloudinary_derivada,
        timeout=cfg['timeout'],
        max_bytes=cfg['max_bytes'],
        dominios_permitidos=dominios,
        validar_dns=True,
    )
    if not raw_bytes:
        return None

    return _sanitizar_miniatura_bytes(raw_bytes, max_bytes=cfg['max_bytes'])


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
    id_usuario_consulta: Optional[int] = None,
    miniatura_url_builder: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    coincidencia_texto = bool(candidato.get('coincidencia_texto', False))
    coincidencia_gps = bool(candidato.get('coincidencia_gps', False))
    id_peticion = _normalizar_id_peticion(candidato.get('id_peticion'))
    foto = (
        _extraer_miniatura_candidato(
            candidato,
            dominios_permitidos=dominios_permitidos,
            urls_autorizadas=urls_autorizadas,
            id_peticion=id_peticion,
            id_usuario_consulta=id_usuario_consulta,
            miniatura_url_builder=miniatura_url_builder,
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
        metodo_imagen = candidato.get('metodo_imagen')
        porcentaje_raw = candidato.get('porcentaje_similitud_visual')
        if (
            porcentaje_raw is None
            and metodo_imagen in ('dinov2_small', None)
            and candidato.get('similitud_imagen') is not None
        ):
            try:
                porcentaje_raw = round(float(candidato['similitud_imagen']) * 100.0, 2)
            except (TypeError, ValueError):
                porcentaje_raw = None

        porcentaje_final = (
            round(float(porcentaje_raw), 2)
            if porcentaje_raw is not None and not isinstance(porcentaje_raw, bool)
            else None
        )
        similitud_visual_label = _clasificar_similitud_visual(porcentaje_final)
        advertencia_default = (
            'Similitud visual no disponible; advertencia generada por proximidad GPS (<= 200 m).'
            if porcentaje_final is None
            else ADVERTENCIA_METRICA_VISUAL_DEFAULT
        )
        resultado.update({
            'coincidencia_imagen': bool(candidato.get('coincidencia_imagen', False)),
            'porcentaje_similitud_visual': porcentaje_final,
            'similitud_visual': similitud_visual_label,
            'metodo_imagen': metodo_imagen,
            'es_certeza_identidad': False,
            'tipo_metrica_visual': candidato.get('tipo_metrica_visual') or TIPO_METRICA_VISUAL,
            'advertencia_metrica_visual': (
                candidato.get('advertencia_metrica_visual') or advertencia_default
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
    id_usuario_consulta: Optional[int] = None,
    miniatura_url_builder: Optional[Callable[[str], str]] = None,
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
            or bool(candidato.get('coincidencia_gps'))
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
            id_usuario_consulta=id_usuario_consulta,
            miniatura_url_builder=miniatura_url_builder,
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
