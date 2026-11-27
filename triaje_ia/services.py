from __future__ import annotations

import logging
import math
import re
import threading
from datetime import timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db import IntegrityError, connections, transaction
from django.db.models import Count, Q
from django.utils import timezone

from peticiones.models import Peticiones, PeticionPosibleDuplicado
from peticiones.services.alertas_duplicados_service import (
    registrar_alertas_desde_deteccion,
)
from peticiones.services.duplicados_service import (
    PeticionNoEncontradaError,
    detectar_duplicados_desde_solicitud,
    extraer_fotografias_peticion,
)
from peticiones.utils import detectar_tipo_archivo, normalizar_texto_juridico
from triaje_ia.models import TriajePeticion
from users.constants import ROL_VETERINARIO
from users.models import Usuarios

logger = logging.getLogger(__name__)

HEADER_TRIAJE_WEBHOOK_SECRET = 'X-Vetericano-Triaje-Secret'
HEADER_IDEMPOTENCY_KEY = 'X-Idempotency-Key'

_PATRONES_REDACCION = (
    (re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'), '[JWT-REDACTADO]'),
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+'), r'\1[REDACTADO]'),
    (
        re.compile(
            r'(?i)((?:password|passwd|secret|api[_-]?key|token|authorization)["\']?\s*[:=]\s*["\']?)'
            r'[^\s"\',;&}]+'
        ),
        r'\1[REDACTADO]',
    ),
)


def _ahora_local():
    """Retorna la fecha/hora actual compatible con settings.USE_TZ."""
    ahora = timezone.now()
    if not getattr(settings, 'USE_TZ', False) and timezone.is_aware(ahora):
        return timezone.make_naive(ahora, timezone.get_current_timezone())
    return ahora


def redactar_secretos(texto: Any, max_len: int = 500) -> str:
    """Elimina posibles tokens o secretos antes de persistir o registrar errores."""
    if texto is None:
        return ''
    limpio = str(texto).strip()
    for patron, reemplazo in _PATRONES_REDACCION:
        limpio = patron.sub(reemplazo, limpio)
    return limpio[:max_len]


def normalizar_prioridad_sugerida(valor: Any) -> Optional[str]:
    """
    Normaliza cualquier etiqueta de prioridad a los valores canónicos compatibles
    con Peticiones.prioridad, es_prioridad_urgente() y orden_prioridad():
    URGENTE, ALTA, MEDIA o BAJA.
    """
    if valor is None:
        return None
    norm = normalizar_texto_juridico(valor)
    if not norm:
        return None

    if any(k in norm for k in ('urgen', 'emergen', 'critic')):
        return TriajePeticion.PRIORIDAD_URGENTE
    if any(k in norm for k in ('alta', 'high')):
        return TriajePeticion.PRIORIDAD_ALTA
    if any(k in norm for k in ('media', 'medio', 'normal', 'moderad', 'medium')):
        return TriajePeticion.PRIORIDAD_MEDIA
    if any(k in norm for k in ('baja', 'bajo', 'low', 'leve')):
        return TriajePeticion.PRIORIDAD_BAJA
    return None


def obtener_config_triaje() -> Dict[str, Any]:
    """Lee la configuración vigente del módulo triaje_ia desde Django settings."""
    try:
        timeout = float(getattr(settings, 'TRIAJE_N8N_TIMEOUT_SEGUNDOS', 5.0))
    except (TypeError, ValueError):
        timeout = 5.0
    if not math.isfinite(timeout):
        timeout = 5.0
    timeout = max(0.5, min(30.0, timeout))

    try:
        max_reintentos = int(getattr(settings, 'TRIAJE_N8N_MAX_REINTENTOS', 3))
    except (TypeError, ValueError):
        max_reintentos = 3
    max_reintentos = max(1, min(10, max_reintentos))

    try:
        backoff_base = float(getattr(settings, 'TRIAJE_N8N_BACKOFF_BASE_SEGUNDOS', 60.0))
    except (TypeError, ValueError):
        backoff_base = 60.0
    if not math.isfinite(backoff_base):
        backoff_base = 60.0
    backoff_base = max(5.0, min(3600.0, backoff_base))

    return {
        'activo': bool(getattr(settings, 'TRIAJE_N8N_ACTIVO', False)),
        'webhook_url': str(getattr(settings, 'TRIAJE_N8N_WEBHOOK_URL', '') or '').strip(),
        'webhook_secret': str(getattr(settings, 'TRIAJE_N8N_WEBHOOK_SECRET', '') or '').strip(),
        'timeout': timeout,
        'max_reintentos': max_reintentos,
        'backoff_base': backoff_base,
        'async_thread': bool(getattr(settings, 'TRIAJE_N8N_ASYNC_THREAD', True)),
    }


def _validar_webhook_url(url_raw: str) -> str:
    if not url_raw:
        raise ValueError('TRIAJE_N8N_WEBHOOK_URL no está configurada.')
    parsed = urlparse(url_raw)
    scheme = (parsed.scheme or '').lower()
    if scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('TRIAJE_N8N_WEBHOOK_URL debe ser una URL HTTP/HTTPS válida.')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('TRIAJE_N8N_WEBHOOK_URL no debe incluir credenciales embebidas.')
    return url_raw


def construir_payload_peticion_triaje(
    peticion: Peticiones,
    *,
    clave_idempotencia: Optional[str] = None,
) -> Dict[str, Any]:
    """Construye el payload estándar de denuncia ciudadana para n8n sin secretos."""
    _, todas_evidencias = extraer_fotografias_peticion(peticion)
    fotos_imagenes = [u for u in todas_evidencias if detectar_tipo_archivo(u) == 'imagen']
    videos = [u for u in todas_evidencias if detectar_tipo_archivo(u) == 'video']

    ubicacion = getattr(peticion, 'id_ubicacion', None)
    lat = getattr(ubicacion, 'latitud', None) if ubicacion is not None else None
    lon = getattr(ubicacion, 'longitud', None) if ubicacion is not None else None

    fecha_val = getattr(peticion, 'fecha', None)
    tipo_obj = getattr(peticion, 'id_tipo', None)
    estado_obj = getattr(peticion, 'id_estado', None)

    return {
        'id_peticion': peticion.id_peticion,
        'numero_radicado': peticion.numero_radicado,
        'clave_idempotencia': clave_idempotencia or f'triaje-pet-{peticion.id_peticion}',
        'id_tipo': getattr(peticion, 'id_tipo_id', None),
        'tipo_nombre': getattr(tipo_obj, 'nombre', None) if tipo_obj else None,
        'estado_actual': getattr(estado_obj, 'nombre', None) if estado_obj else None,
        'prioridad_actual': peticion.prioridad,
        'descripcion': peticion.descripcion or '',
        'fecha': fecha_val.isoformat() if hasattr(fecha_val, 'isoformat') else fecha_val,
        'ubicacion': {
            'direccion': getattr(ubicacion, 'direccion', None) if ubicacion else None,
            'latitud': float(lat) if lat is not None else None,
            'longitud': float(lon) if lon is not None else None,
        },
        'foto': fotos_imagenes[0] if fotos_imagenes else None,
        'fotos': fotos_imagenes,
        'total_fotos': len(fotos_imagenes),
        'videos': videos,
    }


def calcular_carga_veterinarios() -> Dict[str, Any]:
    """
    Reutiliza los mismos criterios de carga laboral de DashboardJuridicoView sobre
    veterinarios activos (rol 3) y retorna el ranking ordenado por menor carga activa.
    """
    filtro_no_finalizada = (
        ~Q(peticiones_asignadas__id_estado__nombre__icontains='atendid')
        & ~Q(peticiones_asignadas__id_estado__nombre__icontains='finaliz')
        & ~Q(peticiones_asignadas__id_estado__nombre__icontains='alta')
        & ~Q(peticiones_asignadas__id_estado__nombre__icontains='cerrad')
        & ~Q(peticiones_asignadas__id_estado__nombre__icontains='resuelt')
        & ~Q(peticiones_asignadas__id_estado__nombre__icontains='duplicad')
    )
    filtro_urgente = (
        Q(peticiones_asignadas__prioridad__iexact='alta')
        | Q(peticiones_asignadas__prioridad__iexact='urgente')
        | Q(peticiones_asignadas__prioridad__icontains='urg')
        | Q(peticiones_asignadas__prioridad__icontains='critic')
        | Q(peticiones_asignadas__prioridad__icontains='emerg')
        | Q(peticiones_asignadas__prioridad__icontains='alt')
    )

    vets_qs = (
        Usuarios.objects.filter(id_rol_id=ROL_VETERINARIO, activo=True)
        .select_related('id_sede')
        .annotate(
            total_asig=Count('peticiones_asignadas', distinct=True),
            pendientes_asig=Count(
                'peticiones_asignadas',
                filter=(
                    Q(peticiones_asignadas__id_estado__nombre__icontains='pendiente')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='evalua')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='asignad')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='revis')
                ),
                distinct=True,
            ),
            en_proceso_asig=Count(
                'peticiones_asignadas',
                filter=(
                    Q(peticiones_asignadas__id_estado__nombre__icontains='proceso')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='atenci')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='curso')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='tratam')
                ),
                distinct=True,
            ),
            finalizadas_asig=Count(
                'peticiones_asignadas',
                filter=(
                    Q(peticiones_asignadas__id_estado__nombre__icontains='atendid')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='finaliz')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='alta')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='cerrad')
                    | Q(peticiones_asignadas__id_estado__nombre__icontains='resuelt')
                ),
                distinct=True,
            ),
            urgentes_activos=Count(
                'peticiones_asignadas',
                filter=filtro_no_finalizada & filtro_urgente,
                distinct=True,
            ),
        )
    )

    veterinarios: List[Dict[str, Any]] = []
    for v in vets_qs:
        total_asig = int(v.total_asig or 0)
        pendientes = int(v.pendientes_asig or 0)
        en_proceso = int(v.en_proceso_asig or 0)
        finalizadas = int(v.finalizadas_asig or 0)
        urgentes_act = int(v.urgentes_activos or 0)
        carga_activa = pendientes + en_proceso
        pct = round((finalizadas / total_asig) * 100, 1) if total_asig > 0 else 0.0
        if isinstance(pct, float) and pct.is_integer():
            pct = int(pct)

        veterinarios.append({
            'id_veterinario': v.id_usuario,
            'nombre_completo': f'{v.nombre or ""} {v.apellido or ""}'.strip(),
            'id_sede': v.id_sede_id,
            'sede_nombre': getattr(v.id_sede, 'nombre', None) if v.id_sede_id else None,
            'total_asignados': total_asig,
            'pendientes': pendientes,
            'en_proceso': en_proceso,
            'finalizadas': finalizadas,
            'carga_activa': carga_activa,
            'urgentes_activos': urgentes_act,
            'porcentaje_completado': pct,
        })

    veterinarios.sort(
        key=lambda item: (
            item['carga_activa'],
            item['urgentes_activos'],
            item['total_asignados'],
            item['id_veterinario'],
        )
    )

    recomendado = veterinarios[0] if veterinarios else None
    justificacion = (
        f"Recomendado {recomendado['nombre_completo']} (ID #{recomendado['id_veterinario']}) "
        f"con menor carga activa ({recomendado['carga_activa']} caso(s): "
        f"{recomendado['pendientes']} pendiente(s), {recomendado['en_proceso']} en proceso, "
        f"{recomendado['urgentes_activos']} urgente(s) activo(s))."
        if recomendado
        else 'No hay veterinarios activos registrados para recomendar.'
    )

    return {
        'total_veterinarios_activos': len(veterinarios),
        'veterinario_recomendado': recomendado,
        'justificacion_recomendacion': justificacion,
        'veterinarios': veterinarios,
    }


