"""
Servicio para registrar, moderar, revocar y consultar la habilitación de fotografías
como miniaturas sanitizadas en el flujo ciudadano de detección de posibles duplicados.
"""

import logging
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from peticiones.models import (
    EvidenciaPeticiones,
    PeticionFotoAutorizada,
    PeticionPosibleDuplicado,
    Peticiones,
)
from peticiones.services.imagen_hash_service import obtener_config_imagen_hash
from peticiones.utils import normalizar_texto_juridico, registrar_historial_peticion
from users.api.permissions import es_admin_o_juridico, es_peticionario
from users.constants import ROL_PETICIONARIO

logger = logging.getLogger(__name__)

VERSION_TEXTO_AUTORIZACION_FOTO = 'v1'
VERSION_TEXTO_MODERACION_APROBADA = 'mod_aprobada'
VERSION_TEXTO_MODERACION_OCULTA = 'mod_oculta'
VERSION_TEXTO_REVOCADA_TITULAR = 'rev_titular'
VERSION_TEXTO_POLITICA_SISTEMA = 'politica_sis'

MARCADOR_BLOQUEO_GLOBAL_PETICION = '*'
MAX_FOTOS_AUTORIZADAS_POR_PETICION = 5
MAX_LARGO_RUTA_AUTORIZADA = 500

HISTORIAL_TIPO_MODERACION_FOTO = 'MODERACION_FOTO'
HISTORIAL_TIPO_REVOCACION_FOTO = 'REVOCACION_FOTO'

ESTADOS_PETICION_EXCLUIDOS_MINIATURA = frozenset({
    'duplicada',
    'duplicado',
    'rechazada',
    'rechazado',
    'anulada',
    'anulado',
    'cancelada',
    'cancelado',
    'descartada',
    'descartado',
    'borrador',
    'oculta',
    'oculto',
})


def _normalizar_ids_peticion(ids_peticion: Iterable[int]) -> List[int]:
    ids_limpios: List[int] = []
    vistos: Set[int] = set()
    for pid in ids_peticion or ():
        if pid is None or isinstance(pid, bool):
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        if pid_int > 0 and pid_int not in vistos:
            vistos.add(pid_int)
            ids_limpios.append(pid_int)
    return ids_limpios


def _filtrar_urls_cloudinary_aptas(
    urls_candidatas: Optional[Sequence[str]],
    *,
    urls_permitidas_set: Optional[Set[str]] = None,
) -> List[str]:
    from peticiones.services.duplicados_ciudadano_service import url_imagen_cloudinary_apta

    dominios_permitidos = obtener_config_imagen_hash()['dominios_permitidos']
    urls_validas: List[str] = []
    vistas: Set[str] = set()

    for raw in urls_candidatas or ():
        if not isinstance(raw, str):
            continue
        url_limpia = raw.strip()
        if not url_limpia or url_limpia in vistas:
            continue
        if urls_permitidas_set is not None and url_limpia not in urls_permitidas_set:
            continue
        url_apta = url_imagen_cloudinary_apta(url_limpia, dominios_permitidos)
        if not url_apta or len(url_apta) > MAX_LARGO_RUTA_AUTORIZADA:
            continue
        vistas.add(url_limpia)
        urls_validas.append(url_apta)
        if len(urls_validas) >= MAX_FOTOS_AUTORIZADAS_POR_PETICION:
            break

    return urls_validas


def obtener_urls_reales_peticion(id_peticion: int) -> List[str]:
    """
    Obtiene en orden determinista las URLs reales registradas en `Peticiones.foto`
    y `EvidenciaPeticiones.ruta_archivo` para una petición puntual.
    """
    urls: List[str] = []
    vistas: Set[str] = set()

    foto_principal = (
        Peticiones.objects
        .filter(pk=id_peticion)
        .values_list('foto', flat=True)
        .first()
    )
    if isinstance(foto_principal, str):
        limpia = foto_principal.strip()
        if limpia:
            vistas.add(limpia)
            urls.append(limpia)

    evidencias = (
        EvidenciaPeticiones.objects
        .filter(id_peticion_id=id_peticion)
        .exclude(ruta_archivo__isnull=True)
        .exclude(ruta_archivo='')
        .order_by('id_evidencia')
        .values_list('ruta_archivo', flat=True)
    )
    for ruta in evidencias:
        if not isinstance(ruta, str):
            continue
        limpia = ruta.strip()
        if limpia and limpia not in vistas:
            vistas.add(limpia)
            urls.append(limpia)

    return urls


