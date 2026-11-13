"""
Servicio de cálculo y comparación perceptual de fotografías (Fase 3) con Pillow y dHash 64-bit.

Características principales:
- Calcula hashes perceptuales dHash de 64 bits utilizando Pillow (`Image`, `ImageOps`).
- Compara hashes mediante distancia de Hamming con umbral configurable (por defecto 10 bits).
- Descarga imágenes únicamente desde dominios Cloudinary permitidos sobre HTTPS,
  con validación DNS anti-SSRF (bloqueo de IPs privadas/loopback/link-local),
  bloqueo de redirecciones inseguras, límite de tamaño de bytes y timeout corto.
- Degradación tolerante: si una descarga o decodificación falla, retorna `None`
  para que las fases de texto y GPS continúen funcionando normalmente.
"""

import io
import ipaddress
import logging
import socket
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

from peticiones.utils import detectar_tipo_archivo

logger = logging.getLogger(__name__)

UMBRAL_HAMMING_DEFAULT = 10
TIMEOUT_IMAGEN_DEFAULT = 2.5
MAX_BYTES_IMAGEN_DEFAULT = 2 * 1024 * 1024  # 2 MB
MAX_IMAGENES_POR_PETICION_DEFAULT = 3
MAX_CANDIDATOS_IMAGEN_DEFAULT = 10
DOMINIOS_CLOUDINARY_DEFAULT = ('res.cloudinary.com',)

MAX_REDIRECTS_PERMITIDOS = 2
MAX_PIXELES_IMAGEN = 16_000_000  # ~4000x4000 px para mitigar bombas de descompresión
CHUNK_SIZE_DESCARGA = 16384
MAX_LARGO_URL = 2048


def obtener_config_imagen_hash() -> Dict[str, Any]:
    """Obtiene parámetros configurables para la comparación perceptual dHash y descarga segura."""
    try:
        umbral_hamming = int(getattr(settings, 'DUPLICADOS_UMBRAL_HAMMING', UMBRAL_HAMMING_DEFAULT))
    except (TypeError, ValueError):
        umbral_hamming = UMBRAL_HAMMING_DEFAULT
    umbral_hamming = max(0, min(umbral_hamming, 64))

    try:
        timeout = float(getattr(settings, 'DUPLICADOS_IMAGEN_TIMEOUT_SEGUNDOS', TIMEOUT_IMAGEN_DEFAULT))
    except (TypeError, ValueError):
        timeout = TIMEOUT_IMAGEN_DEFAULT
    timeout = max(0.5, min(timeout, 10.0))

    try:
        max_bytes = int(getattr(settings, 'DUPLICADOS_IMAGEN_MAX_BYTES', MAX_BYTES_IMAGEN_DEFAULT))
    except (TypeError, ValueError):
        max_bytes = MAX_BYTES_IMAGEN_DEFAULT
    max_bytes = max(32 * 1024, min(max_bytes, 10 * 1024 * 1024))

    try:
        max_imagenes = int(getattr(settings, 'DUPLICADOS_MAX_IMAGENES_POR_PETICION', MAX_IMAGENES_POR_PETICION_DEFAULT))
    except (TypeError, ValueError):
        max_imagenes = MAX_IMAGENES_POR_PETICION_DEFAULT
    max_imagenes = max(1, min(max_imagenes, 10))

    try:
        max_candidatos = int(getattr(settings, 'DUPLICADOS_MAX_CANDIDATOS_IMAGEN', MAX_CANDIDATOS_IMAGEN_DEFAULT))
    except (TypeError, ValueError):
        max_candidatos = MAX_CANDIDATOS_IMAGEN_DEFAULT
    max_candidatos = max(1, min(max_candidatos, 30))

    dominios_cfg = getattr(settings, 'DUPLICADOS_CLOUDINARY_DOMAINS', DOMINIOS_CLOUDINARY_DEFAULT)
    if isinstance(dominios_cfg, str):
        dominios = tuple(d.strip().lower().rstrip('.') for d in dominios_cfg.split(',') if d.strip())
    elif isinstance(dominios_cfg, (list, tuple, set)):
        dominios = tuple(str(d).strip().lower().rstrip('.') for d in dominios_cfg if str(d).strip())
    else:
        dominios = DOMINIOS_CLOUDINARY_DEFAULT

    if not dominios:
        dominios = DOMINIOS_CLOUDINARY_DEFAULT

    return {
        'umbral_hamming': umbral_hamming,
        'timeout': timeout,
        'max_bytes': max_bytes,
        'max_imagenes': max_imagenes,
        'max_candidatos': max_candidatos,
        'dominios_permitidos': dominios,
    }