def ejecutar_deteccion_duplicados_triaje(
    id_peticion: int,
    *,
    usar_dinov2: Optional[bool] = True,
    registrar_alertas: bool = True,
) -> Dict[str, Any]:
    """
    Reutiliza el detector oficial de duplicados (texto pg_trgm, GPS Haversine y DINOv2/dHash)
    y registra idempotentemente las alertas en PeticionPosibleDuplicado.
    """
    peticion = Peticiones.objects.filter(pk=id_peticion).first()
    if not peticion:
        raise PeticionNoEncontradaError(f'La petición #{id_peticion} no existe.')

    deteccion = detectar_duplicados_desde_solicitud(
        id_peticion=id_peticion,
        usar_dinov2=usar_dinov2,
        incluir_imagenes=True,
        incluir_evidencias=True,
    )

    candidatos = deteccion.get('posibles_duplicados') or []
    resumen_alertas = None
    if registrar_alertas and candidatos:
        try:
            batch = registrar_alertas_desde_deteccion(
                peticion_origen=id_peticion,
                candidatos=deteccion,
                solo_posibles_duplicados=True,
                origen_deteccion=PeticionPosibleDuplicado.ORIGEN_REANALISIS,
                notificar=True,
                registrar_historial=True,
                actualizar_si_pendiente=True,
                ignorar_candidatos_inexistentes=True,
            )
            resumen_alertas = {
                'creadas': batch.creadas,
                'actualizadas': batch.actualizadas,
                'respetadas_decision_previa': batch.respetadas_decision_previa,
            }
        except Exception as exc:
            logger.warning(
                'No se pudieron persistir alertas de duplicados en triaje para petición #%s: %s',
                id_peticion,
                redactar_secretos(exc),
            )

    candidatos_resumidos = [
        {
            'id_peticion': c.get('id_peticion'),
            'numero_radicado': c.get('numero_radicado'),
            'score_combinado': c.get('score_combinado'),
            'similitud_texto': c.get('similitud_texto'),
            'distancia_metros': c.get('distancia_metros'),
            'porcentaje_similitud_visual': c.get('porcentaje_similitud_visual'),
            'metodo_imagen': c.get('metodo_imagen'),
            'criterios_coincidencia': c.get('criterios_coincidencia') or [],
            'estado': c.get('estado'),
        }
        for c in candidatos
    ]

    return {
        'id_peticion': id_peticion,
        'posible_duplicado': bool(deteccion.get('posible_duplicado')),
        'total_candidatos': len(candidatos_resumidos),
        'posibles_duplicados': candidatos_resumidos,
        'parametros': deteccion.get('parametros') or {},
        'alertas_registradas': resumen_alertas,
    }