def _es_estado_peticion_elegible(nombre_estado: Optional[str], estado_activo: Optional[bool]) -> bool:
    if estado_activo is False:
        return False
    norm = normalizar_texto_juridico(nombre_estado)
    if norm and norm in ESTADOS_PETICION_EXCLUIDOS_MINIATURA:
        return False
    return True


def _obtener_info_elegibilidad_peticiones(ids_limpios: Sequence[int]) -> Dict[int, Dict[str, object]]:
    """
    Evalúa en bloque si cada petición está en estado elegible, sin confirmación de copia
    duplicada ni responsable desactivado.
    """
    if not ids_limpios:
        return {}

    duplicadas_confirmadas: Set[int] = set()
    alertas_confirmadas = (
        PeticionPosibleDuplicado.objects
        .filter(estado_revision=PeticionPosibleDuplicado.ESTADO_CONFIRMADO)
        .filter(Q(peticion_menor_id__in=ids_limpios) | Q(peticion_mayor_id__in=ids_limpios))
        .values_list('peticion_menor_id', 'peticion_mayor_id', 'peticion_principal_id')
    )
    for id_menor, id_mayor, id_princ in alertas_confirmadas:
        if id_princ is None:
            continue
        if id_princ == id_menor and id_mayor is not None:
            duplicadas_confirmadas.add(id_mayor)
        elif id_princ == id_mayor and id_menor is not None:
            duplicadas_confirmadas.add(id_menor)

    filas = (
        Peticiones.objects
        .filter(id_peticion__in=ids_limpios)
        .values_list(
            'id_peticion',
            'id_estado__nombre',
            'id_estado__activo',
            'responsable_id',
            'responsable__activo',
            'responsable__id_rol_id',
        )
    )

    info: Dict[int, Dict[str, object]] = {}
    for pid, est_nombre, est_activo, resp_id, resp_activo, resp_rol in filas:
        if pid in duplicadas_confirmadas:
            info[pid] = {'elegible': False, 'es_peticionario_activo': False}
            continue
        if not _es_estado_peticion_elegible(est_nombre, est_activo):
            info[pid] = {'elegible': False, 'es_peticionario_activo': False}
            continue
        if resp_id is None or not bool(resp_activo):
            info[pid] = {'elegible': False, 'es_peticionario_activo': False}
            continue
        info[pid] = {
            'elegible': True,
            'es_peticionario_activo': resp_rol == ROL_PETICIONARIO,
        }
    return info