def _es_ip_publica_segura(ip_str: str) -> bool:
    """Verifica que una dirección IP sea globalmente enrutable (no privada, loopback ni link-local)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    ip_mapeada = getattr(ip, 'ipv4_mapped', None)
    if ip_mapeada is not None:
        ip = ip_mapeada

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    return ip.is_global


def _validar_destino_dns(hostname: str, port: int = 443) -> bool:
    """
    Resuelve DNS del hostname destino y garantiza que TODAS las IPs resueltas sean públicas
    para mitigar SSRF / DNS rebinding hacia red interna o metadatos cloud.
    """
    if not hostname:
        return False
    try:
        # Si es literal IP, rechazar directamente (solo se aceptan dominios Cloudinary autorizados)
        ipaddress.ip_address(hostname)
        return False
    except ValueError:
        pass

    try:
        resultados = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return False

    if not resultados:
        return False

    for familia, _, _, _, sockaddr in resultados:
        if familia in (socket.AF_INET, socket.AF_INET6) and sockaddr:
            ip_str = sockaddr[0]
            if not _es_ip_publica_segura(ip_str):
                logger.warning("SSRF bloqueado: hostname '%s' resolvió a IP no pública.", hostname)
                return False
    return True


def validar_url_cloudinary_segura(
    url: Optional[str],
    *,
    dominios_permitidos: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """
    Valida estrictamente que la URL apunte por HTTPS a un dominio Cloudinary permitido,
    sin credenciales embebidas, sin puertos arbitrarios y correspondiendo a una imagen.
    """
    if not url or not isinstance(url, str):
        return None

    url_limpia = url.strip()
    if not url_limpia or len(url_limpia) > MAX_LARGO_URL:
        return None

    if any(ch.isspace() or ord(ch) < 32 for ch in url_limpia):
        return None

    try:
        parsed = urlparse(url_limpia)
    except ValueError:
        return None

    if parsed.scheme.lower() != 'https':
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    try:
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None

    hostname = (parsed.hostname or '').lower().rstrip('.')
    if not hostname:
        return None

    dominios = tuple(
        d.lower().rstrip('.')
        for d in (dominios_permitidos or obtener_config_imagen_hash()['dominios_permitidos'])
    )
    if hostname not in dominios:
        return None

    ruta = parsed.path or ''
    if '..' in ruta.split('/'):
        return None

    if detectar_tipo_archivo(url_limpia) != 'imagen':
        return None

    return url_limpia


def construir_url_miniatura_cloudinary(url_segura: str) -> str:
    """
    Construye una URL de entrega liviana de Cloudinary (on-the-fly) sin modificar
    la imagen original almacenada en Cloudinary.
    """
    marcador = '/image/upload/'
    if marcador not in url_segura:
        return url_segura

    prefijo, sufijo = url_segura.split(marcador, 1)
    if not sufijo:
        return url_segura

    primer_segmento = sufijo.split('/', 1)[0]
    # No alterar URLs firmadas (s--...--) ni URLs que ya traen transformaciones explícitas
    if primer_segmento.startswith('s--') or any(
        token in primer_segmento for token in ('w_', 'h_', 'c_', 'f_', 'q_')
    ):
        return url_segura

    return f"{prefijo}{marcador}w_128,h_128,c_limit,f_jpg/{sufijo}"


def _descargar_bytes_url_validada(
    url_inicial: str,
    *,
    timeout: float,
    max_bytes: int,
    dominios_permitidos: Sequence[str],
    validar_dns: bool = True,
) -> Optional[bytes]:
    """
    Descarga de forma segura los bytes de imagen bloqueando redirecciones inseguras,
    validando Content-Type y aplicando límite estricto de tamaño y timeout.
    """
    url_actual = url_inicial

    for _ in range(MAX_REDIRECTS_PERMITIDOS + 1):
        url_validada = validar_url_cloudinary_segura(url_actual, dominios_permitidos=dominios_permitidos)
        if not url_validada:
            return None

        parsed = urlparse(url_validada)
        hostname = (parsed.hostname or '').lower().rstrip('.')
        if validar_dns and not _validar_destino_dns(hostname, 443):
            return None

        resp = None
        try:
            resp = requests.get(
                url_validada,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
                headers={'Accept': 'image/*'},
            )

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location')
                if not location:
                    return None
                url_actual = urljoin(url_validada, location.strip())
                continue

            if resp.status_code != 200:
                return None

            content_type = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if content_type and not content_type.startswith('image/'):
                return None

            content_length = resp.headers.get('Content-Length')
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        return None
                except ValueError:
                    return None

            acumulado = bytearray()
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE_DESCARGA):
                if not chunk:
                    continue
                if len(acumulado) + len(chunk) > max_bytes:
                    return None
                acumulado.extend(chunk)

            if not acumulado:
                return None
            return bytes(acumulado)
        except requests.RequestException as exc:
            logger.warning("Fallo controlado descargando imagen Cloudinary (%s): %s", hostname, exc)
            return None
        except Exception as exc:
            logger.warning("Error inesperado descargando imagen Cloudinary (%s): %s", hostname, exc)
            return None
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

    return None


def descargar_imagen_segura(
    url: Optional[str],
    *,
    timeout: Optional[float] = None,
    max_bytes: Optional[int] = None,
    dominios_permitidos: Optional[Sequence[str]] = None,
    usar_miniatura: bool = True,
    validar_dns: bool = True,
) -> Optional[bytes]:
    """
    Descarga de forma segura una imagen desde Cloudinary validando HTTPS, dominio,
    resolución DNS pública, redirecciones, Content-Type, tamaño máximo y timeout corto.
    """
    cfg = obtener_config_imagen_hash()
    timeout_val = float(timeout) if timeout is not None else cfg['timeout']
    max_bytes_val = int(max_bytes) if max_bytes is not None else cfg['max_bytes']
    dominios = tuple(dominios_permitidos) if dominios_permitidos else cfg['dominios_permitidos']

    url_segura = validar_url_cloudinary_segura(url, dominios_permitidos=dominios)
    if not url_segura:
        return None

    if usar_miniatura:
        url_miniatura = construir_url_miniatura_cloudinary(url_segura)
        if url_miniatura != url_segura:
            contenido = _descargar_bytes_url_validada(
                url_miniatura,
                timeout=timeout_val,
                max_bytes=max_bytes_val,
                dominios_permitidos=dominios,
                validar_dns=validar_dns,
            )
            if contenido is not None:
                return contenido

    return _descargar_bytes_url_validada(
        url_segura,
        timeout=timeout_val,
        max_bytes=max_bytes_val,
        dominios_permitidos=dominios,
        validar_dns=validar_dns,
    )


def calcular_dhash_desde_imagen(imagen: Image.Image) -> Optional[int]:
    """
    Calcula el hash perceptual dHash de 64 bits sobre una instancia `PIL.Image.Image`.
    Normaliza orientación EXIF, convierte a escala de grises y redimensiona a 9x8.
    """
    if imagen is None:
        return None

    try:
        ancho, alto = imagen.size
        if ancho <= 0 or alto <= 0 or (ancho * alto) > MAX_PIXELES_IMAGEN:
            return None

        resample = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', Image.LANCZOS)
        imagen_norm = ImageOps.exif_transpose(imagen).convert('L').resize((9, 8), resample)
        pixeles = list(imagen_norm.get_flattened_data() if hasattr(imagen_norm, 'get_flattened_data') else imagen_norm.getdata())
    except Exception as exc:
        logger.warning("No se pudo calcular dHash desde imagen Pillow: %s", exc)
        return None

    if len(pixeles) != 72:
        return None

    hash_bits = 0
    for fila in range(8):
        offset = fila * 9
        for col in range(8):
            hash_bits = (hash_bits << 1) | (1 if pixeles[offset + col] > pixeles[offset + col + 1] else 0)
    return hash_bits


def calcular_dhash_desde_bytes(contenido: Optional[bytes]) -> Optional[int]:
    """Decodifica bytes de imagen de forma segura con Pillow y retorna su dHash de 64 bits."""
    if not contenido:
        return None

    anterior_max = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_PIXELES_IMAGEN
    try:
        with Image.open(io.BytesIO(contenido)) as img:
            img.load()
            return calcular_dhash_desde_imagen(img)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        logger.warning("Imagen dañada o no decodificable ignorada al calcular dHash: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Error inesperado procesando imagen con Pillow: %s", exc)
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = anterior_max


def formatear_dhash_hex(dhash_int: Optional[int]) -> Optional[str]:
    """Representación hexadecimal de 16 caracteres para un dHash de 64 bits."""
    if dhash_int is None:
        return None
    return f"{int(dhash_int) & ((1 << 64) - 1):016x}"


def _normalizar_hash_entero(hash_val: Any) -> int:
    if isinstance(hash_val, bool):
        raise ValueError("Hash perceptual inválido (bool).")
    if isinstance(hash_val, int):
        return hash_val & ((1 << 64) - 1)
    if isinstance(hash_val, str):
        return int(hash_val.strip(), 16) & ((1 << 64) - 1)
    raise ValueError("Formato de hash perceptual no soportado.")


def calcular_distancia_hamming(hash1: Any, hash2: Any) -> int:
    """Calcula la distancia de Hamming (0..64 bits) entre dos hashes dHash de 64 bits."""
    h1 = _normalizar_hash_entero(hash1)
    h2 = _normalizar_hash_entero(hash2)
    return (h1 ^ h2).bit_count()


def calcular_similitud_imagen(distancia_hamming: int) -> float:
    """Convierte distancia de Hamming (0..64) en similitud normalizada [0.0, 1.0]."""
    dist = max(0, min(64, int(distancia_hamming)))
    return round(1.0 - (dist / 64.0), 4)


def obtener_dhash_desde_url(
    url: Optional[str],
    *,
    cache: Optional[Dict[str, Optional[int]]] = None,
    timeout: Optional[float] = None,
    max_bytes: Optional[int] = None,
    dominios_permitidos: Optional[Sequence[str]] = None,
) -> Optional[int]:
    """
    Descarga una URL Cloudinary válida y calcula su dHash de 64 bits, utilizando caché
    en memoria por solicitud cuando se proporciona `cache`.
    """
    if not url or not isinstance(url, str):
        return None

    clave = url.strip()
    if cache is not None and clave in cache:
        return cache[clave]

    contenido = descargar_imagen_segura(
        clave,
        timeout=timeout,
        max_bytes=max_bytes,
        dominios_permitidos=dominios_permitidos,
    )
    resultado = calcular_dhash_desde_bytes(contenido) if contenido is not None else None
    if cache is not None:
        cache[clave] = resultado
    return resultado


def calcular_hashes_urls(
    urls: Iterable[str],
    *,
    max_imagenes: Optional[int] = None,
    cache: Optional[Dict[str, Optional[int]]] = None,
) -> List[int]:
    """
    Obtiene hashes dHash únicos y válidos a partir de una lista de URLs,
    respetando el límite máximo de imágenes por petición y tolerando fallos individuales.
    """
    cfg = obtener_config_imagen_hash()
    limite = max_imagenes if max_imagenes is not None else cfg['max_imagenes']

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

    hashes: List[int] = []
    for url in urls_limpias:
        h = obtener_dhash_desde_url(url, cache=cache)
        if h is not None:
            hashes.append(h)
    return hashes


def comparar_conjuntos_hashes(
    hashes_origen: Sequence[Any],
    hashes_candidato: Sequence[Any],
    *,
    umbral_hamming: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Compara múltiples dHash de la petición consultada contra múltiples dHash de una petición
    candidata y conserva la mejor coincidencia (menor distancia de Hamming).
    """
    cfg = obtener_config_imagen_hash()
    umbral = cfg['umbral_hamming'] if umbral_hamming is None else max(0, min(int(umbral_hamming), 64))

    if not hashes_origen or not hashes_candidato:
        return {
            'comparado': False,
            'coincidencia_imagen': False,
            'distancia_hamming': None,
            'similitud_imagen': None,
        }

    mejor_distancia: Optional[int] = None
    for h1 in hashes_origen:
        for h2 in hashes_candidato:
            try:
                dist = calcular_distancia_hamming(h1, h2)
            except ValueError:
                continue
            if mejor_distancia is None or dist < mejor_distancia:
                mejor_distancia = dist
                if mejor_distancia == 0:
                    break
        if mejor_distancia == 0:
            break

    if mejor_distancia is None:
        return {
            'comparado': False,
            'coincidencia_imagen': False,
            'distancia_hamming': None,
            'similitud_imagen': None,
        }

    return {
        'comparado': True,
        'coincidencia_imagen': mejor_distancia <= umbral,
        'distancia_hamming': mejor_distancia,
        'similitud_imagen': calcular_similitud_imagen(mejor_distancia),
    }
