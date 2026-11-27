from __future__ import annotations

import logging
import math
import re
import threading
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from peticiones.models import Peticiones, PeticionPosibleDuplicado
from peticiones.services.alertas_duplicados_service import (
    es_peticion_duplicada,
    registrar_alertas_desde_deteccion,
)
from peticiones.services.duplicados_service import (
    PeticionNoEncontradaError,
    detectar_duplicados_desde_solicitud,
    extraer_fotografias_peticion,
)
from peticiones.utils import (
    DIAS_PARA_REASIGNAR,
    ESTADOS_TERMINALES_NORM,
    calcular_tiempos_peticion,
    detectar_tipo_archivo,
    normalizar_texto_juridico,
    registrar_historial_peticion,
)
from realtime import notificar as rt_notificar
from triaje_ia.models import TriajePeticion
from users.constants import ROL_VETERINARIO
from users.models import Usuarios

logger = logging.getLogger(__name__)

HEADER_TRIAJE_WEBHOOK_SECRET = 'X-Vetericano-Triaje-Secret'
HEADER_IDEMPOTENCY_KEY = 'X-Idempotency-Key'
HEADER_DISPATCH_TOKEN = 'X-Triaje-Dispatch-Token'
_PG_ADVISORY_LOCK_KEY = 72491001

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


class StaleTriajeCallbackError(ValueError):
    """Se lanza cuando llega un callback obsoleto o que intenta degradar un análisis ya completado."""


class TriajeNoEncontradoError(LookupError):
    """Se lanza cuando la petición o su registro de triaje no existen."""


class TriajeDecisionInvalidaError(ValueError):
    """Se lanza cuando la decisión humana sobre el triaje no puede aplicarse."""

    def __init__(self, mensaje: str, *, status_code: int = 400, codigo: str = 'DECISION_INVALIDA'):
        super().__init__(mensaje)
        self.status_code = status_code
        self.codigo = codigo


def _ahora_local():
    """Retorna la fecha/hora actual compatible con settings.USE_TZ."""
    ahora = timezone.now()
    if not getattr(settings, 'USE_TZ', False) and timezone.is_aware(ahora):
        return timezone.make_naive(ahora, timezone.get_current_timezone())
    return ahora


def _select_for_update_qs(qs):
    if getattr(connection.features, 'has_select_for_update_skip_locked', False):
        return qs.select_for_update(skip_locked=True)
    return qs.select_for_update()


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


def _leer_float_setting(nombre: str, default: float, minimo: float, maximo: float) -> float:
    try:
        val = float(getattr(settings, nombre, default))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(val):
        return default
    return max(minimo, min(maximo, val))


def _leer_int_setting(nombre: str, default: int, minimo: int, maximo: int) -> int:
    try:
        val = int(getattr(settings, nombre, default))
    except (TypeError, ValueError):
        return default
    return max(minimo, min(maximo, val))