def _registrar_urls_habilitadas(
    *,
    peticion: Peticiones,
    autorizado_por,
    urls_validas: Sequence[str],
    version_texto: str,
) -> List[PeticionFotoAutorizada]:
    registros: List[PeticionFotoAutorizada] = []
    try:
        with transaction.atomic():
            bloqueo_global = (
                PeticionFotoAutorizada.objects
                .filter(
                    id_peticion=peticion,
                    ruta_archivo=MARCADOR_BLOQUEO_GLOBAL_PETICION,
                    revocada_en__isnull=False,
                )
                .exists()
            )
            if bloqueo_global:
                return []

            existentes_revocadas: Set[str] = set(
                PeticionFotoAutorizada.objects
                .filter(
                    id_peticion=peticion,
                    ruta_archivo__in=urls_validas,
                    revocada_en__isnull=False,
                )
                .values_list('ruta_archivo', flat=True)
            )
            for url_valida in urls_validas:
                if url_valida in existentes_revocadas:
                    continue
                obj, _ = PeticionFotoAutorizada.objects.get_or_create(
                    id_peticion=peticion,
                    ruta_archivo=url_valida,
                    defaults={
                        'autorizado_por': autorizado_por,
                        'version_texto': version_texto,
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

    urls_validas = _filtrar_urls_cloudinary_aptas(
        urls_autorizadas,
        urls_permitidas_set=urls_procesadas_set,
    )
    if not urls_validas:
        return []

    return _registrar_urls_habilitadas(
        peticion=peticion,
        autorizado_por=autorizado_por,
        urls_validas=urls_validas,
        version_texto=VERSION_TEXTO_AUTORIZACION_FOTO,
    )


def habilitar_fotos_por_politica_sistema(
    *,
    peticion: Optional[Peticiones],
    autorizado_por,
    urls_procesadas: Optional[Sequence[str]],
) -> List[PeticionFotoAutorizada]:
    """
    Habilita explícitamente en `PeticionFotoAutorizada` las URLs Cloudinary válidas
    de una nueva petición radicada por un `Peticionario` activo sin casilla de
    consentimiento en Android, únicamente cuando:
    - `DUPLICADOS_AUTO_HABILITAR_FOTOS_PETICIONARIO=True` y `DUPLICADOS_CIUDADANO_FOTO_ACTIVA=True`.
    - `autorizado_por` coincide con el `responsable` titular y es `Peticionario` activo.
    - La petición tiene un estado elegible activo (p. ej. no `Borrador`, `Rechazada` ni `Duplicada`).
    """
    if not bool(getattr(settings, 'DUPLICADOS_AUTO_HABILITAR_FOTOS_PETICIONARIO', False)):
        return []
    if not bool(getattr(settings, 'DUPLICADOS_CIUDADANO_FOTO_ACTIVA', True)):
        return []

    if peticion is None or getattr(peticion, 'id_peticion', None) is None or not urls_procesadas:
        return []
    if not es_peticionario(autorizado_por):
        return []

    uid_actor = getattr(autorizado_por, 'id_usuario', None) or getattr(autorizado_por, 'pk', None)
    if getattr(peticion, 'responsable_id', None) != uid_actor or uid_actor is None:
        return []

    estado_obj = getattr(peticion, 'id_estado', None)
    if estado_obj is not None and not _es_estado_peticion_elegible(
        getattr(estado_obj, 'nombre', None),
        getattr(estado_obj, 'activo', None),
    ):
        return []

    urls_validas = _filtrar_urls_cloudinary_aptas(urls_procesadas)
    if not urls_validas:
        return []

    return _registrar_urls_habilitadas(
        peticion=peticion,
        autorizado_por=autorizado_por,
        urls_validas=urls_validas,
        version_texto=VERSION_TEXTO_POLITICA_SISTEMA,
    )


def obtener_urls_autorizadas(ids_peticion: Iterable[int]) -> Dict[int, Set[str]]:
    """
    Obtiene en lote las URLs habilitadas y vigentes (`revocada_en__isnull=True`) en
    `PeticionFotoAutorizada` para las peticiones indicadas, respetando estrictamente
    revocaciones, moderación y elegibilidad de estado/responsable.

    Nunca interpreta la ausencia de filas en `PeticionFotoAutorizada` como permiso.
    """
    ids_limpios = _normalizar_ids_peticion(ids_peticion)
    if not ids_limpios:
        return {}

    filas = list(
        PeticionFotoAutorizada.objects
        .filter(id_peticion_id__in=ids_limpios)
        .values_list('id_peticion_id', 'ruta_archivo', 'revocada_en', 'version_texto')
    )
    if not filas:
        return {}

    bloqueos_globales: Set[int] = set()
    urls_revocadas: Dict[int, Set[str]] = {}
    activas_candidatas: List[Tuple[int, str, str]] = []

    for id_peticion, ruta_archivo, revocada_en, version_texto in filas:
        ruta_limpia = ruta_archivo.strip() if isinstance(ruta_archivo, str) else ''
        if not ruta_limpia:
            continue
        if revocada_en is not None:
            if ruta_limpia == MARCADOR_BLOQUEO_GLOBAL_PETICION:
                bloqueos_globales.add(id_peticion)
            else:
                urls_revocadas.setdefault(id_peticion, set()).add(ruta_limpia)
        elif ruta_limpia != MARCADOR_BLOQUEO_GLOBAL_PETICION:
            activas_candidatas.append((id_peticion, ruta_limpia, str(version_texto or '')))

    if not activas_candidatas:
        return {}

    ids_con_activas = [pid for pid, _, _ in activas_candidatas]
    info_peticiones = _obtener_info_elegibilidad_peticiones(ids_con_activas)

    autorizadas: Dict[int, Set[str]] = {}
    for id_peticion, ruta_limpia, version_texto in activas_candidatas:
        if id_peticion in bloqueos_globales:
            continue
        if ruta_limpia in urls_revocadas.get(id_peticion, ()):
            continue
        meta = info_peticiones.get(id_peticion)
        if not meta or not meta.get('elegible'):
            continue
        # Reportes internos (no creados por Peticionario) requieren aprobación explícita de moderación
        if not meta.get('es_peticionario_activo') and version_texto != VERSION_TEXTO_MODERACION_APROBADA:
            continue
        autorizadas.setdefault(id_peticion, set()).add(ruta_limpia)

    return autorizadas


def url_foto_esta_habilitada_para_peticion(id_peticion: int, url: str) -> bool:
    """Verifica en vivo en PostgreSQL si una URL sigue habilitada para miniatura ciudadana."""
    if not url or not isinstance(url, str):
        return False
    url_limpia = url.strip()
    if not url_limpia:
        return False
    urls_activas = obtener_urls_autorizadas([id_peticion]).get(id_peticion)
    return bool(urls_activas and url_limpia in urls_activas)


def _resolver_urls_objetivo_peticion(
    *,
    peticion: Peticiones,
    ruta_archivo: Optional[str],
    para_habilitar: bool = False,
) -> Tuple[List[str], bool]:
    """
    Resuelve las URLs reales de una petición sobre las que aplicará revocación o moderación.
    Retorna `(urls_objetivo, aplicar_marcador_global)`.
    """
    urls_reales = obtener_urls_reales_peticion(peticion.id_peticion)
    urls_aptas = _filtrar_urls_cloudinary_aptas(urls_reales)

    ruta_limpia = ruta_archivo.strip() if isinstance(ruta_archivo, str) else ''
    if not ruta_limpia or ruta_limpia == MARCADOR_BLOQUEO_GLOBAL_PETICION:
        if para_habilitar:
            if not urls_aptas:
                raise ValueError("La petición no tiene fotografías Cloudinary válidas para habilitar.")
            return urls_aptas, False
        return urls_reales, True

    if ruta_limpia not in urls_reales:
        raise ValueError("La ruta especificada no pertenece a las evidencias de esta petición.")

    if para_habilitar:
        aptas = _filtrar_urls_cloudinary_aptas([ruta_limpia])
        if not aptas:
            raise ValueError("La URL indicada no es una imagen Cloudinary segura apta para miniatura.")
        return aptas, False

    return [ruta_limpia], False


def revocar_fotos_peticion(
    *,
    peticion: Peticiones,
    usuario_actor,
    ruta_archivo: Optional[str] = None,
    motivo: Optional[str] = None,
) -> Dict[str, object]:
    """
    Permite al ciudadano responsable de la petición revocar de inmediato la visibilidad
    de una o todas sus fotografías en alertas preventivas (e invalida tokens previos).
    """
    if not es_peticionario(usuario_actor) or getattr(peticion, 'responsable_id', None) != getattr(usuario_actor, 'id_usuario', None):
        raise PermissionError("Solo el ciudadano creador activo puede revocar fotografías de esta petición.")

    urls_objetivo, global_flag = _resolver_urls_objetivo_peticion(
        peticion=peticion,
        ruta_archivo=ruta_archivo,
        para_habilitar=False,
    )
    ahora = timezone.now()

    with transaction.atomic():
        if global_flag:
            PeticionFotoAutorizada.objects.filter(
                id_peticion=peticion,
                revocada_en__isnull=True,
            ).update(
                revocada_en=ahora,
                version_texto=VERSION_TEXTO_REVOCADA_TITULAR,
            )
            PeticionFotoAutorizada.objects.update_or_create(
                id_peticion=peticion,
                ruta_archivo=MARCADOR_BLOQUEO_GLOBAL_PETICION,
                defaults={
                    'autorizado_por': usuario_actor,
                    'revocada_en': ahora,
                    'version_texto': VERSION_TEXTO_REVOCADA_TITULAR,
                },
            )
        else:
            for url_obj in urls_objetivo:
                if len(url_obj) > MAX_LARGO_RUTA_AUTORIZADA:
                    continue
                PeticionFotoAutorizada.objects.update_or_create(
                    id_peticion=peticion,
                    ruta_archivo=url_obj,
                    defaults={
                        'autorizado_por': usuario_actor,
                        'revocada_en': ahora,
                        'version_texto': VERSION_TEXTO_REVOCADA_TITULAR,
                    },
                )

        detalle = (motivo or '').strip()
        obs = "Revocación ciudadana de fotografía para alertas preventivas."
        if detalle:
            obs = f"{obs} Motivo: {detalle}"
        registrar_historial_peticion(
            peticion=peticion,
            tipo_cambio=HISTORIAL_TIPO_REVOCACION_FOTO,
            usuario_accion=usuario_actor,
            observacion=obs,
            raise_on_error=True,
        )

    return {
        'id_peticion': peticion.id_peticion,
        'accion': 'revocar',
        'bloqueo_global': global_flag,
        'urls_afectadas': len(urls_objetivo),
    }


def moderar_fotos_peticion(
    *,
    peticion: Peticiones,
    usuario_actor,
    accion: str,
    ruta_archivo: Optional[str] = None,
    motivo: Optional[str] = None,
) -> Dict[str, object]:
    """
    Permite a Jurídico/Administrador ocultar por moderación (`ocultar`) o habilitar tras
    revisión (`habilitar`) miniaturas de una petición respetando revocaciones del titular.
    """
    if not es_admin_o_juridico(usuario_actor):
        raise PermissionError("Solo Administrador o Jurídico pueden moderar fotografías.")

    accion_norm = (accion or '').strip().lower()
    if accion_norm not in ('ocultar', 'habilitar'):
        raise ValueError("Acción inválida; debe ser 'ocultar' o 'habilitar'.")

    if accion_norm == 'habilitar':
        info = _obtener_info_elegibilidad_peticiones([peticion.id_peticion]).get(peticion.id_peticion)
        if not info or not info.get('elegible'):
            raise RuntimeError(
                "La petición no está en estado elegible (p. ej. Duplicada, Rechazada o usuario inactivo)."
            )

    urls_objetivo, global_flag = _resolver_urls_objetivo_peticion(
        peticion=peticion,
        ruta_archivo=ruta_archivo,
        para_habilitar=(accion_norm == 'habilitar'),
    )
    ahora = timezone.now()

    with transaction.atomic():
        if accion_norm == 'ocultar':
            if global_flag:
                PeticionFotoAutorizada.objects.filter(
                    id_peticion=peticion,
                    revocada_en__isnull=True,
                ).update(
                    revocada_en=ahora,
                    version_texto=VERSION_TEXTO_MODERACION_OCULTA,
                )
                PeticionFotoAutorizada.objects.update_or_create(
                    id_peticion=peticion,
                    ruta_archivo=MARCADOR_BLOQUEO_GLOBAL_PETICION,
                    defaults={
                        'autorizado_por': usuario_actor,
                        'revocada_en': ahora,
                        'version_texto': VERSION_TEXTO_MODERACION_OCULTA,
                    },
                )
            else:
                for url_obj in urls_objetivo:
                    if len(url_obj) > MAX_LARGO_RUTA_AUTORIZADA:
                        continue
                    PeticionFotoAutorizada.objects.update_or_create(
                        id_peticion=peticion,
                        ruta_archivo=url_obj,
                        defaults={
                            'autorizado_por': usuario_actor,
                            'revocada_en': ahora,
                            'version_texto': VERSION_TEXTO_MODERACION_OCULTA,
                        },
                    )
        else:
            # Respeta estrictamente la revocación hecha por el ciudadano titular
            rev_titular_global = PeticionFotoAutorizada.objects.filter(
                id_peticion=peticion,
                ruta_archivo=MARCADOR_BLOQUEO_GLOBAL_PETICION,
                revocada_en__isnull=False,
                version_texto=VERSION_TEXTO_REVOCADA_TITULAR,
            ).exists()
            if rev_titular_global:
                raise RuntimeError(
                    "No es posible habilitar la fotografía porque fue revocada por el ciudadano titular."
                )

            revocadas_titular = set(
                PeticionFotoAutorizada.objects.filter(
                    id_peticion=peticion,
                    ruta_archivo__in=urls_objetivo,
                    revocada_en__isnull=False,
                    version_texto=VERSION_TEXTO_REVOCADA_TITULAR,
                ).values_list('ruta_archivo', flat=True)
            )
            if revocadas_titular:
                raise RuntimeError(
                    "No es posible habilitar fotografías revocadas expresamente por el ciudadano titular."
                )

            PeticionFotoAutorizada.objects.filter(
                id_peticion=peticion,
                ruta_archivo=MARCADOR_BLOQUEO_GLOBAL_PETICION,
                version_texto=VERSION_TEXTO_MODERACION_OCULTA,
            ).delete()

            for url_obj in urls_objetivo:
                PeticionFotoAutorizada.objects.update_or_create(
                    id_peticion=peticion,
                    ruta_archivo=url_obj,
                    defaults={
                        'autorizado_por': usuario_actor,
                        'fecha_autorizacion': ahora,
                        'revocada_en': None,
                        'version_texto': VERSION_TEXTO_MODERACION_APROBADA,
                    },
                )

        detalle = (motivo or '').strip()
        obs = f"Moderación de fotografía en alertas preventivas: acción '{accion_norm}'."
        if detalle:
            obs = f"{obs} Motivo: {detalle}"
        registrar_historial_peticion(
            peticion=peticion,
            tipo_cambio=HISTORIAL_TIPO_MODERACION_FOTO,
            usuario_accion=usuario_actor,
            observacion=obs,
            raise_on_error=True,
        )

    urls_habilitadas = sorted(obtener_urls_autorizadas([peticion.id_peticion]).get(peticion.id_peticion, ()))
    return {
        'id_peticion': peticion.id_peticion,
        'accion': accion_norm,
        'bloqueo_global': global_flag if accion_norm == 'ocultar' else False,
        'total_fotos_habilitadas': len(urls_habilitadas),
    }