def _registrar_fallo_envio(
    triaje: TriajePeticion,
    *,
    error_msg: str,
    codigo_http: Optional[int] = None,
    backoff_base: float = 60.0,
) -> TriajePeticion:
    ahora = _ahora_local()
    triaje.intentos_envio = int(triaje.intentos_envio or 0) + 1
    triaje.ultimo_codigo_http = codigo_http
    triaje.ultimo_error_envio = redactar_secretos(error_msg)

    if triaje.intentos_envio >= int(triaje.max_intentos or 3):
        triaje.estado_envio = TriajePeticion.ENVIO_FALLO_DEFINITIVO
        triaje.proximo_reintento_en = None
    else:
        triaje.estado_envio = TriajePeticion.ENVIO_FALLO_TEMPORAL
        espera_seg = backoff_base * (2 ** max(0, triaje.intentos_envio - 1))
        triaje.proximo_reintento_en = ahora + timedelta(seconds=espera_seg)

    triaje.save(
        update_fields=[
            'intentos_envio',
            'ultimo_codigo_http',
            'ultimo_error_envio',
            'estado_envio',
            'proximo_reintento_en',
            'fecha_actualizacion',
        ]
    )
    return triaje


def despachar_triaje_por_id(
    id_triaje: int,
    *,
    forzar_reenvio: bool = False,
) -> Dict[str, Any]:
    """
    Envía el payload de triaje a n8n de forma segura e idempotente.
    Nunca propaga excepciones de red ni credenciales.
    """
    cfg = obtener_config_triaje()
    if not cfg['activo'] and not forzar_reenvio:
        return {'enviado': False, 'id_triaje': id_triaje, 'motivo': 'DESACTIVADO'}

    with transaction.atomic():
        triaje = (
            TriajePeticion.objects.select_for_update()
            .select_related('peticion', 'peticion__id_tipo', 'peticion__id_estado', 'peticion__id_ubicacion')
            .filter(pk=id_triaje)
            .first()
        )
        if not triaje:
            return {'enviado': False, 'id_triaje': id_triaje, 'motivo': 'NO_ENCONTRADO'}

        if not forzar_reenvio and (
            triaje.estado_envio == TriajePeticion.ENVIO_ENVIADO
            or triaje.estado_analisis == TriajePeticion.ANALISIS_COMPLETADO
        ):
            return {
                'enviado': False,
                'idempotente': True,
                'id_triaje': triaje.id_triaje,
                'estado_envio': triaje.estado_envio,
            }

        try:
            webhook_url = _validar_webhook_url(cfg['webhook_url'])
        except ValueError as exc:
            _registrar_fallo_envio(triaje, error_msg=str(exc), backoff_base=cfg['backoff_base'])
            return {'enviado': False, 'id_triaje': triaje.id_triaje, 'estado_envio': triaje.estado_envio}

        payload = construir_payload_peticion_triaje(
            triaje.peticion,
            clave_idempotencia=triaje.clave_idempotencia,
        )
        clave_idem = triaje.clave_idempotencia

    headers = {
        'Content-Type': 'application/json',
        HEADER_IDEMPOTENCY_KEY: clave_idem,
    }
    if cfg['webhook_secret']:
        headers[HEADER_TRIAJE_WEBHOOK_SECRET] = cfg['webhook_secret']

    resp = None
    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers=headers,
            timeout=cfg['timeout'],
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        logger.warning(
            'Error de red enviando triaje #%s (petición #%s) a n8n: %s',
            id_triaje,
            payload['id_peticion'],
            redactar_secretos(exc),
        )
        with transaction.atomic():
            triaje_upd = TriajePeticion.objects.select_for_update().filter(pk=id_triaje).first()
            if triaje_upd:
                _registrar_fallo_envio(
                    triaje_upd,
                    error_msg=f'Error de conexión con n8n: {exc}',
                    backoff_base=cfg['backoff_base'],
                )
                return {
                    'enviado': False,
                    'id_triaje': id_triaje,
                    'estado_envio': triaje_upd.estado_envio,
                }
        return {'enviado': False, 'id_triaje': id_triaje, 'estado_envio': TriajePeticion.ENVIO_FALLO_TEMPORAL}

    try:
        status_code = int(resp.status_code)
        body_preview = redactar_secretos(getattr(resp, 'text', '') or '', max_len=250)
    finally:
        try:
            resp.close()
        except Exception:
            pass

    with transaction.atomic():
        triaje_upd = TriajePeticion.objects.select_for_update().filter(pk=id_triaje).first()
        if not triaje_upd:
            return {'enviado': False, 'id_triaje': id_triaje, 'motivo': 'NO_ENCONTRADO'}

        if 200 <= status_code < 300:
            ahora = _ahora_local()
            triaje_upd.intentos_envio = int(triaje_upd.intentos_envio or 0) + 1
            triaje_upd.estado_envio = TriajePeticion.ENVIO_ENVIADO
            if triaje_upd.estado_analisis == TriajePeticion.ANALISIS_PENDIENTE:
                triaje_upd.estado_analisis = TriajePeticion.ANALISIS_EN_PROCESO
            triaje_upd.ultimo_codigo_http = status_code
            triaje_upd.ultimo_error_envio = ''
            triaje_upd.proximo_reintento_en = None
            triaje_upd.enviado_en = ahora
            triaje_upd.save(
                update_fields=[
                    'intentos_envio',
                    'estado_envio',
                    'estado_analisis',
                    'ultimo_codigo_http',
                    'ultimo_error_envio',
                    'proximo_reintento_en',
                    'enviado_en',
                    'fecha_actualizacion',
                ]
            )
            return {
                'enviado': True,
                'id_triaje': triaje_upd.id_triaje,
                'estado_envio': triaje_upd.estado_envio,
                'status_code': status_code,
            }

        _registrar_fallo_envio(
            triaje_upd,
            error_msg=f'n8n respondió HTTP {status_code}: {body_preview}',
            codigo_http=status_code,
            backoff_base=cfg['backoff_base'],
        )
        return {
            'enviado': False,
            'id_triaje': triaje_upd.id_triaje,
            'estado_envio': triaje_upd.estado_envio,
            'status_code': status_code,
        }


