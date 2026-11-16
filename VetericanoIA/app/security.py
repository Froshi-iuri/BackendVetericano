"""Validaciones de autenticación interna y decodificación segura de imágenes."""

from __future__ import annotations

import hmac
import io
import logging
import threading
from typing import Optional, Sequence

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

HEADER_SECRETO_INTERNO = 'X-Vetericano-IA-Secret'
FORMATOS_IMAGEN_SOPORTADOS = ('JPEG', 'PNG', 'WEBP', 'MPO')
CHUNK_SIZE_LECTURA = 16384
_PIL_LIMIT_LOCK = threading.Lock()


class AuthNoConfiguradaError(RuntimeError):
    """El microservicio no tiene configurado el secreto compartido interno."""


class AuthFaltanteError(PermissionError):
    """No se proporcionó cabecera de autenticación interna."""


class AuthInvalidaError(PermissionError):
    """El secreto proporcionado no coincide con el configurado."""


class PayloadDemasiadoGrandeError(ValueError):
    """El archivo supera el límite máximo permitido en bytes."""


class ImagenInvalidaError(ValueError):
    """Los bytes enviados no corresponden a una imagen válida y permitida."""


def verificar_secreto_interno(header_valor: Optional[str], secreto_esperado: str) -> None:
    """Valida en tiempo constante el secreto interno obligatorio (fail-closed)."""
    if not secreto_esperado:
        raise AuthNoConfiguradaError(
            'El secreto interno IA_INTERNAL_SHARED_SECRET no está configurado.'
        )
    if not header_valor or not header_valor.strip():
        raise AuthFaltanteError('Falta la cabecera de autenticación interna.')
    if not hmac.compare_digest(header_valor.strip(), secreto_esperado):
        raise AuthInvalidaError('Credencial interna inválida.')


async def leer_upload_con_limite(upload_file: UploadFile, max_bytes: int) -> bytes:
    """Lee un archivo subido en bloques acotados evitando cargas ilimitadas en RAM."""
    acumulado = bytearray()
    try:
        while True:
            chunk = await upload_file.read(CHUNK_SIZE_LECTURA)
            if not chunk:
                break
            if len(acumulado) + len(chunk) > max_bytes:
                raise PayloadDemasiadoGrandeError(
                    f'La imagen excede el límite máximo de {max_bytes} bytes.'
                )
            acumulado.extend(chunk)
    finally:
        await upload_file.close()

    if not acumulado:
        raise ImagenInvalidaError('El archivo de imagen está vacío.')
    return bytes(acumulado)


def decodificar_y_validar_imagen_bytes(
    contenido: bytes,
    *,
    formatos_permitidos: Sequence[str] = FORMATOS_IMAGEN_SOPORTADOS,
    max_bytes: int = 2 * 1024 * 1024,
    dimension_minima: int = 16,
    max_pixeles: int = 16_000_000,
) -> Image.Image:
    """
    Valida tamaño, formato permitido, dimensiones mínimas y protección contra
    bombas de descompresión, retornando una instancia PIL normalizada en RGB.
    """
    if not contenido:
        raise ImagenInvalidaError('El contenido de la imagen está vacío.')
    if len(contenido) > max_bytes:
        raise PayloadDemasiadoGrandeError(
            f'La imagen excede el máximo de {max_bytes} bytes ({len(contenido)}).'
        )

    formatos_upper = {str(fmt).upper() for fmt in formatos_permitidos if fmt}
    with _PIL_LIMIT_LOCK:
        anterior_max = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixeles
        try:
            with Image.open(io.BytesIO(contenido)) as img:
                formato = (img.format or '').upper()
                if formatos_upper and formato not in formatos_upper:
                    raise ImagenInvalidaError(
                        f"Formato de imagen no soportado: {formato or 'desconocido'}."
                    )
                img.load()
                ancho, alto = img.size
                if ancho < dimension_minima or alto < dimension_minima:
                    raise ImagenInvalidaError(
                        f'Dimensiones de imagen insuficientes ({ancho}x{alto}).'
                    )
                if (ancho * alto) > max_pixeles:
                    raise ImagenInvalidaError(
                        f'La resolución excede el máximo de píxeles ({ancho}x{alto}).'
                    )
                return ImageOps.exif_transpose(img).convert('RGB')
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            if isinstance(exc, ImagenInvalidaError):
                raise
            raise ImagenInvalidaError(f'Imagen dañada o no decodificable: {exc}') from exc
        finally:
            Image.MAX_IMAGE_PIXELS = anterior_max