def obtener_config_triaje() -> Dict[str, Any]:
    """Lee la configuración vigente del módulo triaje_ia desde Django settings."""
    return {
        'activo': bool(getattr(settings, 'TRIAJE_N8N_ACTIVO', False)),
        'webhook_url': str(getattr(settings, 'TRIAJE_N8N_WEBHOOK_URL', '') or '').strip(),
        'webhook_secret': str(getattr(settings, 'TRIAJE_N8N_WEBHOOK_SECRET', '') or '').strip(),
        'timeout': _leer_float_setting('TRIAJE_N8N_TIMEOUT_SEGUNDOS', 5.0, 0.5, 30.0),
        'max_reintentos': _leer_int_setting('TRIAJE_N8N_MAX_REINTENTOS', 3, 1, 10),
        'backoff_base': _leer_float_setting('TRIAJE_N8N_BACKOFF_BASE_SEGUNDOS', 60.0, 5.0, 3600.0),
        'lease_timeout': _leer_float_setting('TRIAJE_LEASE_TIMEOUT_SEGUNDOS', 60.0, 10.0, 600.0),
        'analisis_timeout': _leer_float_setting('TRIAJE_ANALISIS_TIMEOUT_SEGUNDOS', 300.0, 30.0, 3600.0),
        'max_casos_vet': _leer_int_setting('TRIAJE_MAX_CASOS_ACTIVOS_VETERINARIO', 0, 0, 1000),
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
    despacho_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Construye el payload estándar de denuncia ciudadana para n8n sin secretos ni datos de contacto del ciudadano."""
    _, todas_evidencias = extraer_fotografias_peticion(peticion)
    fotos_imagenes = [u for u in todas_evidencias if detectar_tipo_archivo(u) == 'imagen']
    videos = [u for u in todas_evidencias if detectar_tipo_archivo(u) == 'video']

    ubicacion = getattr(peticion, 'id_ubicacion', None)
    lat = getattr(ubicacion, 'latitud', None) if ubicacion is not None else None
    lon = getattr(ubicacion, 'longitud', None) if ubicacion is not None else None

    fecha_val = getattr(peticion, 'fecha', None)
    tipo_obj = getattr(peticion, 'id_tipo', None)
    estado_obj = getattr(peticion, 'id_estado', None)
    responsable = getattr(peticion, 'responsable', None)

    return {
        'id_peticion': peticion.id_peticion,
        'numero_radicado': peticion.numero_radicado,
        'clave_idempotencia': clave_idempotencia or f'triaje-pet-{peticion.id_peticion}',
        'despacho_token': despacho_token or '',
        'id_tipo': getattr(peticion, 'id_tipo_id', None),
        'tipo_nombre': getattr(tipo_obj, 'nombre', None) if tipo_obj else None,
        'id_sede': getattr(responsable, 'id_sede_id', None) if responsable else None,
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


def _evaluar_elegibilidad_asignacion(peticion: Peticiones) -> Tuple[bool, str]:
    """
    Replica exactamente las reglas de AsignarPeticionView:
    - Bloquea peticiones marcadas como duplicadas.
    - Bloquea peticiones en estado terminal.
    - Bloquea reasignaciones si no cumplen DIAS_PARA_REASIGNAR (5 días sin avance).
    """
    estado_obj = getattr(peticion, 'id_estado', None)
    estado_norm = normalizar_texto_juridico(getattr(estado_obj, 'nombre', '') if estado_obj else '')

    if estado_norm in ('duplicada', 'duplicado') or es_peticion_duplicada(peticion):
        return (False, 'La petición está marcada como duplicada; no admite asignación.')

    if estado_norm in ESTADOS_TERMINALES_NORM:
        return (
            False,
            f"La petición se encuentra en estado terminal '{getattr(estado_obj, 'nombre', '')}'; no requiere asignación.",
        )

    if getattr(peticion, 'asignado_a_id', None) is not None:
        tiempos = calcular_tiempos_peticion(peticion)
        if not tiempos['puede_reasignar']:
            return (
                False,
                f"La petición ya tiene veterinario asignado (#{peticion.asignado_a_id}) y lleva "
                f"{tiempos['dias_sin_avanzar']} día(s) sin avance (mínimo {DIAS_PARA_REASIGNAR} para reasignar).",
            )

    return (True, '')


def calcular_carga_veterinarios(
    *,
    id_peticion: Optional[int] = None,
    id_sede: Optional[int] = None,
    solo_sede: bool = False,
) -> Dict[str, Any]:
    """
    Reutiliza los criterios de carga laboral de DashboardJuridicoView sobre veterinarios
    activos con sede activa (o sin sede), respetando los límites de asignación vigentes.
    """
    cfg = obtener_config_triaje()
    max_casos = cfg['max_casos_vet']

    asignacion_permitida = True
    motivo_no_asignable = ''
    sede_preferida = id_sede

    if id_peticion is not None:
        peticion = (
            Peticiones.objects.select_related('id_estado', 'asignado_a', 'responsable')
            .filter(pk=id_peticion)
            .first()
        )
        if not peticion:
            raise PeticionNoEncontradaError(f'La petición #{id_peticion} no existe.')
        asignacion_permitida, motivo_no_asignable = _evaluar_elegibilidad_asignacion(peticion)
        if sede_preferida is None and getattr(peticion, 'responsable', None):
            sede_preferida = getattr(peticion.responsable, 'id_sede_id', None)

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
        .filter(Q(id_sede__isnull=True) | Q(id_sede__activo=True))
        .select_related('id_sede')
    )
    if solo_sede and sede_preferida is not None:
        vets_qs = vets_qs.filter(id_sede_id=sede_preferida)

    vets_qs = vets_qs.annotate(
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

    veterinarios: List[Dict[str, Any]] = []
    for v in vets_qs:
        total_asig = int(v.total_asig or 0)
        pendientes = int(v.pendientes_asig or 0)
        en_proceso = int(v.en_proceso_asig or 0)
        finalizadas = int(v.finalizadas_asig or 0)
        urgentes_act = int(v.urgentes_activos or 0)
        carga_activa = pendientes + en_proceso
        saturado = bool(max_casos > 0 and carga_activa >= max_casos)
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
            'saturado': saturado,
            'porcentaje_completado': pct,
        })

    veterinarios.sort(
        key=lambda item: (
            1 if item['saturado'] else 0,
            0 if (sede_preferida is not None and item['id_sede'] == sede_preferida) else 1,
            item['carga_activa'],
            item['urgentes_activos'],
            item['total_asignados'],
            item['id_veterinario'],
        )
    )

    elegibles = [v for v in veterinarios if not v['saturado']]
    if not asignacion_permitida:
        recomendado = None
        justificacion = motivo_no_asignable
    elif not veterinarios:
        recomendado = None
        justificacion = 'No hay veterinarios activos en sedes activas disponibles para recomendar.'
    elif not elegibles:
        recomendado = None
        justificacion = (
            f'Todos los veterinarios activos ({len(veterinarios)}) alcanzaron el límite '
            f'de carga activa configurado ({max_casos}).'
        )
    else:
        recomendado = elegibles[0]
        justificacion = (
            f"Recomendado {recomendado['nombre_completo']} (ID #{recomendado['id_veterinario']}) "
            f"con menor carga activa ({recomendado['carga_activa']} caso(s): "
            f"{recomendado['pendientes']} pendiente(s), {recomendado['en_proceso']} en proceso, "
            f"{recomendado['urgentes_activos']} urgente(s) activo(s))."
        )

    return {
        'asignacion_permitida': asignacion_permitida,
        'motivo_no_asignable': motivo_no_asignable,
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
    incrementar_intento: bool = True,
) -> TriajePeticion:
    ahora = _ahora_local()
    if incrementar_intento:
        triaje.intentos_envio = int(triaje.intentos_envio or 0) + 1
    triaje.ultimo_intento_en = ahora
    triaje.lease_expira_en = None
    triaje.ultimo_codigo_http = codigo_http
    triaje.ultimo_error_envio = redactar_secretos(error_msg)

    if int(triaje.intentos_envio or 0) >= int(triaje.max_intentos or 3):
        triaje.estado_envio = TriajePeticion.ENVIO_FALLO_DEFINITIVO
        triaje.proximo_reintento_en = None
    else:
        triaje.estado_envio = TriajePeticion.ENVIO_FALLO_TEMPORAL
        espera_seg = backoff_base * (2 ** max(0, int(triaje.intentos_envio or 1) - 1))
        triaje.proximo_reintento_en = ahora + timedelta(seconds=espera_seg)

    triaje.save(
        update_fields=[
            'intentos_envio',
            'ultimo_intento_en',
            'lease_expira_en',
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
    Reclama el registro mediante un UPDATE condicional atómico con lease (sin retener
    bloqueos durante HTTP), despacha hacia n8n y actualiza el estado usando fencing
    mediante `despacho_token`.
    """
    cfg = obtener_config_triaje()
    if not cfg['activo'] and not forzar_reenvio:
        return {'enviado': False, 'id_triaje': id_triaje, 'motivo': 'DESACTIVADO'}

    ahora = _ahora_local()
    triaje = (
        TriajePeticion.objects.select_related(
            'peticion',
            'peticion__id_tipo',
            'peticion__id_estado',
            'peticion__id_ubicacion',
            'peticion__responsable',
        )
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

    if (
        not forzar_reenvio
        and triaje.estado_envio == TriajePeticion.ENVIO_ENVIANDO
        and triaje.lease_expira_en is not None
        and triaje.lease_expira_en > ahora
    ):
        return {
            'enviado': False,
            'en_curso': True,
            'id_triaje': triaje.id_triaje,
            'estado_envio': triaje.estado_envio,
        }

    if (
        not forzar_reenvio
        and triaje.estado_envio == TriajePeticion.ENVIO_FALLO_TEMPORAL
        and triaje.proximo_reintento_en is not None
        and triaje.proximo_reintento_en > ahora
    ):
        return {
            'enviado': False,
            'esperando_backoff': True,
            'id_triaje': triaje.id_triaje,
            'estado_envio': triaje.estado_envio,
        }

    if not forzar_reenvio and int(triaje.intentos_envio or 0) >= int(triaje.max_intentos or 3):
        TriajePeticion.objects.filter(pk=id_triaje).update(
            estado_envio=TriajePeticion.ENVIO_FALLO_DEFINITIVO,
            lease_expira_en=None,
            proximo_reintento_en=None,
            fecha_actualizacion=ahora,
        )
        return {
            'enviado': False,
            'id_triaje': triaje.id_triaje,
            'estado_envio': TriajePeticion.ENVIO_FALLO_DEFINITIVO,
        }

    try:
        webhook_url = _validar_webhook_url(cfg['webhook_url'])
    except ValueError as exc:
        with transaction.atomic():
            triaje_err = TriajePeticion.objects.select_for_update().filter(pk=id_triaje).first()
            if triaje_err:
                _registrar_fallo_envio(triaje_err, error_msg=str(exc), backoff_base=cfg['backoff_base'])
                return {'enviado': False, 'id_triaje': triaje_err.id_triaje, 'estado_envio': triaje_err.estado_envio}
        return {'enviado': False, 'id_triaje': id_triaje, 'estado_envio': TriajePeticion.ENVIO_FALLO_TEMPORAL}

    claimed_token = uuid.uuid4().hex
    lease_hasta = ahora + timedelta(seconds=cfg['lease_timeout'])

    condicion_elegible = Q(pk=id_triaje)
    if not forzar_reenvio:
        condicion_elegible &= ~Q(estado_analisis=TriajePeticion.ANALISIS_COMPLETADO)
        condicion_elegible &= ~Q(estado_envio=TriajePeticion.ENVIO_ENVIADO)
        condicion_elegible &= ~(
            Q(estado_envio=TriajePeticion.ENVIO_ENVIANDO)
            & Q(lease_expira_en__isnull=False)
            & Q(lease_expira_en__gt=ahora)
        )
        condicion_elegible &= ~(
            Q(estado_envio=TriajePeticion.ENVIO_FALLO_TEMPORAL)
            & Q(proximo_reintento_en__isnull=False)
            & Q(proximo_reintento_en__gt=ahora)
        )
        condicion_elegible &= Q(intentos_envio__lt=F('max_intentos'))

    with transaction.atomic():
        reclamadas = TriajePeticion.objects.filter(condicion_elegible).update(
            estado_envio=TriajePeticion.ENVIO_ENVIANDO,
            intentos_envio=F('intentos_envio') + 1,
            ultimo_intento_en=ahora,
            lease_expira_en=lease_hasta,
            proximo_reintento_en=None,
            despacho_token=claimed_token,
            fecha_actualizacion=ahora,
        )

    if reclamadas != 1:
        actual = TriajePeticion.objects.filter(pk=id_triaje).values('estado_envio', 'estado_analisis').first() or {}
        return {
            'enviado': False,
            'en_curso': actual.get('estado_envio') == TriajePeticion.ENVIO_ENVIANDO,
            'idempotente': (
                actual.get('estado_envio') == TriajePeticion.ENVIO_ENVIADO
                or actual.get('estado_analisis') == TriajePeticion.ANALISIS_COMPLETADO
            ),
            'id_triaje': id_triaje,
            'estado_envio': actual.get('estado_envio'),
        }

    clave_idem = triaje.clave_idempotencia
    payload = construir_payload_peticion_triaje(
        triaje.peticion,
        clave_idempotencia=clave_idem,
        despacho_token=claimed_token,
    )

    headers = {
        'Content-Type': 'application/json',
        HEADER_IDEMPOTENCY_KEY: clave_idem,
        HEADER_DISPATCH_TOKEN: claimed_token,
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
            if triaje_upd and triaje_upd.despacho_token == claimed_token:
                _registrar_fallo_envio(
                    triaje_upd,
                    error_msg=f'Error de conexión con n8n: {exc}',
                    backoff_base=cfg['backoff_base'],
                    incrementar_intento=False,
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
        if triaje_upd.despacho_token != claimed_token:
            return {
                'enviado': False,
                'fenced': True,
                'id_triaje': id_triaje,
                'estado_envio': triaje_upd.estado_envio,
            }

        ahora_fin = _ahora_local()
        if 200 <= status_code < 300:
            triaje_upd.estado_envio = TriajePeticion.ENVIO_ENVIADO
            triaje_upd.lease_expira_en = None
            if triaje_upd.estado_analisis == TriajePeticion.ANALISIS_PENDIENTE:
                triaje_upd.estado_analisis = TriajePeticion.ANALISIS_EN_PROCESO
            triaje_upd.ultimo_codigo_http = status_code
            triaje_upd.ultimo_error_envio = ''
            triaje_upd.proximo_reintento_en = None
            triaje_upd.enviado_en = ahora_fin
            triaje_upd.save(
                update_fields=[
                    'estado_envio',
                    'lease_expira_en',
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
            incrementar_intento=False,
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
        triaje.estado_envio in (TriajePeticion.ENVIO_ENVIADO, TriajePeticion.ENVIO_ENVIANDO)
        or triaje.estado_analisis == TriajePeticion.ANALISIS_COMPLETADO
    ):
        return {
            'ejecutado': False,
            'idempotente': True,
            'creada': creada,
            'id_triaje': triaje.id_triaje,
            'estado_envio': triaje.estado_envio,
        }

    if forzar_reenvio and (
        triaje.estado_envio in (
            TriajePeticion.ENVIO_FALLO_DEFINITIVO,
            TriajePeticion.ENVIO_FALLO_TEMPORAL,
            TriajePeticion.ENVIO_OMITIDO,
        )
        or triaje.estado_analisis in (
            TriajePeticion.ANALISIS_FALLIDO,
            TriajePeticion.ANALISIS_PARCIAL,
        )
    ):
        campos_reset = [
            'estado_envio',
            'intentos_envio',
            'lease_expira_en',
            'proximo_reintento_en',
            'fecha_actualizacion',
        ]
        triaje.estado_envio = TriajePeticion.ENVIO_PENDIENTE
        triaje.intentos_envio = 0
        triaje.lease_expira_en = None
        triaje.proximo_reintento_en = None
        if triaje.estado_analisis in (
            TriajePeticion.ANALISIS_FALLIDO,
            TriajePeticion.ANALISIS_PARCIAL,
        ):
            triaje.estado_analisis = TriajePeticion.ANALISIS_PENDIENTE
            campos_reset.append('estado_analisis')
        triaje.save(update_fields=campos_reset)

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
        res = despachar_triaje_por_id(item.id_triaje)
        detalles.append(res)
        if res.get('enviado'):
            enviados += 1
        elif not res.get('omitido_por_lock') and not res.get('en_curso'):
            fallidos += 1

    return {
        'activo': True,
        'procesados': len(pendientes),
        'enviados': enviados,
        'fallidos': fallidos,
        'detalles': detalles,
    }


def recuperar_trabajos_interrumpidos(
    *,
    limite: int = 20,
    usar_advisory_lock: bool = False,
) -> Dict[str, Any]:
    """
    Recupera trabajos interrumpidos tras reinicios o caídas:
    1) ENVIANDO con lease expirado (worker cayó en medio de HTTP).
    2) ENVIADO + EN_PROCESO con timeout de análisis vencido (n8n no respondió callback).
    3) Despacha PENDIENTE y FALLO_TEMPORAL vencidos.
    """
    cfg = obtener_config_triaje()
    if not cfg['activo']:
        return {'activo': False, 'recuperados_lease': 0, 'recuperados_analisis': 0, 'procesados': 0}

    advisory_adquirido = False
    if usar_advisory_lock and connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [_PG_ADVISORY_LOCK_KEY])
            fila = cursor.fetchone()
            advisory_adquirido = bool(fila and fila[0])
        if not advisory_adquirido:
            return {'activo': True, 'omitido_por_lock': True, 'procesados': 0}

    try:
        ahora = _ahora_local()
        recuperados_lease = 0
        recuperados_analisis = 0

        # 1. Leases expirados en ENVIANDO
        with transaction.atomic():
            atascados_envio = list(
                _select_for_update_qs(
                    TriajePeticion.objects.filter(
                        estado_envio=TriajePeticion.ENVIO_ENVIANDO,
                        lease_expira_en__isnull=False,
                        lease_expira_en__lte=ahora,
                    ).order_by('lease_expira_en', 'id_triaje')[: max(1, min(100, int(limite)))]
                )
            )
            for item in atascados_envio:
                item.lease_expira_en = None
                item.despacho_token = ''
                if int(item.intentos_envio or 0) >= int(item.max_intentos or 3):
                    item.estado_envio = TriajePeticion.ENVIO_FALLO_DEFINITIVO
                    item.proximo_reintento_en = None
                    item.ultimo_error_envio = 'Envío interrumpido (lease expirado; reintentos agotados).'
                else:
                    item.estado_envio = TriajePeticion.ENVIO_FALLO_TEMPORAL
                    item.proximo_reintento_en = ahora
                    item.ultimo_error_envio = 'Envío interrumpido por reinicio o caída; reencolado automáticamente.'
                item.save(
                    update_fields=[
                        'estado_envio',
                        'lease_expira_en',
                        'despacho_token',
                        'proximo_reintento_en',
                        'ultimo_error_envio',
                        'fecha_actualizacion',
                    ]
                )
                recuperados_lease += 1

        # 2. Análisis EN_PROCESO sin callback más allá de TRIAJE_ANALISIS_TIMEOUT_SEGUNDOS
        corte_analisis = ahora - timedelta(seconds=cfg['analisis_timeout'])
        with transaction.atomic():
            atascados_analisis = list(
                _select_for_update_qs(
                    TriajePeticion.objects.filter(
                        estado_envio=TriajePeticion.ENVIO_ENVIADO,
                        estado_analisis=TriajePeticion.ANALISIS_EN_PROCESO,
                        enviado_en__isnull=False,
                        enviado_en__lte=corte_analisis,
                    ).order_by('enviado_en', 'id_triaje')[: max(1, min(100, int(limite)))]
                )
            )
            for item in atascados_analisis:
                item.despacho_token = ''
                if int(item.intentos_envio or 0) >= int(item.max_intentos or 3):
                    item.estado_envio = TriajePeticion.ENVIO_FALLO_DEFINITIVO
                    item.estado_analisis = TriajePeticion.ANALISIS_FALLIDO
                    item.proximo_reintento_en = None
                    item.ultimo_error_envio = 'Timeout esperando callback de n8n (reintentos agotados).'
                else:
                    item.estado_envio = TriajePeticion.ENVIO_FALLO_TEMPORAL
                    item.estado_analisis = TriajePeticion.ANALISIS_PENDIENTE
                    item.proximo_reintento_en = ahora
                    item.ultimo_error_envio = 'Timeout esperando callback de n8n; reencolado automáticamente.'
                item.save(
                    update_fields=[
                        'estado_envio',
                        'estado_analisis',
                        'despacho_token',
                        'proximo_reintento_en',
                        'ultimo_error_envio',
                        'fecha_actualizacion',
                    ]
                )
                recuperados_analisis += 1

        res_reintentos = procesar_reintentos_pendientes(limite=limite)
        return {
            **res_reintentos,
            'recuperados_lease': recuperados_lease,
            'recuperados_analisis': recuperados_analisis,
        }
    finally:
        if advisory_adquirido and connection.vendor == 'postgresql':
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(%s)', [_PG_ADVISORY_LOCK_KEY])
            except Exception:
                pass


def _normalizar_lista_textos(valor: Any, *, max_items: int = 20, max_len: int = 120) -> List[str]:
    if not isinstance(valor, list):
        return []
    vistos = set()
    limpios: List[str] = []
    for item in valor:
        texto = str(item or '').strip()[:max_len]
        if not texto:
            continue
        clave = texto.lower()
        if clave in vistos:
            continue
        vistos.add(clave)
        limpios.append(texto)
        if len(limpios) >= max_items:
            break
    return limpios


def registrar_resultado_triaje(id_peticion: int, datos: Dict[str, Any]) -> TriajePeticion:
    """
    Persiste de forma idempotente y protegida contra concurrencia/callbacks obsoletos
    el resultado del análisis recibido desde n8n.
    Nunca modifica Peticiones.prioridad ni asigna Peticiones.asignado_a.
    """
    peticion = (
        Peticiones.objects.select_related('id_estado', 'asignado_a')
        .prefetch_related('evidenciapeticiones_set')
        .filter(pk=id_peticion)
        .first()
    )
    if not peticion:
        raise PeticionNoEncontradaError(f'La petición #{id_peticion} no existe.')

    prioridad_norm = normalizar_prioridad_sugerida(datos.get('prioridad_sugerida'))

    asignacion_ok, motivo_no_asig = _evaluar_elegibilidad_asignacion(peticion)
    vet_rec_id = datos.get('veterinario_recomendado_id')
    veterinario_obj = None
    aviso_vet = ''
    if not asignacion_ok:
        if vet_rec_id is not None:
            aviso_vet = f' [{motivo_no_asig}]'
    elif vet_rec_id is not None:
        veterinario_obj = (
            Usuarios.objects.filter(
                id_usuario=vet_rec_id,
                id_rol_id=ROL_VETERINARIO,
                activo=True,
            )
            .filter(Q(id_sede__isnull=True) | Q(id_sede__activo=True))
            .first()
        )
        if not veterinario_obj:
            aviso_vet = f' [Veterinario recomendado #{vet_rec_id} no válido, inactivo o con sede inactiva]'

    estado_analisis = datos.get('estado_analisis') or TriajePeticion.ANALISIS_COMPLETADO
    estados_validos = {c[0] for c in TriajePeticion.ESTADOS_ANALISIS_CHOICES}
    if estado_analisis not in estados_validos:
        estado_analisis = TriajePeticion.ANALISIS_COMPLETADO

    posibles_dup = datos.get('posibles_duplicados')
    if not isinstance(posibles_dup, list):
        posibles_dup = []

    carga_snap = datos.get('carga_veterinarios_snapshot')
    if not isinstance(carga_snap, list):
        carga_snap = []

    resultado_raw = datos.get('resultado_raw')
    if not isinstance(resultado_raw, dict):
        resultado_raw = {}
    ia_raw = resultado_raw.get('ia') if isinstance(resultado_raw.get('ia'), dict) else {}

    signos = _normalizar_lista_textos(datos.get('signos_detectados'), max_items=15, max_len=80)
    signos_reportados = _normalizar_lista_textos(
        datos.get('signos_reportados') or ia_raw.get('signos_reportados'),
        max_items=20,
        max_len=120,
    )

    _, todas_evidencias = extraer_fotografias_peticion(peticion)
    tiene_fotos = any(detectar_tipo_archivo(u) == 'imagen' for u in todas_evidencias)
    if tiene_fotos:
        signos_fotografia = _normalizar_lista_textos(
            datos.get('signos_fotografia') or ia_raw.get('signos_fotografia'),
            max_items=20,
            max_len=120,
        )
    else:
        signos_fotografia = []

    raw_advertencias = (
        datos.get('advertencias_contradiccion')
        if datos.get('advertencias_contradiccion') is not None
        else ia_raw.get('advertencias_contradiccion')
    )
    if raw_advertencias is None and isinstance(ia_raw.get('advertencias'), list):
        raw_advertencias = ia_raw.get('advertencias')
    advertencias_contradiccion = _normalizar_lista_textos(raw_advertencias, max_items=10, max_len=400)
    contradiccion_flag = bool(
        datos.get('contradiccion_foto_descripcion')
        or ia_raw.get('contradiccion_foto_descripcion')
        or advertencias_contradiccion
    )

    confianza = datos.get('nivel_confianza')
    if confianza is not None:
        try:
            conf_f = float(confianza)
            confianza = max(0.0, min(1.0, round(conf_f, 4))) if math.isfinite(conf_f) else None
        except (TypeError, ValueError):
            confianza = None

    justificacion_rec = str(datos.get('justificacion_recomendacion') or '').strip()
    if not asignacion_ok and not justificacion_rec:
        justificacion_rec = motivo_no_asig
    elif aviso_vet:
        justificacion_rec = f'{justificacion_rec}{aviso_vet}'.strip()

    ahora = _ahora_local()
    clave_idem = str(datos.get('clave_idempotencia') or '').strip() or f'triaje-pet-{id_peticion}'
    token_entrante = str(datos.get('despacho_token') or '').strip()
    exec_id_entrante = str(datos.get('n8n_execution_id') or '').strip()[:120]

    with transaction.atomic():
        triaje, _ = TriajePeticion.objects.select_for_update().get_or_create(
            peticion_id=id_peticion,
            defaults={
                'clave_idempotencia': clave_idem,
                'prioridad_original': peticion.prioridad,
                'estado_envio': TriajePeticion.ENVIO_ENVIADO,
            },
        )

        # Protección contra callbacks de intentos obsoletos (fencing token)
        if token_entrante and triaje.despacho_token and token_entrante != triaje.despacho_token:
            raise StaleTriajeCallbackError(
                'Callback rechazado: despacho_token obsoleto respecto al intento vigente.'
            )

        # Protección idempotente / anti-regresión cuando ya está COMPLETADO
        if triaje.estado_analisis == TriajePeticion.ANALISIS_COMPLETADO:
            mismo_exec = bool(exec_id_entrante and triaje.n8n_execution_id and exec_id_entrante == triaje.n8n_execution_id)
            mismo_token = bool(token_entrante and triaje.despacho_token and token_entrante == triaje.despacho_token)
            if mismo_exec or (mismo_token and estado_analisis == TriajePeticion.ANALISIS_COMPLETADO) or (
                not exec_id_entrante and not token_entrante and estado_analisis == TriajePeticion.ANALISIS_COMPLETADO
            ):
                return triaje
            raise StaleTriajeCallbackError(
                'Callback rechazado: la petición ya posee un resultado de triaje completado.'
            )

        triaje.estado_analisis = estado_analisis
        triaje.estado_envio = TriajePeticion.ENVIO_ENVIADO
        triaje.lease_expira_en = None
        triaje.proximo_reintento_en = None
        if prioridad_norm is not None:
            triaje.prioridad_sugerida = prioridad_norm
        if confianza is not None:
            triaje.nivel_confianza = confianza
        triaje.justificacion = str(datos.get('justificacion') or '').strip()
        triaje.resumen_texto = str(datos.get('resumen_texto') or '').strip()
        triaje.resumen_vision = str(datos.get('resumen_vision') or '').strip()
        triaje.signos_detectados = signos
        triaje.signos_reportados = signos_reportados
        triaje.signos_fotografia = signos_fotografia
        triaje.contradiccion_foto_descripcion = contradiccion_flag
        triaje.advertencias_contradiccion = advertencias_contradiccion
        triaje.posibles_duplicados = posibles_dup[:20]
        triaje.total_duplicados_detectados = len(posibles_dup)
        triaje.veterinario_recomendado = veterinario_obj
        triaje.justificacion_recomendacion = justificacion_rec
        triaje.carga_veterinarios_snapshot = carga_snap[:50]
        triaje.modelo_texto_usado = str(datos.get('modelo_texto_usado') or '')[:100]
        triaje.modelo_vision_usado = str(datos.get('modelo_vision_usado') or '')[:100]
        triaje.n8n_execution_id = exec_id_entrante
        triaje.resultado_raw = resultado_raw
        triaje.version_resultado = int(triaje.version_resultado or 0) + 1
        if estado_analisis in (TriajePeticion.ANALISIS_COMPLETADO, TriajePeticion.ANALISIS_PARCIAL):
            triaje.completado_en = ahora
        triaje.save()

        rt_notificar.actualizacion_silenciosa(
            'peticion.estado_actualizado',
            roles=(1, 2),
            accion='triaje_actualizado',
            referencia={
                'id_peticion': peticion.id_peticion,
                'numero_radicado': peticion.numero_radicado,
                'estado_analisis': triaje.estado_analisis,
                'prioridad_sugerida': triaje.prioridad_sugerida,
            },
        )

    return triaje


def _nombre_usuario_auditoria(usuario) -> str:
    if not usuario:
        return 'Área Jurídica'
    return f'{getattr(usuario, "nombre", "") or ""} {getattr(usuario, "apellido", "") or ""}'.strip() or getattr(
        usuario, 'email', 'Usuario'
    )


def _rol_usuario_auditoria(usuario) -> str:
    rol_obj = getattr(usuario, 'id_rol', None)
    return getattr(rol_obj, 'nombre_rol', '') or ''


def registrar_decision_humana_triaje(
    id_peticion: int,
    usuario,
    datos: Dict[str, Any],
) -> Tuple[TriajePeticion, bool]:
    """
    Registra la decisión humana (ACEPTADA o RECHAZADA) sobre una recomendación
    del triaje inteligente (`PRIORIDAD` o `VETERINARIO`), reutilizando los flujos
    autorizados existentes con atomicidad, idempotencia y trazabilidad completa.
    Retorna `(triaje, cambiada)`.
    """
    tipo_rec = str(datos.get('tipo_recomendacion') or '').strip().upper()
    decision = str(datos.get('decision') or '').strip().upper()
    observacion = str(datos.get('observacion') or datos.get('motivo') or '').strip()[:1000]

    if tipo_rec not in ('PRIORIDAD', 'VETERINARIO'):
        raise TriajeDecisionInvalidaError('Tipo de recomendación inválido. Use PRIORIDAD o VETERINARIO.')
    if decision not in (TriajePeticion.DECISION_ACEPTADA, TriajePeticion.DECISION_RECHAZADA):
        raise TriajeDecisionInvalidaError('Decisión inválida. Use ACEPTADA o RECHAZADA.')

    ahora = _ahora_local()
    ahora_iso = ahora.isoformat() if hasattr(ahora, 'isoformat') else str(ahora)
    cambiada = True

    with transaction.atomic():
        triaje = (
            TriajePeticion.objects.select_for_update()
            .filter(peticion_id=id_peticion)
            .first()
        )
        if not triaje:
            raise TriajeNoEncontradoError(f'No existe registro de triaje para la petición #{id_peticion}.')

        # Bloquear también la fila de Peticiones sin joins nulables (PostgreSQL prohíbe FOR UPDATE en outer joins)
        peticion = Peticiones.objects.select_for_update().get(pk=triaje.peticion_id)
        estado_nombre = getattr(getattr(peticion, 'id_estado', None), 'nombre', '') or ''
        estado_norm = normalizar_texto_juridico(estado_nombre)

        # Idempotencia estricta: evitar duplicar historial, asignaciones o notificaciones en reintentos
        if tipo_rec == 'PRIORIDAD' and triaje.decision_prioridad == decision:
            if decision == TriajePeticion.DECISION_RECHAZADA or (
                decision == TriajePeticion.DECISION_ACEPTADA
                and triaje.prioridad_sugerida
                and peticion.prioridad == triaje.prioridad_sugerida
            ):
                cambiada = False
        elif tipo_rec == 'VETERINARIO' and triaje.decision_veterinario == decision:
            if decision == TriajePeticion.DECISION_RECHAZADA or (
                decision == TriajePeticion.DECISION_ACEPTADA
                and triaje.veterinario_recomendado_id
                and peticion.asignado_a_id == triaje.veterinario_recomendado_id
            ):
                cambiada = False

        if not cambiada:
            return (
                TriajePeticion.objects.select_related(
                    'peticion',
                    'peticion__id_estado',
                    'peticion__responsable',
                    'peticion__asignado_a',
                    'veterinario_recomendado',
                    'veterinario_recomendado__id_sede',
                    'decision_prioridad_por',
                    'decision_veterinario_por',
                )
                .prefetch_related('peticion__evidenciapeticiones_set')
                .get(pk=triaje.pk),
                False,
            )

        if decision == TriajePeticion.DECISION_ACEPTADA:
            if estado_norm in ('duplicada', 'duplicado') or es_peticion_duplicada(peticion):
                raise TriajeDecisionInvalidaError(
                    'No se pueden aceptar recomendaciones operativas sobre una petición marcada como duplicada.',
                    status_code=409,
                    codigo='PETICION_DUPLICADA',
                )
            if estado_norm in ESTADOS_TERMINALES_NORM:
                raise TriajeDecisionInvalidaError(
                    f"La petición se encuentra en estado terminal '{estado_nombre}'; no admite modificaciones operativas.",
                    status_code=409,
                    codigo='PETICION_TERMINAL',
                )

        historial = list(triaje.historial_decisiones or [])

        if tipo_rec == 'PRIORIDAD':
            if not triaje.prioridad_sugerida:
                raise TriajeDecisionInvalidaError(
                    'El análisis de triaje no tiene una prioridad sugerida disponible.',
                    status_code=400,
                    codigo='SIN_PRIORIDAD_SUGERIDA',
                )
            prioridad_ant = peticion.prioridad
            if decision == TriajePeticion.DECISION_ACEPTADA:
                peticion.prioridad = triaje.prioridad_sugerida
                peticion.save(update_fields=['prioridad'])
                triaje.prioridad_aplicada = triaje.prioridad_sugerida
                valor_resultante = triaje.prioridad_sugerida
                obs_hist = (
                    f"Prioridad sugerida por Triaje IA ({triaje.prioridad_sugerida}) ACEPTADA "
                    f"(anterior: {prioridad_ant or 'Sin prioridad'})."
                )
            else:
                valor_resultante = prioridad_ant or 'Sin cambios'
                obs_hist = (
                    f"Prioridad sugerida por Triaje IA ({triaje.prioridad_sugerida}) RECHAZADA; "
                    f"se conserva '{prioridad_ant or 'Sin prioridad'}'."
                )
            if observacion:
                obs_hist = f"{obs_hist} Motivo: {observacion}"

            triaje.decision_prioridad = decision
            triaje.decision_prioridad_por = usuario
            triaje.decision_prioridad_en = ahora
            triaje.decision_prioridad_observacion = observacion

            historial.append({
                'tipo_recomendacion': 'PRIORIDAD',
                'decision': decision,
                'valor_sugerido': triaje.prioridad_sugerida,
                'valor_anterior': prioridad_ant,
                'valor_resultante': valor_resultante,
                'observacion': observacion,
                'usuario_id': getattr(usuario, 'id_usuario', None),
                'usuario_nombre': _nombre_usuario_auditoria(usuario),
                'usuario_rol': _rol_usuario_auditoria(usuario),
                'fecha': ahora_iso,
            })
            triaje.historial_decisiones = historial[-50:]
            triaje.save(
                update_fields=[
                    'decision_prioridad',
                    'decision_prioridad_por',
                    'decision_prioridad_en',
                    'decision_prioridad_observacion',
                    'prioridad_aplicada',
                    'historial_decisiones',
                    'fecha_actualizacion',
                ]
            )

            registrar_historial_peticion(
                peticion=peticion,
                tipo_cambio='TRIAJE_PRIORIDAD',
                estado_anterior=peticion.id_estado,
                estado_nuevo=peticion.id_estado,
                veterinario_anterior=peticion.asignado_a,
                veterinario_nuevo=peticion.asignado_a,
                usuario_accion=usuario,
                observacion=obs_hist,
                raise_on_error=True,
            )
            if decision == TriajePeticion.DECISION_ACEPTADA:
                rt_notificar.actualizacion_silenciosa(
                    'peticion.estado_actualizado',
                    roles=(1, 2),
                    accion='prioridad_triaje_aceptada',
                    referencia={
                        'id_peticion': peticion.id_peticion,
                        'numero_radicado': peticion.numero_radicado,
                        'prioridad': peticion.prioridad,
                    },
                )
        else:
            if not triaje.veterinario_recomendado_id:
                raise TriajeDecisionInvalidaError(
                    'El análisis de triaje no tiene un veterinario recomendado disponible.',
                    status_code=400,
                    codigo='SIN_VETERINARIO_RECOMENDADO',
                )
            vet_rec_obj = triaje.veterinario_recomendado
            vet_rec_nombre = _nombre_usuario_auditoria(vet_rec_obj)
            vet_ant = peticion.asignado_a

            if decision == TriajePeticion.DECISION_ACEPTADA:
                if not getattr(vet_rec_obj, 'activo', False) or getattr(vet_rec_obj, 'id_rol_id', None) != ROL_VETERINARIO:
                    raise TriajeDecisionInvalidaError(
                        'El veterinario recomendado ya no está activo o no es válido para asignación.',
                        status_code=409,
                        codigo='VETERINARIO_NO_DISPONIBLE',
                    )
                if getattr(vet_rec_obj, 'id_sede', None) is not None and getattr(vet_rec_obj.id_sede, 'activo', True) is False:
                    raise TriajeDecisionInvalidaError(
                        'La sede del veterinario recomendado se encuentra inactiva.',
                        status_code=409,
                        codigo='SEDE_INACTIVA',
                    )

                # Si aún no está asignado al veterinario recomendado, ejecutar atómicamente
                # mediante el flujo autorizado AsignarPeticionView dentro de esta misma transacción.
                if peticion.asignado_a_id != triaje.veterinario_recomendado_id:
                    from rest_framework.test import APIRequestFactory, force_authenticate
                    from peticiones.api.views import AsignarPeticionView

                    sub_factory = APIRequestFactory()
                    motivo_asig = observacion or (
                        f'Asignación aceptada desde recomendación de Triaje Inteligente '
                        f'(Veterinario #{triaje.veterinario_recomendado_id}).'
                    )
                    sub_req = sub_factory.patch(
                        f'/api/peticiones/{id_peticion}/asignar/',
                        {'asignado_a': triaje.veterinario_recomendado_id, 'motivo': motivo_asig},
                        format='json',
                    )
                    force_authenticate(sub_req, user=usuario)
                    resp_asig = AsignarPeticionView.as_view()(sub_req, id_peticion=id_peticion)
                    if int(resp_asig.status_code) >= 400:
                        err_data = resp_asig.data if isinstance(getattr(resp_asig, 'data', None), dict) else {}
                        msg = (
                            err_data.get('error')
                            or err_data.get('detail')
                            or 'No fue posible asignar al veterinario recomendado.'
                        )
                        raise TriajeDecisionInvalidaError(
                            str(msg),
                            status_code=int(resp_asig.status_code),
                            codigo=str(err_data.get('codigo') or 'ASIGNACION_RECHAZADA'),
                        )
                    peticion.refresh_from_db()

                triaje.veterinario_asignado_decision_id = triaje.veterinario_recomendado_id
                valor_resultante = f'{vet_rec_nombre} (#{triaje.veterinario_recomendado_id})'
                # Actualizar snapshot de carga laboral para reflejar inmediatamente la nueva asignación
                try:
                    carga_actualizada = calcular_carga_veterinarios()
                    if isinstance(carga_actualizada.get('veterinarios'), list):
                        triaje.carga_veterinarios_snapshot = carga_actualizada['veterinarios'][:50]
                except Exception:
                    pass
            else:
                valor_resultante = (
                    f'{_nombre_usuario_auditoria(vet_ant)} (#{vet_ant.id_usuario})'
                    if vet_ant
                    else 'Sin asignar'
                )

            triaje.decision_veterinario = decision
            triaje.decision_veterinario_por = usuario
            triaje.decision_veterinario_en = ahora
            triaje.decision_veterinario_observacion = observacion

            historial.append({
                'tipo_recomendacion': 'VETERINARIO',
                'decision': decision,
                'valor_sugerido': f'{vet_rec_nombre} (#{triaje.veterinario_recomendado_id})',
                'valor_anterior': (
                    f'{_nombre_usuario_auditoria(vet_ant)} (#{vet_ant.id_usuario})' if vet_ant else 'Sin asignar'
                ),
                'valor_resultante': valor_resultante,
                'observacion': observacion,
                'usuario_id': getattr(usuario, 'id_usuario', None),
                'usuario_nombre': _nombre_usuario_auditoria(usuario),
                'usuario_rol': _rol_usuario_auditoria(usuario),
                'fecha': ahora_iso,
            })
            triaje.historial_decisiones = historial[-50:]
            triaje.save(
                update_fields=[
                    'decision_veterinario',
                    'decision_veterinario_por',
                    'decision_veterinario_en',
                    'decision_veterinario_observacion',
                    'veterinario_asignado_decision_id',
                    'carga_veterinarios_snapshot',
                    'historial_decisiones',
                    'fecha_actualizacion',
                ]
            )

            if decision == TriajePeticion.DECISION_RECHAZADA:
                obs_hist = f'Recomendación de veterinario ({vet_rec_nombre}) RECHAZADA.'
                if observacion:
                    obs_hist = f"{obs_hist} Motivo: {observacion}"
                registrar_historial_peticion(
                    peticion=peticion,
                    tipo_cambio='TRIAJE_VETERINARIO',
                    estado_anterior=peticion.id_estado,
                    estado_nuevo=peticion.id_estado,
                    veterinario_anterior=peticion.asignado_a,
                    veterinario_nuevo=peticion.asignado_a,
                    usuario_accion=usuario,
                    observacion=obs_hist,
                    raise_on_error=True,
                )

    return (
        TriajePeticion.objects.select_related(
            'peticion',
            'peticion__id_estado',
            'peticion__responsable',
            'peticion__asignado_a',
            'veterinario_recomendado',
            'veterinario_recomendado__id_sede',
            'decision_prioridad_por',
            'decision_veterinario_por',
        )
        .prefetch_related('peticion__evidenciapeticiones_set')
        .get(pk=triaje.pk),
        True,
    )
