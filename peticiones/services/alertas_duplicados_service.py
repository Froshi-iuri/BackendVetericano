"""
Servicio de dominio para el almacenamiento, idempotencia, concurrencia,
notificación, auditoría y resolución de alertas de posibles peticiones duplicadas.

Fase 4B: opera únicamente sobre las tablas y modelos existentes
(`PeticionPosibleDuplicado`, `NotificacionJuridico`, `HistorialPeticiones`) sin
exponer endpoints HTTP ni modificar peticiones, evidencias o historias clínicas.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import requests

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from vetericano.ordenamiento import orden_valor_primero

from peticiones.models import (
    EstadoPeticiones,
    HistorialPeticiones,
    NotificacionJuridico,
    Peticiones,
    PeticionPosibleDuplicado,
)
from peticiones.services import dinov2_service
from peticiones.services.duplicados_service import (
    PeticionNoEncontradaError,
    RADIO_GPS_METROS_DEFAULT,
    _calcular_puntaje_combinado,
    buscar_peticiones_similares_por_descripcion,
    calcular_distancia_haversine_metros,
    es_descripcion_valida_para_comparacion,
    extraer_fotografias_peticion,
    normalizar_coordenadas,
    normalizar_descripcion,
    obtener_config_duplicados,
)
from peticiones.utils import detectar_tipo_archivo, normalizar_texto_juridico
from users.api.permissions import es_admin_o_juridico
from users.models import Usuarios

logger = logging.getLogger(__name__)


# ==============================================================================
# CONSTANTES DE INTEGRACIÓN
# ==============================================================================

# Contrato NotificacionJuridico.tipo (max_length=50)
TIPO_NOTIFICACION_POSIBLE_DUPLICADO = 'POSIBLE_DUPLICADO'

# Contrato HistorialPeticiones.tipo_cambio (varchar(30) en Postgres real)
HISTORIAL_TIPO_DUPLICADO_DETECTADO = 'DUPLICADO_DETECTADO'
HISTORIAL_TIPO_DUPLICADO_CONFIRMADO = 'DUPLICADO_CONFIRMADO'
HISTORIAL_TIPO_DUPLICADO_DESCARTADO = 'DUPLICADO_DESCARTADO'
HISTORIAL_TIPO_DUPLICADO_REABIERTO = 'DUPLICADO_REABIERTO'

MAX_LONGITUD_TIPO_CAMBIO_HISTORIAL = 30
ESTADO_PETICION_DUPLICADA = 'Duplicada'
ESTADOS_DUPLICADOS_NORM = frozenset({'duplicada', 'duplicado'})
METODO_IMAGEN_DINOV2 = 'dinov2_small'
METODOS_IMAGEN_DHASH = frozenset({'dhash', 'dhash_fallback'})

AUTO_DETECCION_TIMEOUT_MS_DEFAULT = 1500
AUTO_DETECCION_TIMEOUT_MS_MIN = 100
AUTO_DETECCION_TIMEOUT_MS_MAX = 10000

ESTADOS_ALERTA_VALIDOS = frozenset({
    PeticionPosibleDuplicado.ESTADO_PENDIENTE,
    PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
    PeticionPosibleDuplicado.ESTADO_DESCARTADO,
})

ESTADOS_ALERTA_RESUELTOS = frozenset({
    PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
    PeticionPosibleDuplicado.ESTADO_DESCARTADO,
})

ORIGENES_DETECCION_VALIDOS = frozenset({
    PeticionPosibleDuplicado.ORIGEN_REGISTRO,
    PeticionPosibleDuplicado.ORIGEN_ENDPOINT_MANUAL,
    PeticionPosibleDuplicado.ORIGEN_REANALISIS,
})

_CRITERIO_TEXTO = 'TEXTO'
_CRITERIO_GPS = 'GPS'
_CRITERIO_IMAGEN = 'IMAGEN'


# ==============================================================================
# EXCEPCIONES DE DOMINIO
# ==============================================================================

class AlertaDuplicadoError(Exception):
    """Error base para el servicio de gestión de alertas de duplicados."""


class ParDuplicadoInvalidoError(AlertaDuplicadoError, ValueError):
    """Los identificadores o metadatos del par de peticiones son inválidos."""


class AlertaNoEncontradaError(AlertaDuplicadoError, LookupError):
    """No existe una alerta de posible duplicado para el ID o par indicado."""


class TransicionAlertaInvalidaError(AlertaDuplicadoError, ValueError):
    """La transición solicitada no respeta las reglas de estado de la alerta."""


class SeleccionPrincipalInvalidaError(AlertaDuplicadoError, ValueError):
    """La petición principal seleccionada es obligatoria o inválida para el par."""


class UsuarioRevisionInvalidoError(AlertaDuplicadoError, ValueError):
    """El usuario revisor o actor indicado no es válido o no existe."""


class Dinov2NoDisponibleParaAlertaError(AlertaDuplicadoError, RuntimeError):
    """El servicio DINOv2 no se encuentra disponible para comparar las evidencias."""

    def __init__(
        self,
        message: str,
        *,
        codigo: str = 'DINOV2_NO_DISPONIBLE',
        distancia_gps_metros: float | None = None,
    ) -> None:
        super().__init__(message)
        self.codigo = codigo
        self.distancia_gps_metros = distancia_gps_metros


# ==============================================================================
# RESULTADOS ESTRUCTURADOS
# ==============================================================================

@dataclass(frozen=True)
class ResultadoRegistroAlerta:
    """Resultado de registrar idempotentemente una alerta de posible duplicado."""

    alerta: PeticionPosibleDuplicado
    creada: bool
    actualizada: bool
    notificada: bool
    respetada_decision_previa: bool
    notificacion_id: int | None
    historial_ids: tuple[int, ...]


@dataclass(frozen=True)
class ResultadoDecisionAlerta:
    """Resultado de gestionar la decisión de Jurídico sobre una alerta."""

    alerta: PeticionPosibleDuplicado
    cambiada: bool
    estado_anterior: str
    estado_nuevo: str
    historial_ids: tuple[int, ...]


@dataclass(frozen=True)
class ResultadoBatchAlertas:
    """Resumen consolidado de alertas registradas desde una salida de detección."""

    resultados: tuple[ResultadoRegistroAlerta, ...]
    total_procesados: int
    creadas: int
    actualizadas: int
    existentes_sin_cambios: int
    respetadas_decision_previa: int
    notificaciones_creadas: int
    omitidos: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ResultadoDeteccionAutomatica:
    """Resultado consolidado y no bloqueante de la detección automática al iniciar una petición."""

    ejecutada: bool
    peticion_id: int | None
    candidatos_detectados: int
    batch: ResultadoBatchAlertas | None = None
    omitida_motivo: str | None = None
    error: str | None = None


# ==============================================================================
# UTILIDADES INTERNAS DE VALIDACIÓN Y NORMALIZACIÓN
# ==============================================================================

def _ahora_local():
    """Retorna la fecha/hora actual compatible con settings.USE_TZ."""
    ahora = timezone.now()
    if not getattr(settings, 'USE_TZ', False) and timezone.is_aware(ahora):
        return timezone.make_naive(ahora, timezone.get_current_timezone())
    return ahora


def _extraer_id_peticion(
    peticion_o_id: Peticiones | int,
    *,
    nombre_campo: str,
    error_cls: type[Exception] = ParDuplicadoInvalidoError,
) -> int:
    if isinstance(peticion_o_id, Peticiones):
        pk = peticion_o_id.pk
        if pk is None:
            raise error_cls(f'{nombre_campo} debe estar guardada en base de datos.')
        return int(pk)

    if isinstance(peticion_o_id, bool) or not isinstance(peticion_o_id, int):
        raise error_cls(f'{nombre_campo} debe ser un entero positivo o instancia de Peticiones.')

    if peticion_o_id <= 0:
        raise error_cls(f'{nombre_campo} debe ser un identificador entero positivo.')

    return int(peticion_o_id)


def _normalizar_par_ids(
    peticion_a: Peticiones | int,
    peticion_b: Peticiones | int,
) -> tuple[int, int]:
    id_a = _extraer_id_peticion(peticion_a, nombre_campo='peticion_a')
    id_b = _extraer_id_peticion(peticion_b, nombre_campo='peticion_b')
    try:
        return PeticionPosibleDuplicado.normalizar_par_ids(id_a, id_b)
    except (ValueError, ValidationError) as exc:
        raise ParDuplicadoInvalidoError(str(exc)) from exc


def _validar_float_opcional(
    valor: Any,
    *,
    nombre_campo: str,
    minimo: float | None = None,
    maximo: float | None = None,
) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise ParDuplicadoInvalidoError(f'{nombre_campo} no puede ser booleano.')
    try:
        numero = float(valor)
    except (TypeError, ValueError) as exc:
        raise ParDuplicadoInvalidoError(f'{nombre_campo} debe ser numérico.') from exc

    if not math.isfinite(numero):
        raise ParDuplicadoInvalidoError(f'{nombre_campo} debe ser un número finito.')
    if minimo is not None and numero < minimo:
        raise ParDuplicadoInvalidoError(f'{nombre_campo} no puede ser menor que {minimo}.')
    if maximo is not None and numero > maximo:
        raise ParDuplicadoInvalidoError(f'{nombre_campo} no puede ser mayor que {maximo}.')
    return numero


def _validar_int_opcional(
    valor: Any,
    *,
    nombre_campo: str,
    minimo: int | None = None,
    maximo: int | None = None,
) -> int | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise ParDuplicadoInvalidoError(f'{nombre_campo} no puede ser booleano.')
    if isinstance(valor, float):
        if not math.isfinite(valor) or not valor.is_integer():
            raise ParDuplicadoInvalidoError(f'{nombre_campo} debe ser un entero exacto.')
        entero = int(valor)
    elif isinstance(valor, int):
        entero = int(valor)
    else:
        try:
            texto = str(valor).strip()
            entero = int(texto)
        except (TypeError, ValueError) as exc:
            raise ParDuplicadoInvalidoError(f'{nombre_campo} debe ser entero.') from exc

    if minimo is not None and entero < minimo:
        raise ParDuplicadoInvalidoError(f'{nombre_campo} no puede ser menor que {minimo}.')
    if maximo is not None and entero > maximo:
        raise ParDuplicadoInvalidoError(f'{nombre_campo} no puede ser mayor que {maximo}.')
    return entero


def _normalizar_criterios(
    criterios: Any,
    *,
    coincidencia_texto: bool,
    coincidencia_gps: bool,
    coincidencia_imagen: bool,
) -> list[str]:
    if criterios is None:
        derivados: list[str] = []
        if coincidencia_texto:
            derivados.append(_CRITERIO_TEXTO)
        if coincidencia_gps:
            derivados.append(_CRITERIO_GPS)
        if coincidencia_imagen:
            derivados.append(_CRITERIO_IMAGEN)
        return derivados

    if isinstance(criterios, (str, bytes)) or not isinstance(criterios, Sequence):
        raise ParDuplicadoInvalidoError('criterios_coincidencia debe ser una secuencia de textos.')

    limpios: list[str] = []
    vistos: set[str] = set()
    for item in criterios:
        if item is None:
            continue
        etiqueta = str(item).strip().upper()
        if etiqueta and etiqueta not in vistos:
            vistos.add(etiqueta)
            limpios.append(etiqueta)
    return limpios


def _leer_float_seguro(valor: Any) -> float | None:
    if valor is None or isinstance(valor, bool):
        return None
    if not isinstance(valor, (int, float)):
        return None
    numero = float(valor)
    return numero if math.isfinite(numero) else None


def _leer_int_seguro(valor: Any) -> int | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return int(valor)
    if isinstance(valor, float) and math.isfinite(valor) and valor.is_integer():
        return int(valor)
    return None


def _leer_str_seguro(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    limpio = valor.strip()
    return limpio or None


def obtener_radio_gps_referencia(radio_metros: float | None = None) -> float:
    """Retorna el radio GPS de referencia (default 200 m)."""
    radio = _leer_float_seguro(radio_metros)
    if radio is not None and radio >= 1.0:
        return radio
    try:
        cfg = obtener_config_duplicados()
        radio_cfg = _leer_float_seguro(cfg.get('radio_metros'))
        if radio_cfg is not None and radio_cfg >= 1.0:
            return radio_cfg
    except Exception:
        pass
    return float(RADIO_GPS_METROS_DEFAULT)


def calcular_porcentaje_proximidad_gps(
    distancia_metros: float | None,
    radio_metros: float | None = None,
) -> float | None:
    """
    Calcula el porcentaje real de proximidad GPS [0, 100] dentro del radio de
    referencia (default 200 m): `100 * max(0, 1 - distancia_metros / radio_metros)`.
    """
    dist = _leer_float_seguro(distancia_metros)
    if dist is None or dist < 0.0:
        return None
    radio = obtener_radio_gps_referencia(radio_metros)
    ratio = max(0.0, min(1.0, 1.0 - (dist / radio)))
    return round(ratio * 100.0, 2)


def resolver_estado_metrica_visual(fuente: Any) -> dict[str, Any]:
    """
    Determina de forma estricta si existe comparación visual calculada por DINOv2,
    sin inventar porcentajes ni presentar dHash legado como resultado de DINOv2.
    """
    if isinstance(fuente, Mapping):
        raw_metodo = fuente.get('metodo_imagen')
        raw_hamming = fuente.get('distancia_hamming')
        raw_sim_img = fuente.get('similitud_imagen')
        raw_cos = fuente.get('similitud_visual_coseno')
        raw_pct = fuente.get('porcentaje_similitud_visual')
    else:
        raw_metodo = getattr(fuente, 'metodo_imagen', None)
        raw_hamming = getattr(fuente, 'distancia_hamming', None)
        raw_sim_img = getattr(fuente, 'similitud_imagen', None)
        raw_cos = getattr(fuente, 'similitud_visual_coseno', None)
        raw_pct = getattr(fuente, 'porcentaje_similitud_visual', None)

    metodo = _leer_str_seguro(raw_metodo)
    metodo_norm = metodo.lower() if metodo else None
    hamming = _leer_int_seguro(raw_hamming)
    sim_img = _leer_float_seguro(raw_sim_img)
    sim_cos = _leer_float_seguro(raw_cos)
    pct_guardado = _leer_float_seguro(raw_pct)

    if metodo_norm is None and hamming is not None and sim_img is not None:
        metodo_norm = 'dhash'

    es_dinov2 = (
        metodo_norm == METODO_IMAGEN_DINOV2
        or (
            sim_img is not None
            and hamming is None
            and metodo_norm not in METODOS_IMAGEN_DHASH
        )
    )

    if not es_dinov2 or (sim_img is None and pct_guardado is None):
        return {
            'metodo_imagen': metodo_norm,
            'dinov2_calculada': False,
            'visual_calculada': False,
            'similitud_visual_coseno': None,
            'similitud_visual_dinov2': None,
            'porcentaje_similitud_visual': None,
            'estado_comparacion_visual': 'No calculada',
        }

    if metodo_norm is None:
        metodo_norm = METODO_IMAGEN_DINOV2

    sim_base = sim_img if sim_img is not None else max(0.0, min(1.0, (pct_guardado or 0.0) / 100.0))
    sim_acotada = round(max(0.0, min(1.0, sim_base)), 4)
    pct_final = (
        round(max(0.0, min(100.0, pct_guardado)), 2)
        if pct_guardado is not None
        else round(sim_acotada * 100.0, 2)
    )
    cos_final = round(sim_cos, 4) if sim_cos is not None else sim_acotada
    return {
        'metodo_imagen': metodo_norm,
        'dinov2_calculada': True,
        'visual_calculada': True,
        'similitud_visual_coseno': cos_final,
        'similitud_visual_dinov2': sim_acotada,
        'porcentaje_similitud_visual': pct_final,
        'estado_comparacion_visual': 'Calculada (DINOv2)',
    }


def obtener_o_crear_estado_duplicada(*, permitir_crear: bool = True) -> EstadoPeticiones | None:
    """Obtiene o crea el estado institucional `'Duplicada'` en `estado_peticiones`."""
    estado = EstadoPeticiones.objects.filter(nombre__iexact=ESTADO_PETICION_DUPLICADA).first()
    if estado is not None:
        return estado
    for item in EstadoPeticiones.objects.all():
        if normalizar_texto_juridico(getattr(item, 'nombre', '')) in ESTADOS_DUPLICADOS_NORM:
            return item
    if not permitir_crear:
        return None
    return EstadoPeticiones.objects.create(
        nombre=ESTADO_PETICION_DUPLICADA,
        descripcion='Petición marcada como duplicada tras comparación jurídica.',
        orden=99,
        activo=True,
    )


def _obtener_estado_reactivacion(id_estado_preferido: int | None = None) -> EstadoPeticiones | None:
    """Busca un estado activo no duplicado cuando se revierte explícitamente una alerta confirmada."""
    if id_estado_preferido is not None:
        cand = EstadoPeticiones.objects.filter(pk=id_estado_preferido).first()
        if cand is not None and normalizar_texto_juridico(cand.nombre) not in ESTADOS_DUPLICADOS_NORM:
            return cand
    for nombre in ('En revisión', 'Pendiente', 'Borrador'):
        est = EstadoPeticiones.objects.filter(nombre__iexact=nombre).first()
        if est is not None:
            return est
    for item in EstadoPeticiones.objects.filter(activo=True).order_by('orden', 'id_estado'):
        if normalizar_texto_juridico(getattr(item, 'nombre', '')) not in ESTADOS_DUPLICADOS_NORM:
            return item
    return None


def es_peticion_duplicada(peticion_o_id: Peticiones | int | None) -> bool:
    """Indica si la petición fue marcada como duplicada o figura en una alerta CONFIRMADO."""
    if peticion_o_id is None:
        return False
    if isinstance(peticion_o_id, Peticiones):
        estado_obj = getattr(peticion_o_id, 'id_estado', None)
        nombre_estado = normalizar_texto_juridico(getattr(estado_obj, 'nombre', ''))
        if nombre_estado in ESTADOS_DUPLICADOS_NORM:
            return True
        pid = getattr(peticion_o_id, 'pk', None)
    elif isinstance(peticion_o_id, bool) or not isinstance(peticion_o_id, int) or peticion_o_id <= 0:
        return False
    else:
        pid = int(peticion_o_id)
        estado_nom = (
            Peticiones.objects.filter(pk=pid)
            .values_list('id_estado__nombre', flat=True)
            .first()
        )
        if normalizar_texto_juridico(estado_nom) in ESTADOS_DUPLICADOS_NORM:
            return True

    if pid is None:
        return False

    return PeticionPosibleDuplicado.objects.filter(
        estado_revision=PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
        peticion_duplicada_id=pid,
    ).exists()


def obtener_peticion_original_de_duplicada(id_peticion: int | None) -> int | None:
    """Devuelve el ID de la petición original conservada si esta petición fue confirmada como duplicada."""
    if not isinstance(id_peticion, int) or isinstance(id_peticion, bool) or id_peticion <= 0:
        return None
    return (
        PeticionPosibleDuplicado.objects.filter(
            estado_revision=PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
            peticion_duplicada_id=id_peticion,
        )
        .values_list('peticion_principal_id', flat=True)
        .first()
    )


def _normalizar_evidencia_deteccion(
    evidencia: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if evidencia is None:
        data: Mapping[str, Any] = {}
    elif isinstance(evidencia, Mapping):
        data = evidencia
    else:
        raise ParDuplicadoInvalidoError('evidencia_deteccion debe ser un diccionario o mapeo.')

    coincidencia_texto = bool(data.get('coincidencia_texto', False))
    coincidencia_gps = bool(data.get('coincidencia_gps', False))
    coincidencia_imagen = bool(data.get('coincidencia_imagen', False))

    similitud_texto = _validar_float_opcional(
        data.get('similitud_texto'),
        nombre_campo='similitud_texto',
        minimo=0.0,
        maximo=1.0,
    )
    distancia_metros = _validar_float_opcional(
        data.get('distancia_metros'),
        nombre_campo='distancia_metros',
        minimo=0.0,
    )
    distancia_hamming = _validar_int_opcional(
        data.get('distancia_hamming'),
        nombre_campo='distancia_hamming',
        minimo=0,
        maximo=64,
    )
    similitud_imagen = _validar_float_opcional(
        data.get('similitud_imagen'),
        nombre_campo='similitud_imagen',
        minimo=0.0,
        maximo=1.0,
    )
    score_bruto = data.get('score_combinado', 0.0)
    score_combinado = _validar_float_opcional(
        0.0 if score_bruto is None else score_bruto,
        nombre_campo='score_combinado',
        minimo=0.0,
        maximo=1.0,
    )
    criterios = _normalizar_criterios(
        data.get('criterios_coincidencia') if 'criterios_coincidencia' in data else None,
        coincidencia_texto=coincidencia_texto,
        coincidencia_gps=coincidencia_gps,
        coincidencia_imagen=coincidencia_imagen,
    )
    radio_gps_metros = _validar_float_opcional(
        data.get('radio_gps_metros'),
        nombre_campo='radio_gps_metros',
        minimo=1.0,
    )
    radio_final = obtener_radio_gps_referencia(radio_gps_metros)

    porcentaje_gps = _validar_float_opcional(
        data.get('porcentaje_proximidad_gps'),
        nombre_campo='porcentaje_proximidad_gps',
        minimo=0.0,
        maximo=100.0,
    )
    if porcentaje_gps is None and distancia_metros is not None:
        porcentaje_gps = calcular_porcentaje_proximidad_gps(distancia_metros, radio_final)

    metodo_imagen_raw = _leer_str_seguro(data.get('metodo_imagen'))
    metodo_imagen = metodo_imagen_raw[:40] if metodo_imagen_raw else None
    similitud_visual_coseno = _validar_float_opcional(
        data.get('similitud_visual_coseno'),
        nombre_campo='similitud_visual_coseno',
        minimo=-1.0,
        maximo=1.0,
    )
    porcentaje_similitud_visual = _validar_float_opcional(
        data.get('porcentaje_similitud_visual'),
        nombre_campo='porcentaje_similitud_visual',
        minimo=0.0,
        maximo=100.0,
    )

    info_visual = resolver_estado_metrica_visual({
        'metodo_imagen': metodo_imagen,
        'distancia_hamming': distancia_hamming,
        'similitud_imagen': similitud_imagen,
        'similitud_visual_coseno': similitud_visual_coseno,
        'porcentaje_similitud_visual': porcentaje_similitud_visual,
    })

    return {
        'similitud_texto': similitud_texto,
        'coincidencia_texto': coincidencia_texto,
        'distancia_metros': distancia_metros,
        'radio_gps_metros': radio_final,
        'porcentaje_proximidad_gps': porcentaje_gps,
        'coincidencia_gps': coincidencia_gps,
        'distancia_hamming': distancia_hamming,
        'similitud_imagen': similitud_imagen,
        'similitud_visual_coseno': info_visual['similitud_visual_coseno'],
        'porcentaje_similitud_visual': info_visual['porcentaje_similitud_visual'],
        'metodo_imagen': info_visual['metodo_imagen'],
        'coincidencia_imagen': coincidencia_imagen,
        'score_combinado': float(score_combinado if score_combinado is not None else 0.0),
        'criterios_coincidencia': criterios,
    }


def _normalizar_observacion(observacion: str | None) -> str | None:
    if observacion is None:
        return None
    if not isinstance(observacion, str):
        raise AlertaDuplicadoError('observacion debe ser texto o None.')
    limpia = observacion.strip()
    return limpia or None


def _resolver_usuario_opcional(usuario_o_id: Usuarios | int | None) -> Usuarios | None:
    if usuario_o_id is None:
        return None

    if isinstance(usuario_o_id, Usuarios):
        if usuario_o_id.pk is None:
            raise UsuarioRevisionInvalidoError(
                'El usuario indicado no está persistido en base de datos.'
            )
        usuario = Usuarios.objects.filter(pk=usuario_o_id.pk).first()
        if usuario is None:
            raise UsuarioRevisionInvalidoError(
                f'El usuario #{usuario_o_id.pk} no existe en base de datos.'
            )
        return usuario

    if isinstance(usuario_o_id, bool) or not isinstance(usuario_o_id, int) or usuario_o_id <= 0:
        raise UsuarioRevisionInvalidoError(
            'El usuario debe ser una instancia de Usuarios o un entero positivo.'
        )

    usuario = Usuarios.objects.filter(pk=usuario_o_id).first()
    if usuario is None:
        raise UsuarioRevisionInvalidoError(f'El usuario #{usuario_o_id} no existe en base de datos.')
    return usuario


# ==============================================================================
# BLOQUEOS DETERMINÍSTICOS E INTEGRACIÓN (NOTIFICACIÓN + AUDITORÍA)
# ==============================================================================

def _bloquear_par_peticiones(
    id_menor: int,
    id_mayor: int,
) -> tuple[Peticiones, Peticiones]:
    """
    Bloquea ambas peticiones en orden ascendente por PK (`select_for_update`)
    para evitar deadlocks cuando concurren solicitudes en orden inverso.
    """
    bloqueadas = list(
        Peticiones.objects.select_for_update()
        .filter(id_peticion__in=[id_menor, id_mayor])
        .order_by('id_peticion')
    )
    por_id = {p.id_peticion: p for p in bloqueadas}
    faltantes = [pid for pid in (id_menor, id_mayor) if pid not in por_id]
    if faltantes:
        detalle = ', '.join(f'#{pid}' for pid in faltantes)
        raise PeticionNoEncontradaError(
            f'No se encontraron todas las peticiones del par ({detalle}).'
        )
    return por_id[id_menor], por_id[id_mayor]


def _formatear_radicado(peticion: Peticiones) -> str:
    radicado = (peticion.numero_radicado or '').strip()
    return radicado if radicado else f'ID #{peticion.id_peticion}'


def _marcador_par(id_menor: int, id_mayor: int) -> str:
    return f'par #{id_menor}-#{id_mayor}'


def _patron_marcador_notificacion(id_menor: int, id_mayor: int) -> str:
    """Delimitador exacto para evitar colisiones de prefijo (#1-#2 vs #1-#20)."""
    return f'par #{id_menor}-#{id_mayor})'


def _construir_mensaje_notificacion(
    alerta: PeticionPosibleDuplicado,
    peticion_notificada: Peticiones,
    peticion_contraparte: Peticiones,
) -> str:
    criterios = ', '.join(alerta.criterios_coincidencia or []) or 'SIMILITUD'
    marcador = _marcador_par(alerta.peticion_menor_id, alerta.peticion_mayor_id)
    return (
        f'Posible duplicado detectado (alerta #{alerta.id_alerta}, {marcador}): '
        f'la petición {_formatear_radicado(peticion_notificada)} coincide con '
        f'{_formatear_radicado(peticion_contraparte)} '
        f'[criterios: {criterios}; score: {alerta.score_combinado:.2f}].'
    )


def _crear_notificacion_alerta_si_no_existe(
    alerta: PeticionPosibleDuplicado,
    peticion_menor: Peticiones,
    peticion_mayor: Peticiones,
    peticion_origen: Peticiones | None,
) -> NotificacionJuridico | None:
    """
    Emite una NotificacionJuridico solo cuando corresponde a una nueva alerta.

    Se ejecuta únicamente dentro de la rama ganadora de inserción protegida por
    `uq_pet_dup_par` y verifica además la ausencia previa del identificador del
    par dentro del mismo bloqueo transaccional, sin alterar el contrato existente
    de `NotificacionJuridico`.
    """
    patron_par = _patron_marcador_notificacion(
        alerta.peticion_menor_id,
        alerta.peticion_mayor_id,
    )
    ya_notificada = NotificacionJuridico.objects.filter(
        tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
        mensaje__contains=patron_par,
    ).exists()
    if ya_notificada:
        return None

    peticion_notificada = peticion_origen if peticion_origen is not None else peticion_mayor
    peticion_contraparte = (
        peticion_menor
        if peticion_notificada.id_peticion == peticion_mayor.id_peticion
        else peticion_mayor
    )

    notificacion = NotificacionJuridico.objects.create(
        id_peticion=peticion_notificada,
        tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
        mensaje=_construir_mensaje_notificacion(
            alerta,
            peticion_notificada,
            peticion_contraparte,
        ),
        leida=False,
    )
    _notificar_posible_duplicado_tiempo_real(peticion_notificada, peticion_contraparte)
    return notificacion


def _notificar_posible_duplicado_tiempo_real(peticion_notificada, peticion_contraparte) -> None:
    """
    PET-07 (contrato v1): NP individual para cada Admin/Jurídico activo (lectura individual en la
    campana central; la NotificacionJuridico compartida se mantiene intacta) + AO a rol_1/rol_2.
    Tolerante: si falla, no afecta al registro de la alerta (SAVEPOINT en el helper). WS/FCM tras el COMMIT.
    """
    try:
        from peticiones.utils import crear_notificaciones_peticion_usuarios
        from realtime import notificar as rt_notificar

        rad_a = getattr(peticion_notificada, 'numero_radicado', None) or f"#{peticion_notificada.id_peticion}"
        rad_b = getattr(peticion_contraparte, 'numero_radicado', None) or f"#{peticion_contraparte.id_peticion}"
        titulo = f"Posible duplicado: {rad_a}"
        mensaje = f"La petición {rad_a} podría ser un duplicado de {rad_b}. Revísala en Alertas de duplicados."
        destinatarios = list(Usuarios.objects.filter(id_rol_id__in=[1, 2], activo=True))
        notifs = crear_notificaciones_peticion_usuarios(
            peticion_notificada, destinatarios, 'POSIBLE_DUPLICADO', titulo, mensaje, raise_on_error=False,
        )
        rt_notificar.entregar_notificaciones_peticion(notifs)
        rt_notificar.alerta_operativa(
            'peticion.posible_duplicado', titulo, mensaje, nivel='warning', roles=(1, 2),
            referencia={
                'id_peticion': peticion_notificada.id_peticion,
                'numero_radicado': getattr(peticion_notificada, 'numero_radicado', None),
                'id_peticion_relacionada': peticion_contraparte.id_peticion,
            },
        )
    except Exception as exc:  # nunca rompe la detección de duplicados
        logger.warning('PET-07: no se pudo emitir el aviso en tiempo real: %s', type(exc).__name__)


def _registrar_auditoria_par(
    peticion_menor: Peticiones,
    peticion_mayor: Peticiones,
    *,
    tipo_cambio: str,
    usuario_accion: Usuarios | None,
    observacion: str,
    estado_anterior_por_id: Mapping[int, EstadoPeticiones | None] | None = None,
    estado_nuevo_por_id: Mapping[int, EstadoPeticiones | None] | None = None,
) -> tuple[int, int]:
    """
    Registra la auditoría en `HistorialPeticiones` para ambas peticiones del par
    respetando la estructura real de `historial_peticiones` (`tipo_cambio <= 30`),
    incluyendo transiciones de estado cuando se confirma o revierte un duplicado.
    """
    if len(tipo_cambio) > MAX_LONGITUD_TIPO_CAMBIO_HISTORIAL:
        raise ValueError(
            f'tipo_cambio "{tipo_cambio}" supera los {MAX_LONGITUD_TIPO_CAMBIO_HISTORIAL} '
            'caracteres permitidos por historial_peticiones.'
        )

    est_ant_map = estado_anterior_por_id or {}
    est_nvo_map = estado_nuevo_por_id or {}

    h_menor = HistorialPeticiones.objects.create(
        id_peticion=peticion_menor,
        tipo_cambio=tipo_cambio,
        id_estado_anterior=est_ant_map.get(peticion_menor.id_peticion),
        id_estado_nuevo=est_nvo_map.get(peticion_menor.id_peticion),
        id_veterinario_anterior=None,
        id_veterinario_nuevo=None,
        id_usuario_accion=usuario_accion,
        observacion=observacion,
    )
    h_mayor = HistorialPeticiones.objects.create(
        id_peticion=peticion_mayor,
        tipo_cambio=tipo_cambio,
        id_estado_anterior=est_ant_map.get(peticion_mayor.id_peticion),
        id_estado_nuevo=est_nvo_map.get(peticion_mayor.id_peticion),
        id_veterinario_anterior=None,
        id_veterinario_nuevo=None,
        id_usuario_accion=usuario_accion,
        observacion=observacion,
    )
    return int(h_menor.id_historial), int(h_mayor.id_historial)


def _observacion_deteccion(
    alerta: PeticionPosibleDuplicado,
    peticion_menor: Peticiones,
    peticion_mayor: Peticiones,
) -> str:
    criterios = ', '.join(alerta.criterios_coincidencia or []) or 'NINGUNO'
    marcador = _marcador_par(alerta.peticion_menor_id, alerta.peticion_mayor_id)
    origen_txt = (
        f' Origen de análisis: {_formatear_radicado(alerta.peticion_origen)}.'
        if alerta.peticion_origen_id
        else ''
    )
    return (
        f'Alerta #{alerta.id_alerta} ({alerta.estado_revision}) registrada para {marcador} '
        f'entre {_formatear_radicado(peticion_menor)} y {_formatear_radicado(peticion_mayor)}. '
        f'Criterios: {criterios}; score={alerta.score_combinado:.4f}; '
        f'origen_deteccion={alerta.origen_deteccion}.{origen_txt}'
    )


def _observacion_decision(
    alerta: PeticionPosibleDuplicado,
    peticion_menor: Peticiones,
    peticion_mayor: Peticiones,
    *,
    estado_anterior: str,
    nuevo_estado: str,
    observacion_usuario: str | None,
    usuario_revisor: Usuarios | None = None,
) -> str:
    marcador = _marcador_par(alerta.peticion_menor_id, alerta.peticion_mayor_id)
    rad_menor = _formatear_radicado(peticion_menor)
    rad_mayor = _formatear_radicado(peticion_mayor)
    actor_txt = ''
    if usuario_revisor is not None:
        nombre_actor = f"{getattr(usuario_revisor, 'nombre', '') or ''} {getattr(usuario_revisor, 'apellido', '') or ''}".strip()
        etiqueta_actor = nombre_actor or getattr(usuario_revisor, 'email', None) or f"Usuario #{usuario_revisor.pk}"
        actor_txt = f' Usuario responsable: {etiqueta_actor} (ID #{usuario_revisor.pk}).'

    if nuevo_estado == PeticionPosibleDuplicado.ESTADO_CONFIRMADO:
        pet_principal = (
            peticion_menor
            if alerta.peticion_principal_id == peticion_menor.id_peticion
            else peticion_mayor
        )
        pet_duplicada = (
            peticion_mayor
            if pet_principal.id_peticion == peticion_menor.id_peticion
            else peticion_menor
        )
        detalle = (
            f'Alerta #{alerta.id_alerta} CONFIRMADA ({estado_anterior} -> CONFIRMADO) '
            f'para {marcador} ({rad_menor} y {rad_mayor}). '
            f'Petición original conservada: {_formatear_radicado(pet_principal)} '
            f'(ID #{pet_principal.id_peticion}); petición marcada como duplicada: '
            f'{_formatear_radicado(pet_duplicada)} (ID #{pet_duplicada.id_peticion}). '
            f'Las evidencias y registros originales permanecen intactos.{actor_txt}'
        )
    elif nuevo_estado == PeticionPosibleDuplicado.ESTADO_DESCARTADO:
        detalle = (
            f'Alerta #{alerta.id_alerta} DESCARTADA ({estado_anterior} -> DESCARTADO) '
            f'para {marcador} ({rad_menor} y {rad_mayor}). '
            f'Se descarta duplicidad y ambas peticiones permanecen independientes.{actor_txt}'
        )
    else:
        detalle = (
            f'Alerta #{alerta.id_alerta} REABIERTA ({estado_anterior} -> PENDIENTE) '
            f'para {marcador} ({rad_menor} y {rad_mayor}).{actor_txt}'
        )

    if observacion_usuario:
        return f'{detalle} Observación: {observacion_usuario}'
    return detalle


def _actualizar_metricas_si_pendiente(
    alerta: PeticionPosibleDuplicado,
    *,
    metricas: Mapping[str, Any],
    origen_deteccion: str,
    peticion_origen: Peticiones | None,
) -> bool:
    """
    Actualiza únicamente métricas de detección sobre una alerta aún PENDIENTE
    cuando existan cambios efectivos. Nunca cambia `estado_revision`.
    """
    campos_cambio: list[str] = []

    campos_metrica = (
        'similitud_texto',
        'coincidencia_texto',
        'distancia_metros',
        'radio_gps_metros',
        'porcentaje_proximidad_gps',
        'coincidencia_gps',
        'distancia_hamming',
        'similitud_imagen',
        'similitud_visual_coseno',
        'porcentaje_similitud_visual',
        'metodo_imagen',
        'coincidencia_imagen',
        'score_combinado',
        'criterios_coincidencia',
    )
    for campo in campos_metrica:
        nuevo_valor = metricas[campo]
        if getattr(alerta, campo) != nuevo_valor:
            setattr(alerta, campo, nuevo_valor)
            campos_cambio.append(campo)

    if alerta.origen_deteccion != origen_deteccion:
        alerta.origen_deteccion = origen_deteccion
        campos_cambio.append('origen_deteccion')

    if alerta.peticion_origen_id is None and peticion_origen is not None:
        alerta.peticion_origen = peticion_origen
        campos_cambio.append('peticion_origen')

    if not campos_cambio:
        return False

    campos_cambio.append('fecha_actualizacion')
    alerta.save(update_fields=campos_cambio)
    return True


# ==============================================================================
# API PÚBLICA DEL SERVICIO (FASE 4B)
# ==============================================================================

def registrar_alerta_posible_duplicado(
    peticion_a: Peticiones | int,
    peticion_b: Peticiones | int,
    *,
    peticion_origen: Peticiones | int | None = None,
    evidencia_deteccion: Mapping[str, Any] | None = None,
    origen_deteccion: str = PeticionPosibleDuplicado.ORIGEN_REGISTRO,
    usuario_accion: Usuarios | int | None = None,
    notificar: bool = True,
    registrar_historial: bool = True,
    actualizar_si_pendiente: bool = True,
) -> ResultadoRegistroAlerta:
    """
    Registra de forma idempotente y segura ante concurrencia una alerta de
    posible duplicado para el par ordenado `(min(id_a, id_b), max(id_a, id_b))`.

    Reglas garantizadas:
    - Normaliza el par sin importar el orden `(a, b)` o `(b, a)`.
    - Rechaza autorreferencias (`a == b`), orígenes inválidos o métricas inválidas.
    - Bloquea ambas `Peticiones` en orden ascendente de PK (`select_for_update`)
      en una transacción breve.
    - Apoya la unicidad en la restricción `uq_pet_dup_par` mediante savepoint y
      captura de `IntegrityError` si otro proceso inserta primero el mismo par.
    - Respeta alertas previamente en estado `CONFIRMADO` o `DESCARTADO` sin
      sobrescribir su decisión, ni sus campos de revisión, ni re-notificar.
    - Emite como máximo una `NotificacionJuridico` únicamente al crearse por
      primera vez la alerta.
    - Audita la detección inicial en `HistorialPeticiones` para ambas peticiones
      sin modificar peticiones, evidencias ni datos clínicos originales.
    """
    id_menor, id_mayor = _normalizar_par_ids(peticion_a, peticion_b)

    if origen_deteccion not in ORIGENES_DETECCION_VALIDOS:
        raise ParDuplicadoInvalidoError(
            f'origen_deteccion inválido: "{origen_deteccion}". '
            f'Valores permitidos: {sorted(ORIGENES_DETECCION_VALIDOS)}.'
        )

    id_origen: int | None = None
    if peticion_origen is not None:
        id_origen = _extraer_id_peticion(peticion_origen, nombre_campo='peticion_origen')
        if id_origen not in (id_menor, id_mayor):
            raise ParDuplicadoInvalidoError(
                f'peticion_origen (#{id_origen}) debe pertenecer al par '
                f'({id_menor}, {id_mayor}).'
            )

    metricas = _normalizar_evidencia_deteccion(evidencia_deteccion)

    with transaction.atomic():
        peticion_menor, peticion_mayor = _bloquear_par_peticiones(id_menor, id_mayor)
        usuario_obj = _resolver_usuario_opcional(usuario_accion)

        peticion_origen_obj: Peticiones | None = None
        if id_origen == id_menor:
            peticion_origen_obj = peticion_menor
        elif id_origen == id_mayor:
            peticion_origen_obj = peticion_mayor

        alerta = (
            PeticionPosibleDuplicado.objects.select_for_update()
            .filter(peticion_menor_id=id_menor, peticion_mayor_id=id_mayor)
            .first()
        )
        creada = False

        if alerta is None:
            try:
                with transaction.atomic():
                    alerta = PeticionPosibleDuplicado.objects.create(
                        peticion_menor=peticion_menor,
                        peticion_mayor=peticion_mayor,
                        peticion_origen=peticion_origen_obj,
                        estado_revision=PeticionPosibleDuplicado.ESTADO_PENDIENTE,
                        peticion_principal=None,
                        peticion_duplicada=None,
                        similitud_texto=metricas['similitud_texto'],
                        coincidencia_texto=metricas['coincidencia_texto'],
                        distancia_metros=metricas['distancia_metros'],
                        radio_gps_metros=metricas['radio_gps_metros'],
                        porcentaje_proximidad_gps=metricas['porcentaje_proximidad_gps'],
                        coincidencia_gps=metricas['coincidencia_gps'],
                        distancia_hamming=metricas['distancia_hamming'],
                        similitud_imagen=metricas['similitud_imagen'],
                        similitud_visual_coseno=metricas['similitud_visual_coseno'],
                        porcentaje_similitud_visual=metricas['porcentaje_similitud_visual'],
                        metodo_imagen=metricas['metodo_imagen'],
                        coincidencia_imagen=metricas['coincidencia_imagen'],
                        score_combinado=metricas['score_combinado'],
                        criterios_coincidencia=metricas['criterios_coincidencia'],
                        origen_deteccion=origen_deteccion,
                    )
                    creada = True
            except IntegrityError:
                # Otro hilo/transacción insertó el par (id_menor, id_mayor)
                # primero y disparó `uq_pet_dup_par`. El savepoint revirtió solo
                # este INSERT fallido; recuperamos el registro ganador bloqueado.
                alerta = (
                    PeticionPosibleDuplicado.objects.select_for_update()
                    .filter(peticion_menor_id=id_menor, peticion_mayor_id=id_mayor)
                    .first()
                )
                if alerta is None:
                    raise
                creada = False

        if creada:
            notif_id: int | None = None
            if notificar:
                notif = _crear_notificacion_alerta_si_no_existe(
                    alerta,
                    peticion_menor,
                    peticion_mayor,
                    peticion_origen_obj,
                )
                if notif is not None:
                    notif_id = int(notif.id_notificacion)

            hist_ids: tuple[int, ...] = ()
            if registrar_historial:
                hist_ids = _registrar_auditoria_par(
                    peticion_menor,
                    peticion_mayor,
                    tipo_cambio=HISTORIAL_TIPO_DUPLICADO_DETECTADO,
                    usuario_accion=usuario_obj,
                    observacion=_observacion_deteccion(
                        alerta,
                        peticion_menor,
                        peticion_mayor,
                    ),
                )

            return ResultadoRegistroAlerta(
                alerta=alerta,
                creada=True,
                actualizada=False,
                notificada=notif_id is not None,
                respetada_decision_previa=False,
                notificacion_id=notif_id,
                historial_ids=hist_ids,
            )

        # Alerta ya existente
        if alerta.estado_revision in ESTADOS_ALERTA_RESUELTOS:
            return ResultadoRegistroAlerta(
                alerta=alerta,
                creada=False,
                actualizada=False,
                notificada=False,
                respetada_decision_previa=True,
                notificacion_id=None,
                historial_ids=(),
            )

        actualizada = False
        if actualizar_si_pendiente:
            actualizada = _actualizar_metricas_si_pendiente(
                alerta,
                metricas=metricas,
                origen_deteccion=origen_deteccion,
                peticion_origen=peticion_origen_obj,
            )

        return ResultadoRegistroAlerta(
            alerta=alerta,
            creada=False,
            actualizada=actualizada,
            notificada=False,
            respetada_decision_previa=False,
            notificacion_id=None,
            historial_ids=(),
        )


def registrar_alertas_desde_deteccion(
    peticion_origen: Peticiones | int,
    candidatos: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    *,
    solo_posibles_duplicados: bool = True,
    origen_deteccion: str = PeticionPosibleDuplicado.ORIGEN_REGISTRO,
    usuario_accion: Usuarios | int | None = None,
    notificar: bool = True,
    registrar_historial: bool = True,
    actualizar_si_pendiente: bool = True,
    ignorar_candidatos_inexistentes: bool = True,
) -> ResultadoBatchAlertas:
    """
    Persiste alertas idempotentes a partir de la salida del detector de duplicados
    (`duplicados_service.detectar_duplicados_desde_solicitud` o lista de candidatos).

    Ejecuta cada par en una transacción breve independiente en orden determinístico
    de `(id_menor, id_mayor)` para minimizar bloqueos y evitar contención.
    """
    id_origen = _extraer_id_peticion(peticion_origen, nombre_campo='peticion_origen')

    if origen_deteccion not in ORIGENES_DETECCION_VALIDOS:
        raise ParDuplicadoInvalidoError(
            f'origen_deteccion inválido: "{origen_deteccion}". '
            f'Valores permitidos: {sorted(ORIGENES_DETECCION_VALIDOS)}.'
        )

    if not Peticiones.objects.filter(id_peticion=id_origen).exists():
        raise PeticionNoEncontradaError(f'No existe la petición origen #{id_origen}.')

    if isinstance(candidatos, Mapping):
        if 'posibles_duplicados' in candidatos:
            lista_bruta = candidatos.get('posibles_duplicados') or []
        elif 'resultados' in candidatos:
            lista_bruta = candidatos.get('resultados') or []
        else:
            raise ParDuplicadoInvalidoError(
                'El diccionario de detección debe contener "posibles_duplicados" o "resultados".'
            )
    elif isinstance(candidatos, Sequence) and not isinstance(candidatos, (str, bytes)):
        lista_bruta = candidatos
    else:
        raise ParDuplicadoInvalidoError(
            'candidatos debe ser una secuencia de candidatos o un resultado de detección.'
        )

    candidatos_por_id: dict[int, Mapping[str, Any]] = {}
    omitidos: list[dict[str, Any]] = []

    for item in lista_bruta:
        if not isinstance(item, Mapping):
            raise ParDuplicadoInvalidoError('Cada candidato debe ser un mapeo o diccionario.')

        raw_id = item.get('id_peticion')
        try:
            id_cand = _extraer_id_peticion(raw_id, nombre_campo='candidato.id_peticion')
        except ParDuplicadoInvalidoError as exc:
            omitidos.append({'id_peticion': raw_id, 'motivo': str(exc)})
            continue

        if id_cand == id_origen:
            omitidos.append({'id_peticion': id_cand, 'motivo': 'Autorreferencia ignorada.'})
            continue

        if solo_posibles_duplicados and item.get('posible_duplicado') is False:
            omitidos.append({
                'id_peticion': id_cand,
                'motivo': 'Descartado por bandera posible_duplicado=False.',
            })
            continue

        previo = candidatos_por_id.get(id_cand)
        score_nuevo = float(item.get('score_combinado') or 0.0)
        score_previo = float(previo.get('score_combinado') or 0.0) if previo is not None else -1.0
        if previo is None or score_nuevo > score_previo:
            candidatos_por_id[id_cand] = item

    pares_ordenados = sorted(
        candidatos_por_id.items(),
        key=lambda par: (min(id_origen, par[0]), max(id_origen, par[0])),
    )

    resultados: list[ResultadoRegistroAlerta] = []
    for id_cand, evidencia in pares_ordenados:
        try:
            res = registrar_alerta_posible_duplicado(
                id_origen,
                id_cand,
                peticion_origen=id_origen,
                evidencia_deteccion=evidencia,
                origen_deteccion=origen_deteccion,
                usuario_accion=usuario_accion,
                notificar=notificar,
                registrar_historial=registrar_historial,
                actualizar_si_pendiente=actualizar_si_pendiente,
            )
            resultados.append(res)
        except PeticionNoEncontradaError as exc:
            if (
                ignorar_candidatos_inexistentes
                and Peticiones.objects.filter(id_peticion=id_origen).exists()
            ):
                logger.warning(
                    'Se omite candidato inexistente #%s al registrar alertas para petición #%s: %s',
                    id_cand,
                    id_origen,
                    exc,
                )
                omitidos.append({'id_peticion': id_cand, 'motivo': str(exc)})
                continue
            raise

    creadas = sum(1 for r in resultados if r.creada)
    actualizadas = sum(1 for r in resultados if r.actualizada)
    respetadas = sum(1 for r in resultados if r.respetada_decision_previa)
    sin_cambios = sum(
        1
        for r in resultados
        if not r.creada and not r.actualizada and not r.respetada_decision_previa
    )
    notif_total = sum(1 for r in resultados if r.notificada)

    return ResultadoBatchAlertas(
        resultados=tuple(resultados),
        total_procesados=len(resultados),
        creadas=creadas,
        actualizadas=actualizadas,
        existentes_sin_cambios=sin_cambios,
        respetadas_decision_previa=respetadas,
        notificaciones_creadas=notif_total,
        omitidos=tuple(omitidos),
    )


def gestionar_decision_alerta(
    id_alerta: PeticionPosibleDuplicado | int,
    nuevo_estado: str,
    *,
    peticion_principal: Peticiones | int | None = None,
    usuario_revisor: Usuarios | int | None = None,
    observacion: str | None = None,
    permitir_cambio_decision_previa: bool = False,
    registrar_historial: bool = True,
) -> ResultadoDecisionAlerta:
    """
    Gestiona las decisiones `PENDIENTE`, `CONFIRMADO` y `DESCARTADO` sobre una
    alerta existente con bloqueo determinístico, respeto a decisiones previas y
    auditoría en `HistorialPeticiones`.

    Nunca elimina ni modifica las peticiones, evidencias ni historias clínicas
    originales.
    """
    if nuevo_estado not in ESTADOS_ALERTA_VALIDOS:
        raise TransicionAlertaInvalidaError(
            f'Estado de revisión inválido: "{nuevo_estado}". '
            f'Valores permitidos: {sorted(ESTADOS_ALERTA_VALIDOS)}.'
        )

    if isinstance(id_alerta, PeticionPosibleDuplicado):
        if id_alerta.pk is None:
            raise AlertaNoEncontradaError('La alerta indicada no está persistida.')
        alerta_pk = int(id_alerta.pk)
    elif isinstance(id_alerta, bool) or not isinstance(id_alerta, int) or id_alerta <= 0:
        raise AlertaNoEncontradaError(
            'id_alerta debe ser un entero positivo o instancia persistida.'
        )
    else:
        alerta_pk = int(id_alerta)

    obs_limpia = _normalizar_observacion(observacion)

    id_principal: int | None = None
    if nuevo_estado == PeticionPosibleDuplicado.ESTADO_CONFIRMADO:
        if peticion_principal is None:
            raise SeleccionPrincipalInvalidaError(
                'Al confirmar un duplicado debe especificarse peticion_principal.'
            )
        id_principal = _extraer_id_peticion(
            peticion_principal,
            nombre_campo='peticion_principal',
            error_cls=SeleccionPrincipalInvalidaError,
        )
    elif peticion_principal is not None:
        raise SeleccionPrincipalInvalidaError(
            f'No se debe especificar peticion_principal cuando el estado es {nuevo_estado}.'
        )

    par_meta = (
        PeticionPosibleDuplicado.objects.filter(id_alerta=alerta_pk)
        .values_list('peticion_menor_id', 'peticion_mayor_id')
        .first()
    )
    if par_meta is None:
        raise AlertaNoEncontradaError(f'No existe la alerta de duplicado #{alerta_pk}.')

    id_menor_ref, id_mayor_ref = par_meta

    with transaction.atomic():
        # Mantener la misma jerarquía global de bloqueos que el registro de alertas:
        # primero ambas Peticiones en orden ascendente, luego PeticionPosibleDuplicado.
        peticion_menor, peticion_mayor = _bloquear_par_peticiones(id_menor_ref, id_mayor_ref)

        alerta = (
            PeticionPosibleDuplicado.objects.select_for_update()
            .filter(id_alerta=alerta_pk)
            .first()
        )
        if alerta is None:
            raise AlertaNoEncontradaError(f'No existe la alerta de duplicado #{alerta_pk}.')

        if id_principal is not None and id_principal not in (
            alerta.peticion_menor_id,
            alerta.peticion_mayor_id,
        ):
            raise SeleccionPrincipalInvalidaError(
                f'peticion_principal (#{id_principal}) debe ser una de las peticiones '
                f'del par ({alerta.peticion_menor_id}, {alerta.peticion_mayor_id}).'
            )

        revisor_obj = _resolver_usuario_opcional(usuario_revisor)
        estado_anterior = alerta.estado_revision
        id_duplicada_anterior = alerta.peticion_duplicada_id

        # Impedir decisiones repetidas, confirmaciones simultáneas y sobrescrituras no autorizadas
        if estado_anterior == PeticionPosibleDuplicado.ESTADO_PENDIENTE and nuevo_estado == PeticionPosibleDuplicado.ESTADO_PENDIENTE:
            return ResultadoDecisionAlerta(
                alerta=alerta,
                cambiada=False,
                estado_anterior=estado_anterior,
                estado_nuevo=nuevo_estado,
                historial_ids=(),
            )

        if estado_anterior in ESTADOS_ALERTA_RESUELTOS and not permitir_cambio_decision_previa:
            if estado_anterior == PeticionPosibleDuplicado.ESTADO_CONFIRMADO:
                raise TransicionAlertaInvalidaError(
                    f'La alerta #{alerta.id_alerta} ya fue confirmada previamente con petición original '
                    f'#{alerta.peticion_principal_id}. No se permiten decisiones repetidas ni cambios concurrentes.'
                )
            raise TransicionAlertaInvalidaError(
                f'La alerta #{alerta.id_alerta} ya fue resuelta previamente en estado "{estado_anterior}" '
                'y no admite decisiones repetidas ni transiciones automáticas.'
            )

        id_duplicada: int | None = None
        estado_anterior_por_id: dict[int, EstadoPeticiones | None] = {}
        estado_nuevo_por_id: dict[int, EstadoPeticiones | None] = {}

        if nuevo_estado == PeticionPosibleDuplicado.ESTADO_CONFIRMADO:
            id_duplicada = (
                alerta.peticion_mayor_id
                if id_principal == alerta.peticion_menor_id
                else alerta.peticion_menor_id
            )
            pet_principal = peticion_menor if id_principal == peticion_menor.id_peticion else peticion_mayor
            pet_duplicada = peticion_mayor if id_duplicada == peticion_mayor.id_peticion else peticion_menor

            # Verificar integridad cruzada contra otras alertas confirmadas antes de aplicar
            ya_duplicada_otra = (
                PeticionPosibleDuplicado.objects.filter(
                    estado_revision=PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
                    peticion_duplicada_id=id_principal,
                )
                .exclude(id_alerta=alerta.id_alerta)
                .exists()
            )
            estado_princ_actual = EstadoPeticiones.objects.filter(pk=pet_principal.id_estado_id).first()
            estado_princ_norm = normalizar_texto_juridico(getattr(estado_princ_actual, 'nombre', ''))
            if ya_duplicada_otra or estado_princ_norm in ESTADOS_DUPLICADOS_NORM:
                raise TransicionAlertaInvalidaError(
                    f'La petición #{id_principal} ya fue marcada como duplicada en otra resolución '
                    'y no puede elegirse como petición original.'
                )

            dup_es_original_en_otra = (
                PeticionPosibleDuplicado.objects.filter(
                    estado_revision=PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
                    peticion_principal_id=id_duplicada,
                )
                .exclude(id_alerta=alerta.id_alerta)
                .exists()
            )
            if dup_es_original_en_otra:
                raise TransicionAlertaInvalidaError(
                    f'La petición #{id_duplicada} ya fue confirmada como original en otra alerta '
                    'y no puede marcarse como duplicada.'
                )

            estado_dup_anterior = EstadoPeticiones.objects.filter(pk=pet_duplicada.id_estado_id).first()
            estado_duplicada_obj = obtener_o_crear_estado_duplicada(permitir_crear=True)
            if estado_duplicada_obj is not None and pet_duplicada.id_estado_id != estado_duplicada_obj.id_estado:
                pet_duplicada.id_estado = estado_duplicada_obj
                pet_duplicada.save(update_fields=['id_estado'])

            estado_anterior_por_id = {
                pet_principal.id_peticion: estado_princ_actual,
                pet_duplicada.id_peticion: estado_dup_anterior,
            }
            estado_nuevo_por_id = {
                pet_principal.id_peticion: estado_princ_actual,
                pet_duplicada.id_peticion: estado_duplicada_obj or estado_dup_anterior,
            }
        elif id_duplicada_anterior is not None and permitir_cambio_decision_previa:
            # Si se revierte explícitamente una confirmación previa, reactivar la petición solo si
            # no está marcada como duplicada por otra alerta confirmada vigente.
            sigue_siendo_duplicada = (
                PeticionPosibleDuplicado.objects.filter(
                    estado_revision=PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
                    peticion_duplicada_id=id_duplicada_anterior,
                )
                .exclude(id_alerta=alerta.id_alerta)
                .exists()
            )
            if not sigue_siendo_duplicada:
                pet_antes_dup = (
                    peticion_menor if id_duplicada_anterior == peticion_menor.id_peticion else peticion_mayor
                )
                est_act = EstadoPeticiones.objects.filter(pk=pet_antes_dup.id_estado_id).first()
                if normalizar_texto_juridico(getattr(est_act, 'nombre', '')) in ESTADOS_DUPLICADOS_NORM:
                    estado_reactivado = _obtener_estado_reactivacion()
                    if estado_reactivado is not None:
                        pet_antes_dup.id_estado = estado_reactivado
                        pet_antes_dup.save(update_fields=['id_estado'])

        alerta.estado_revision = nuevo_estado
        alerta.peticion_principal_id = id_principal
        alerta.peticion_duplicada_id = id_duplicada
        alerta.revisado_por = revisor_obj
        alerta.observacion_revision = obs_limpia
        alerta.fecha_revision = _ahora_local()
        alerta.save(
            update_fields=[
                'estado_revision',
                'peticion_principal',
                'peticion_duplicada',
                'revisado_por',
                'observacion_revision',
                'fecha_revision',
                'fecha_actualizacion',
            ]
        )

        hist_ids: tuple[int, ...] = ()
        if registrar_historial:
            tipo_cambio_map = {
                PeticionPosibleDuplicado.ESTADO_CONFIRMADO: HISTORIAL_TIPO_DUPLICADO_CONFIRMADO,
                PeticionPosibleDuplicado.ESTADO_DESCARTADO: HISTORIAL_TIPO_DUPLICADO_DESCARTADO,
                PeticionPosibleDuplicado.ESTADO_PENDIENTE: HISTORIAL_TIPO_DUPLICADO_REABIERTO,
            }
            hist_ids = _registrar_auditoria_par(
                peticion_menor,
                peticion_mayor,
                tipo_cambio=tipo_cambio_map[nuevo_estado],
                usuario_accion=revisor_obj,
                observacion=_observacion_decision(
                    alerta,
                    peticion_menor,
                    peticion_mayor,
                    estado_anterior=estado_anterior,
                    nuevo_estado=nuevo_estado,
                    observacion_usuario=obs_limpia,
                    usuario_revisor=revisor_obj,
                ),
                estado_anterior_por_id=estado_anterior_por_id,
                estado_nuevo_por_id=estado_nuevo_por_id,
            )

        return ResultadoDecisionAlerta(
            alerta=alerta,
            cambiada=True,
            estado_anterior=estado_anterior,
            estado_nuevo=nuevo_estado,
            historial_ids=hist_ids,
        )


def confirmar_duplicado(
    id_alerta: PeticionPosibleDuplicado | int,
    peticion_principal: Peticiones | int,
    *,
    usuario_revisor: Usuarios | int | None = None,
    observacion: str | None = None,
    permitir_cambio_decision_previa: bool = False,
    registrar_historial: bool = True,
) -> ResultadoDecisionAlerta:
    """Marca una alerta como CONFIRMADO eligiendo qué petición del par es la principal."""
    return gestionar_decision_alerta(
        id_alerta,
        PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
        peticion_principal=peticion_principal,
        usuario_revisor=usuario_revisor,
        observacion=observacion,
        permitir_cambio_decision_previa=permitir_cambio_decision_previa,
        registrar_historial=registrar_historial,
    )


def descartar_duplicado(
    id_alerta: PeticionPosibleDuplicado | int,
    *,
    usuario_revisor: Usuarios | int | None = None,
    observacion: str | None = None,
    permitir_cambio_decision_previa: bool = False,
    registrar_historial: bool = True,
) -> ResultadoDecisionAlerta:
    """Marca una alerta como DESCARTADO sin petición principal."""
    return gestionar_decision_alerta(
        id_alerta,
        PeticionPosibleDuplicado.ESTADO_DESCARTADO,
        peticion_principal=None,
        usuario_revisor=usuario_revisor,
        observacion=observacion,
        permitir_cambio_decision_previa=permitir_cambio_decision_previa,
        registrar_historial=registrar_historial,
    )


def reabrir_alerta_pendiente(
    id_alerta: PeticionPosibleDuplicado | int,
    *,
    usuario_revisor: Usuarios | int | None = None,
    observacion: str | None = None,
    registrar_historial: bool = True,
) -> ResultadoDecisionAlerta:
    """Reabre explícitamente una alerta resuelta a estado PENDIENTE para nueva revisión."""
    return gestionar_decision_alerta(
        id_alerta,
        PeticionPosibleDuplicado.ESTADO_PENDIENTE,
        peticion_principal=None,
        usuario_revisor=usuario_revisor,
        observacion=observacion,
        permitir_cambio_decision_previa=True,
        registrar_historial=registrar_historial,
    )


def _resolver_distancia_gps_alerta(alerta: PeticionPosibleDuplicado) -> float | None:
    """Obtiene la distancia GPS real en metros entre ambas peticiones sin modificar la alerta."""
    distancia = _leer_float_seguro(getattr(alerta, 'distancia_metros', None))
    if distancia is not None and distancia >= 0.0:
        return round(distancia, 2)

    ubic_menor = getattr(getattr(alerta, 'peticion_menor', None), 'id_ubicacion', None)
    ubic_mayor = getattr(getattr(alerta, 'peticion_mayor', None), 'id_ubicacion', None)
    if ubic_menor is None or ubic_mayor is None:
        return None

    coords_menor = normalizar_coordenadas(
        getattr(ubic_menor, 'latitud', None),
        getattr(ubic_menor, 'longitud', None),
    )
    coords_mayor = normalizar_coordenadas(
        getattr(ubic_mayor, 'latitud', None),
        getattr(ubic_mayor, 'longitud', None),
    )
    if coords_menor is None or coords_mayor is None:
        return None

    return round(
        float(
            calcular_distancia_haversine_metros(
                coords_menor[0],
                coords_menor[1],
                coords_mayor[0],
                coords_mayor[1],
            )
        ),
        2,
    )


def calcular_comparacion_visual_dinov2_alerta(
    id_alerta: PeticionPosibleDuplicado | int,
    *,
    usuario: Usuarios | int | None = None,
) -> dict[str, Any]:
    """
    Calcula bajo demanda la similitud visual real con DINOv2 Small entre las
    fotografías de ambas peticiones de una alerta, reutilizando embeddings cacheados
    cuando existan y consultando VetericanoIA cuando haga falta.
    No modifica ni resuelve automáticamente la alerta en base de datos.
    """
    if isinstance(id_alerta, PeticionPosibleDuplicado):
        if id_alerta.pk is None:
            raise AlertaNoEncontradaError('La alerta indicada no está persistida.')
        alerta_pk = int(id_alerta.pk)
    elif isinstance(id_alerta, bool) or not isinstance(id_alerta, int) or id_alerta <= 0:
        raise AlertaNoEncontradaError('id_alerta debe ser un entero positivo.')
    else:
        alerta_pk = int(id_alerta)

    if usuario is not None:
        usuario_res = _resolver_usuario_opcional(usuario)
        if not es_admin_o_juridico(usuario_res):
            raise UsuarioRevisionInvalidoError(
                'Solo los roles Administrador y Jurídico autorizados pueden ejecutar la comparación visual.'
            )

    alerta = (
        PeticionPosibleDuplicado.objects.select_related(
            'peticion_menor',
            'peticion_menor__id_tipo',
            'peticion_menor__id_estado',
            'peticion_menor__id_ubicacion',
            'peticion_menor__asignado_a',
            'peticion_menor__asignado_por',
            'peticion_mayor',
            'peticion_mayor__id_tipo',
            'peticion_mayor__id_estado',
            'peticion_mayor__id_ubicacion',
            'peticion_mayor__asignado_a',
            'peticion_mayor__asignado_por',
            'peticion_origen',
            'peticion_principal',
            'peticion_duplicada',
            'revisado_por',
        )
        .prefetch_related(
            'peticion_menor__evidenciapeticiones_set',
            'peticion_mayor__evidenciapeticiones_set',
        )
        .filter(id_alerta=alerta_pk)
        .first()
    )
    if alerta is None:
        raise AlertaNoEncontradaError(f'No existe la alerta de duplicado #{alerta_pk}.')

    distancia_gps_metros = _resolver_distancia_gps_alerta(alerta)
    radio_ref = obtener_radio_gps_referencia(getattr(alerta, 'radio_gps_metros', None))
    porcentaje_gps = (
        alerta.porcentaje_proximidad_gps
        if alerta.porcentaje_proximidad_gps is not None
        else calcular_porcentaje_proximidad_gps(distancia_gps_metros, radio_ref)
    )

    _, raw_fotos_menor = extraer_fotografias_peticion(alerta.peticion_menor)
    _, raw_fotos_mayor = extraer_fotografias_peticion(alerta.peticion_mayor)
    fotos_menor = [u for u in raw_fotos_menor if detectar_tipo_archivo(u) == 'imagen']
    fotos_mayor = [u for u in raw_fotos_mayor if detectar_tipo_archivo(u) == 'imagen']

    if not fotos_menor or not fotos_mayor:
        peticiones_sin_fotos = [
            pet.id_peticion
            for pet, imgs in (
                (alerta.peticion_menor, fotos_menor),
                (alerta.peticion_mayor, fotos_mayor),
            )
            if not imgs and getattr(pet, 'id_peticion', None) is not None
        ]
        return {
            'id_alerta': alerta.id_alerta,
            'alerta': alerta,
            'comparado': False,
            'dinov2_calculada': False,
            'visual_calculada': False,
            'codigo': 'SIN_FOTOGRAFIAS',
            'motivo': 'SIN_FOTOGRAFIAS_EN_AMBAS_PETICIONES',
            'mensaje': 'Ambas peticiones deben contar con al menos una fotografía válida para calcular DINOv2.',
            'similitud_imagen_porcentaje': None,
            'porcentaje_similitud_visual': None,
            'similitud_imagen': None,
            'similitud_visual_coseno': None,
            'coincidencia_imagen': False,
            'metodo_imagen': None,
            'distancia_gps_metros': distancia_gps_metros,
            'radio_gps_metros': round(radio_ref, 2),
            'porcentaje_proximidad_gps': porcentaje_gps,
            'peticiones_sin_fotos': peticiones_sin_fotos,
            'estado_revision': alerta.estado_revision,
            'alerta_modificada': False,
        }

    cfg = obtener_config_duplicados()
    cache_emb: dict[str, list[float] | None] = {}
    precargar_fn = getattr(dinov2_service, 'precargar_embeddings_bd', None)
    if callable(precargar_fn):
        max_img = cfg['max_imagenes_por_peticion']
        precargar_fn(
            list(fotos_menor[:max_img]) + list(fotos_mayor[:max_img]),
            cache=cache_emb,
            id_peticiones=[
                pid
                for pid in (alerta.peticion_menor_id, alerta.peticion_mayor_id)
                if pid is not None
            ],
            usar_cache_bd=True,
        )
    try:
        emb_menor = dinov2_service.calcular_embeddings_urls(
            fotos_menor,
            max_imagenes=cfg['max_imagenes_por_peticion'],
            cache=cache_emb,
            id_peticion=alerta.peticion_menor_id,
            usar_cache_bd=True,
        )
        emb_mayor = dinov2_service.calcular_embeddings_urls(
            fotos_mayor,
            max_imagenes=cfg['max_imagenes_por_peticion'],
            cache=cache_emb,
            id_peticion=alerta.peticion_mayor_id,
            usar_cache_bd=True,
        )
    except (
        dinov2_service.ModelDinov2NoDisponibleError,
        requests.RequestException,
        TimeoutError,
        ConnectionError,
    ) as exc:
        raise Dinov2NoDisponibleParaAlertaError(
            f'El servicio DINOv2 no está disponible para calcular la comparación visual: {exc}',
            codigo='DINOV2_NO_DISPONIBLE',
            distancia_gps_metros=distancia_gps_metros,
        ) from exc

    if not emb_menor or not emb_mayor:
        return {
            'id_alerta': alerta.id_alerta,
            'alerta': alerta,
            'comparado': False,
            'dinov2_calculada': False,
            'visual_calculada': False,
            'codigo': 'IMAGENES_NO_PROCESABLES',
            'motivo': 'IMAGENES_NO_PROCESABLES',
            'mensaje': 'No fue posible extraer embeddings DINOv2 de las imágenes adjuntas.',
            'similitud_imagen_porcentaje': None,
            'porcentaje_similitud_visual': None,
            'similitud_imagen': None,
            'similitud_visual_coseno': None,
            'coincidencia_imagen': False,
            'metodo_imagen': None,
            'distancia_gps_metros': distancia_gps_metros,
            'radio_gps_metros': round(radio_ref, 2),
            'porcentaje_proximidad_gps': porcentaje_gps,
            'estado_revision': alerta.estado_revision,
            'alerta_modificada': False,
        }

    comp = dinov2_service.comparar_conjuntos_embeddings(
        emb_menor,
        emb_mayor,
        umbral_coseno=cfg['umbral_dinov2'],
    )
    if not comp.get('comparado'):
        return {
            'id_alerta': alerta.id_alerta,
            'alerta': alerta,
            'comparado': False,
            'dinov2_calculada': False,
            'visual_calculada': False,
            'codigo': 'SIN_COMPARACION_VALIDA',
            'motivo': 'SIN_COMPARACION_VALIDA',
            'mensaje': 'No se obtuvo una comparación visual DINOv2 válida.',
            'similitud_imagen_porcentaje': None,
            'porcentaje_similitud_visual': None,
            'similitud_imagen': None,
            'similitud_visual_coseno': None,
            'coincidencia_imagen': False,
            'metodo_imagen': None,
            'distancia_gps_metros': distancia_gps_metros,
            'radio_gps_metros': round(radio_ref, 2),
            'porcentaje_proximidad_gps': porcentaje_gps,
            'estado_revision': alerta.estado_revision,
            'alerta_modificada': False,
        }

    similitud_pct = float(comp['porcentaje_similitud_visual'])
    similitud_img = float(comp['similitud_imagen'])
    similitud_cos = float(comp['similitud_coseno'])
    coincidencia_img = bool(comp['coincidencia_imagen'])
    metodo_img = comp.get('metodo_imagen') or METODO_IMAGEN_DINOV2

    return {
        'id_alerta': alerta.id_alerta,
        'alerta': alerta,
        'comparado': True,
        'dinov2_calculada': True,
        'visual_calculada': True,
        'codigo': 'OK',
        'motivo': None,
        'mensaje': 'Similitud visual DINOv2 calculada correctamente.',
        'similitud_imagen_porcentaje': similitud_pct,
        'porcentaje_similitud_visual': similitud_pct,
        'similitud_imagen': similitud_img,
        'similitud_visual_coseno': similitud_cos,
        'coincidencia_imagen': coincidencia_img,
        'metodo_imagen': metodo_img,
        'distancia_gps_metros': distancia_gps_metros,
        'radio_gps_metros': round(radio_ref, 2),
        'porcentaje_proximidad_gps': porcentaje_gps,
        'estado_revision': alerta.estado_revision,
        'alerta_modificada': False,
    }


# ==============================================================================
# CONSULTAS DE LECTURA (SOPORTE PARA FUTURA FASE 4C)
# ==============================================================================

def obtener_alerta_por_id(id_alerta: int) -> PeticionPosibleDuplicado:
    """Obtiene una alerta por ID con sus relaciones principales pre-cargadas."""
    if isinstance(id_alerta, bool) or not isinstance(id_alerta, int) or id_alerta <= 0:
        raise AlertaNoEncontradaError('id_alerta debe ser un entero positivo.')

    alerta = (
        PeticionPosibleDuplicado.objects.select_related(
            'peticion_menor',
            'peticion_mayor',
            'peticion_origen',
            'peticion_principal',
            'peticion_duplicada',
            'revisado_por',
        )
        .filter(id_alerta=id_alerta)
        .first()
    )
    if alerta is None:
        raise AlertaNoEncontradaError(f'No existe la alerta de duplicado #{id_alerta}.')
    return alerta


def obtener_alerta_por_par(
    peticion_a: Peticiones | int,
    peticion_b: Peticiones | int,
) -> PeticionPosibleDuplicado | None:
    """Obtiene la alerta correspondiente a un par sin importar el orden `(a, b)` o `(b, a)`."""
    id_menor, id_mayor = _normalizar_par_ids(peticion_a, peticion_b)
    return (
        PeticionPosibleDuplicado.objects.select_related(
            'peticion_menor',
            'peticion_mayor',
            'peticion_origen',
            'peticion_principal',
            'peticion_duplicada',
            'revisado_por',
        )
        .filter(peticion_menor_id=id_menor, peticion_mayor_id=id_mayor)
        .first()
    )


def listar_alertas_duplicados(
    *,
    estado_revision: str | None = None,
    id_peticion: int | None = None,
    solo_pendientes: bool = False,
) -> QuerySet[PeticionPosibleDuplicado]:
    """Retorna el queryset de alertas filtrado de forma segura y ordenada por prioridad."""
    qs = PeticionPosibleDuplicado.objects.select_related(
        'peticion_menor',
        'peticion_mayor',
        'peticion_origen',
        'peticion_principal',
        'peticion_duplicada',
        'revisado_por',
    )

    if solo_pendientes:
        qs = qs.filter(estado_revision=PeticionPosibleDuplicado.ESTADO_PENDIENTE)
    elif estado_revision is not None:
        if estado_revision not in ESTADOS_ALERTA_VALIDOS:
            raise TransicionAlertaInvalidaError(
                f'estado_revision inválido: "{estado_revision}".'
            )
        qs = qs.filter(estado_revision=estado_revision)

    if id_peticion is not None:
        pid = _extraer_id_peticion(id_peticion, nombre_campo='id_peticion')
        qs = qs.filter(Q(peticion_menor_id=pid) | Q(peticion_mayor_id=pid))

    # Pendientes de revisión primero; luego mayor similitud y lo más reciente.
    return qs.order_by(
        orden_valor_primero('estado_revision', PeticionPosibleDuplicado.ESTADO_PENDIENTE),
        '-score_combinado', '-fecha_creacion', '-id_alerta',
    )


def obtener_resumen_alertas_duplicados(*, id_peticion: int | None = None) -> dict[str, int]:
    """Devuelve conteos agregados por estado sin cargar registros en memoria."""
    qs = PeticionPosibleDuplicado.objects.all()
    if id_peticion is not None:
        pid = _extraer_id_peticion(id_peticion, nombre_campo='id_peticion')
        qs = qs.filter(Q(peticion_menor_id=pid) | Q(peticion_mayor_id=pid))

    agregados = qs.values('estado_revision').annotate(total=Count('id_alerta'))
    resumen = {
        PeticionPosibleDuplicado.ESTADO_PENDIENTE: 0,
        PeticionPosibleDuplicado.ESTADO_CONFIRMADO: 0,
        PeticionPosibleDuplicado.ESTADO_DESCARTADO: 0,
    }
    for fila in agregados:
        estado = fila['estado_revision']
        if estado in resumen:
            resumen[estado] = int(fila['total'])

    resumen['total'] = sum(resumen.values())
    return resumen


# ==============================================================================
# DETECCIÓN AUTOMÁTICA AL REGISTRAR UNA PETICIÓN (FASE 4D)
# ==============================================================================

def _obtener_config_deteccion_automatica() -> dict[str, Any]:
    """
    Configuración operativa de la detección automática al radicar peticiones.
    Permite deshabilitarla o ajustar límites desde `django.conf.settings`.
    """
    activo = bool(getattr(settings, 'DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA', True))

    raw_timeout = getattr(
        settings,
        'DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS',
        AUTO_DETECCION_TIMEOUT_MS_DEFAULT,
    )
    timeout_ms: int | None
    if raw_timeout in (None, False, 0, '0'):
        timeout_ms = None
    else:
        try:
            timeout_int = int(raw_timeout)
            if timeout_int <= 0:
                timeout_ms = None
            else:
                timeout_ms = max(
                    AUTO_DETECCION_TIMEOUT_MS_MIN,
                    min(timeout_int, AUTO_DETECCION_TIMEOUT_MS_MAX),
                )
        except (TypeError, ValueError):
            timeout_ms = AUTO_DETECCION_TIMEOUT_MS_DEFAULT

    raw_max_cand = getattr(settings, 'DUPLICADOS_AUTO_MAX_CANDIDATOS', None)
    max_candidatos: int | None = None
    if raw_max_cand is not None:
        try:
            max_cand_int = int(raw_max_cand)
            if max_cand_int >= 1:
                max_candidatos = min(max_cand_int, 50)
        except (TypeError, ValueError):
            max_candidatos = None

    return {
        'activo': activo,
        'timeout_ms': timeout_ms,
        'max_candidatos': max_candidatos,
    }


def detectar_y_registrar_duplicados_al_iniciar(
    peticion: Peticiones | int,
    *,
    usuario_accion: Usuarios | int | None = None,
    notificar: bool = True,
    registrar_historial: bool = True,
) -> ResultadoDeteccionAutomatica:
    """
    Ejecuta detección rápida de posibles duplicados (`pg_trgm` + GPS Haversine,
    sin descargas ni comparación de imágenes de Cloudinary) sobre una petición ya
    guardada y confirmada en base de datos, y registra alertas idempotentes
    reutilizando `alertas_duplicados_service.registrar_alertas_desde_deteccion`.

    Nunca lanza excepciones hacia el flujo de registro ni altera peticiones,
    evidencias, asignaciones o historias clínicas.
    """
    id_peticion_ref: int | None = (
        getattr(peticion, 'id_peticion', None)
        if isinstance(peticion, Peticiones)
        else (peticion if isinstance(peticion, int) and not isinstance(peticion, bool) else None)
    )

    try:
        cfg_auto = _obtener_config_deteccion_automatica()
        if not cfg_auto['activo']:
            return ResultadoDeteccionAutomatica(
                ejecutada=False,
                peticion_id=id_peticion_ref,
                candidatos_detectados=0,
                omitida_motivo='DESACTIVADA',
            )

        if isinstance(peticion, Peticiones):
            id_peticion_ref = peticion.id_peticion
            if id_peticion_ref is None or getattr(getattr(peticion, '_state', None), 'adding', False):
                return ResultadoDeteccionAutomatica(
                    ejecutada=False,
                    peticion_id=id_peticion_ref,
                    candidatos_detectados=0,
                    omitida_motivo='PETICION_NO_PERSISTIDA',
                )
            peticion_obj = peticion
        else:
            id_peticion_ref = _extraer_id_peticion(peticion, nombre_campo='peticion')
            peticion_obj = (
                Peticiones.objects.select_related('id_ubicacion')
                .filter(pk=id_peticion_ref)
                .first()
            )
            if peticion_obj is None:
                return ResultadoDeteccionAutomatica(
                    ejecutada=False,
                    peticion_id=id_peticion_ref,
                    candidatos_detectados=0,
                    omitida_motivo='PETICION_NO_ENCONTRADA',
                )

        descripcion = getattr(peticion_obj, 'descripcion', None)
        ubicacion = (
            peticion_obj.id_ubicacion
            if getattr(peticion_obj, 'id_ubicacion_id', None) is not None
            else None
        )
        latitud = getattr(ubicacion, 'latitud', None) if ubicacion is not None else None
        longitud = getattr(ubicacion, 'longitud', None) if ubicacion is not None else None

        # Prevalidación en memoria para omitir consultas SQL si no hay ni descripción válida ni GPS válido.
        cfg_dup = obtener_config_duplicados()
        texto_activo = es_descripcion_valida_para_comparacion(
            normalizar_descripcion(descripcion),
            min_longitud=cfg_dup['min_longitud'],
        )
        gps_activo = normalizar_coordenadas(latitud, longitud) is not None
        if not texto_activo and not gps_activo:
            return ResultadoDeteccionAutomatica(
                ejecutada=False,
                peticion_id=id_peticion_ref,
                candidatos_detectados=0,
                omitida_motivo='SIN_TEXTO_NI_GPS_VALIDOS',
            )

        with transaction.atomic():
            if cfg_auto['timeout_ms'] and connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        [str(cfg_auto['timeout_ms'])],
                    )

            deteccion = buscar_peticiones_similares_por_descripcion(
                descripcion,
                latitud=latitud,
                longitud=longitud,
                excluir_id_peticion=id_peticion_ref,
                limite=cfg_auto['max_candidatos'],
                usar_dinov2=False,
                incluir_imagenes=False,
                incluir_evidencias=False,
            )

        candidatos = deteccion.get('posibles_duplicados') or []
        if not candidatos:
            return ResultadoDeteccionAutomatica(
                ejecutada=True,
                peticion_id=id_peticion_ref,
                candidatos_detectados=0,
                batch=None,
            )

        usuario_limpio: Usuarios | int | None = None
        if isinstance(usuario_accion, Usuarios):
            if getattr(usuario_accion, 'pk', None) is not None:
                usuario_limpio = usuario_accion
        elif isinstance(usuario_accion, int) and not isinstance(usuario_accion, bool) and usuario_accion > 0:
            usuario_limpio = usuario_accion

        batch = registrar_alertas_desde_deteccion(
            peticion_origen=id_peticion_ref,
            candidatos=deteccion,
            solo_posibles_duplicados=True,
            origen_deteccion=PeticionPosibleDuplicado.ORIGEN_REGISTRO,
            usuario_accion=usuario_limpio,
            notificar=notificar,
            registrar_historial=registrar_historial,
            actualizar_si_pendiente=True,
            ignorar_candidatos_inexistentes=True,
        )

        return ResultadoDeteccionAutomatica(
            ejecutada=True,
            peticion_id=id_peticion_ref,
            candidatos_detectados=len(candidatos),
            batch=batch,
        )
    except Exception as exc:
        logger.warning(
            'Fallo controlado en detección automática de duplicados para petición #%s: %s',
            id_peticion_ref,
            exc,
            exc_info=True,
        )
        return ResultadoDeteccionAutomatica(
            ejecutada=False,
            peticion_id=id_peticion_ref,
            candidatos_detectados=0,
            error=str(exc),
        )