def _despachar_en_segundo_plano(id_triaje: int, *, forzar_reenvio: bool = False) -> None:
    try:
        despachar_triaje_por_id(id_triaje, forzar_reenvio=forzar_reenvio)
    except Exception:
        logger.exception('Fallo inesperado en hilo asíncrono de triaje #%s.', id_triaje)
    finally:
        try:
            connections.close_all()
        except Exception:
            pass


def encolar_y_despachar_triaje_peticion(
    peticion: Peticiones | int,
    *,
    forzar_reenvio: bool = False,
    async_dispatch: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Crea/recupera el registro persistente de triaje de forma idempotente y
    programa su envío no bloqueante hacia n8n cuando TRIAJE_N8N_ACTIVO=True.
    """
    cfg = obtener_config_triaje()
    if not cfg['activo'] and not forzar_reenvio:
        return {'ejecutado': False, 'motivo': 'DESACTIVADO'}

    if isinstance(peticion, Peticiones):
        peticion_obj = peticion
        id_peticion = peticion.id_peticion
    else:
        id_peticion = int(peticion)
        peticion_obj = Peticiones.objects.filter(pk=id_peticion).first()

    if not peticion_obj or not id_peticion:
        return {'ejecutado': False, 'motivo': 'PETICION_NO_ENCONTRADA'}

    clave_idem = f'triaje-pet-{id_peticion}'
    try:
        with transaction.atomic():
            triaje, creada = TriajePeticion.objects.get_or_create(
                peticion_id=id_peticion,
                defaults={
                    'clave_idempotencia': clave_idem,
                    'prioridad_original': peticion_obj.prioridad,
                    'max_intentos': cfg['max_reintentos'],
                },
            )
    except IntegrityError:
        triaje = TriajePeticion.objects.filter(peticion_id=id_peticion).first()
        creada = False

    if not triaje:
        return {'ejecutado': False, 'motivo': 'ERROR_PERSISTENCIA'}

    if not forzar_reenvio and (
        triaje.estado_envio == TriajePeticion.ENVIO_ENVIADO
        or triaje.estado_analisis == TriajePeticion.ANALISIS_COMPLETADO
    ):
        return {
            'ejecutado': False,
            'idempotente': True,
            'creada': creada,
            'id_triaje': triaje.id_triaje,
            'estado_envio': triaje.estado_envio,
        }

    if forzar_reenvio and triaje.estado_envio == TriajePeticion.ENVIO_FALLO_DEFINITIVO:
        triaje.estado_envio = TriajePeticion.ENVIO_PENDIENTE
        triaje.intentos_envio = 0
        triaje.proximo_reintento_en = None
        triaje.save(update_fields=['estado_envio', 'intentos_envio', 'proximo_reintento_en', 'fecha_actualizacion'])

    usar_hilo = cfg['async_thread'] if async_dispatch is None else bool(async_dispatch)
    if usar_hilo:
        hilo = threading.Thread(
            target=_despachar_en_segundo_plano,
            args=(triaje.id_triaje,),
            kwargs={'forzar_reenvio': forzar_reenvio},
            name=f'triaje-n8n-{id_peticion}',
            daemon=True,
        )
        hilo.start()
        return {
            'ejecutado': True,
            'asincrono': True,
            'creada': creada,
            'id_triaje': triaje.id_triaje,
            'clave_idempotencia': triaje.clave_idempotencia,
        }

    resultado = despachar_triaje_por_id(triaje.id_triaje, forzar_reenvio=forzar_reenvio)
    return {
        'ejecutado': True,
        'asincrono': False,
        'creada': creada,
        'id_triaje': triaje.id_triaje,
        'clave_idempotencia': triaje.clave_idempotencia,
        **resultado,
    }


def procesar_reintentos_pendientes(*, limite: int = 20) -> Dict[str, Any]:
    """Reintenta el envío a n8n de los triajes en PENDIENTE o FALLO_TEMPORAL vencidos."""
    cfg = obtener_config_triaje()
    if not cfg['activo']:
        return {'activo': False, 'procesados': 0, 'enviados': 0, 'fallidos': 0}

    ahora = _ahora_local()
    pendientes = list(
        TriajePeticion.objects.filter(
            estado_envio__in=[
                TriajePeticion.ENVIO_PENDIENTE,
                TriajePeticion.ENVIO_FALLO_TEMPORAL,
            ]
        )
        .filter(Q(proximo_reintento_en__isnull=True) | Q(proximo_reintento_en__lte=ahora))
        .order_by('proximo_reintento_en', 'id_triaje')[: max(1, min(100, int(limite)))]
    )

    enviados = 0
    fallidos = 0
    detalles = []
    for item in pendientes:
        if int(item.intentos_envio or 0) >= int(item.max_intentos or 3):
            item.estado_envio = TriajePeticion.ENVIO_FALLO_DEFINITIVO
            item.proximo_reintento_en = None
            item.save(update_fields=['estado_envio', 'proximo_reintento_en', 'fecha_actualizacion'])
            fallidos += 1
            continue
        res = despachar_triaje_por_id(item.id_triaje)
        detalles.append(res)
        if res.get('enviado'):
            enviados += 1
        else:
            fallidos += 1

    return {
        'activo': True,
        'procesados': len(pendientes),
        'enviados': enviados,
        'fallidos': fallidos,
        'detalles': detalles,
    }


def registrar_resultado_triaje(id_peticion: int, datos: Dict[str, Any]) -> TriajePeticion:
    """
    Persiste de forma idempotente el resultado del análisis recibido desde n8n.
    Nunca modifica Peticiones.prioridad ni asigna Peticiones.asignado_a.
    """
    peticion = Peticiones.objects.filter(pk=id_peticion).first()
    if not peticion:
        raise PeticionNoEncontradaError(f'La petición #{id_peticion} no existe.')

    prioridad_raw = datos.get('prioridad_sugerida')
    prioridad_norm = normalizar_prioridad_sugerida(prioridad_raw)

    vet_rec_id = datos.get('veterinario_recomendado_id')
    veterinario_obj = None
    aviso_vet = ''
    if vet_rec_id is not None:
        veterinario_obj = Usuarios.objects.filter(
            id_usuario=vet_rec_id,
            id_rol_id=ROL_VETERINARIO,
            activo=True,
        ).first()
        if not veterinario_obj:
            aviso_vet = f' [Veterinario recomendado #{vet_rec_id} no válido o inactivo]'

    estado_analisis = datos.get('estado_analisis') or TriajePeticion.ANALISIS_COMPLETADO
    estados_validos = {c[0] for c in TriajePeticion.ESTADOS_ANALISIS_CHOICES}
    if estado_analisis not in estados_validos:
        estado_analisis = TriajePeticion.ANALISIS_COMPLETADO

    posibles_dup = datos.get('posibles_duplicados')
    if not isinstance(posibles_dup, list):
        posibles_dup = []

    signos = datos.get('signos_detectados')
    if not isinstance(signos, list):
        signos = []

    carga_snap = datos.get('carga_veterinarios_snapshot')
    if not isinstance(carga_snap, list):
        carga_snap = []

    resultado_raw = datos.get('resultado_raw')
    if not isinstance(resultado_raw, dict):
        resultado_raw = {}

    confianza = datos.get('nivel_confianza')
    if confianza is not None:
        try:
            confianza = max(0.0, min(1.0, round(float(confianza), 4)))
        except (TypeError, ValueError):
            confianza = None

    justificacion_rec = str(datos.get('justificacion_recomendacion') or '').strip()
    if aviso_vet:
        justificacion_rec = f'{justificacion_rec}{aviso_vet}'.strip()

    ahora = _ahora_local()
    clave_idem = str(datos.get('clave_idempotencia') or '').strip() or f'triaje-pet-{id_peticion}'

    with transaction.atomic():
        triaje, _ = TriajePeticion.objects.select_for_update().get_or_create(
            peticion_id=id_peticion,
            defaults={
                'clave_idempotencia': clave_idem,
                'prioridad_original': peticion.prioridad,
                'estado_envio': TriajePeticion.ENVIO_ENVIADO,
            },
        )
        triaje.estado_analisis = estado_analisis
        if triaje.estado_envio == TriajePeticion.ENVIO_PENDIENTE:
            triaje.estado_envio = TriajePeticion.ENVIO_ENVIADO
        if prioridad_norm is not None:
            triaje.prioridad_sugerida = prioridad_norm
        if confianza is not None:
            triaje.nivel_confianza = confianza
        triaje.justificacion = str(datos.get('justificacion') or '').strip()
        triaje.resumen_texto = str(datos.get('resumen_texto') or '').strip()
        triaje.resumen_vision = str(datos.get('resumen_vision') or '').strip()
        triaje.signos_detectados = signos[:30]
        triaje.posibles_duplicados = posibles_dup[:20]
        triaje.total_duplicados_detectados = len(posibles_dup)
        triaje.veterinario_recomendado = veterinario_obj
        triaje.justificacion_recomendacion = justificacion_rec
        triaje.carga_veterinarios_snapshot = carga_snap[:50]
        triaje.modelo_texto_usado = str(datos.get('modelo_texto_usado') or '')[:100]
        triaje.modelo_vision_usado = str(datos.get('modelo_vision_usado') or '')[:100]
        triaje.n8n_execution_id = str(datos.get('n8n_execution_id') or '')[:120]
        triaje.resultado_raw = resultado_raw
        if estado_analisis in (TriajePeticion.ANALISIS_COMPLETADO, TriajePeticion.ANALISIS_PARCIAL):
            triaje.completado_en = ahora
        triaje.save()

    return triaje
