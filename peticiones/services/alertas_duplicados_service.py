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

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from peticiones.models import (
    HistorialPeticiones,
    NotificacionJuridico,
    Peticiones,
    PeticionPosibleDuplicado,
)
from peticiones.services.duplicados_service import (
    PeticionNoEncontradaError,
    buscar_peticiones_similares_por_descripcion,
    es_descripcion_valida_para_comparacion,
    normalizar_coordenadas,
    normalizar_descripcion,
    obtener_config_duplicados,
)
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

    return {
        'similitud_texto': similitud_texto,
        'coincidencia_texto': coincidencia_texto,
        'distancia_metros': distancia_metros,
        'coincidencia_gps': coincidencia_gps,
        'distancia_hamming': distancia_hamming,
        'similitud_imagen': similitud_imagen,
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

    return NotificacionJuridico.objects.create(
        id_peticion=peticion_notificada,
        tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
        mensaje=_construir_mensaje_notificacion(
            alerta,
            peticion_notificada,
            peticion_contraparte,
        ),
        leida=False,
    )


def _registrar_auditoria_par(
    peticion_menor: Peticiones,
    peticion_mayor: Peticiones,
    *,
    tipo_cambio: str,
    usuario_accion: Usuarios | None,
    observacion: str,
) -> tuple[int, int]:
    """
    Registra la auditoría en `HistorialPeticiones` para ambas peticiones del par
    respetando la estructura real de `historial_peticiones` (`tipo_cambio <= 30`),
    sin modificar estado ni asignaciones de las peticiones originales.
    """
    if len(tipo_cambio) > MAX_LONGITUD_TIPO_CAMBIO_HISTORIAL:
        raise ValueError(
            f'tipo_cambio "{tipo_cambio}" supera los {MAX_LONGITUD_TIPO_CAMBIO_HISTORIAL} '
            'caracteres permitidos por historial_peticiones.'
        )

    h_menor = HistorialPeticiones.objects.create(
        id_peticion=peticion_menor,
        tipo_cambio=tipo_cambio,
        id_estado_anterior=None,
        id_estado_nuevo=None,
        id_veterinario_anterior=None,
        id_veterinario_nuevo=None,
        id_usuario_accion=usuario_accion,
        observacion=observacion,
    )
    h_mayor = HistorialPeticiones.objects.create(
        id_peticion=peticion_mayor,
        tipo_cambio=tipo_cambio,
        id_estado_anterior=None,
        id_estado_nuevo=None,
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
) -> str:
    marcador = _marcador_par(alerta.peticion_menor_id, alerta.peticion_mayor_id)
    rad_menor = _formatear_radicado(peticion_menor)
    rad_mayor = _formatear_radicado(peticion_mayor)

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
            f'Petición principal: {_formatear_radicado(pet_principal)} '
            f'(ID #{pet_principal.id_peticion}); duplicado confirmado: '
            f'{_formatear_radicado(pet_duplicada)} (ID #{pet_duplicada.id_peticion}). '
            'Las peticiones y evidencias originales permanecen intactas.'
        )
    elif nuevo_estado == PeticionPosibleDuplicado.ESTADO_DESCARTADO:
        detalle = (
            f'Alerta #{alerta.id_alerta} DESCARTADA ({estado_anterior} -> DESCARTADO) '
            f'para {marcador} ({rad_menor} y {rad_mayor}). '
            'Se descarta duplicidad y ambas peticiones permanecen independientes.'
        )
    else:
        detalle = (
            f'Alerta #{alerta.id_alerta} REABIERTA ({estado_anterior} -> PENDIENTE) '
            f'para {marcador} ({rad_menor} y {rad_mayor}).'
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
        'coincidencia_gps',
        'distancia_hamming',
        'similitud_imagen',
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
                        similitud_texto=metricas['similitud_texto'],
                        coincidencia_texto=metricas['coincidencia_texto'],
                        distancia_metros=metricas['distancia_metros'],
                        coincidencia_gps=metricas['coincidencia_gps'],
                        distancia_hamming=metricas['distancia_hamming'],
                        similitud_imagen=metricas['similitud_imagen'],
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

        # Idempotencia si el estado (y principal cuando aplica) ya coinciden
        if estado_anterior == nuevo_estado:
            if nuevo_estado != PeticionPosibleDuplicado.ESTADO_CONFIRMADO:
                return ResultadoDecisionAlerta(
                    alerta=alerta,
                    cambiada=False,
                    estado_anterior=estado_anterior,
                    estado_nuevo=nuevo_estado,
                    historial_ids=(),
                )
            if alerta.peticion_principal_id == id_principal:
                return ResultadoDecisionAlerta(
                    alerta=alerta,
                    cambiada=False,
                    estado_anterior=estado_anterior,
                    estado_nuevo=nuevo_estado,
                    historial_ids=(),
                )
            if not permitir_cambio_decision_previa:
                raise TransicionAlertaInvalidaError(
                    f'La alerta #{alerta.id_alerta} ya fue confirmada con petición principal '
                    f'#{alerta.peticion_principal_id}. Para cambiarla, autorice explícitamente '
                    'el cambio de decisión previa.'
                )
        elif estado_anterior in ESTADOS_ALERTA_RESUELTOS and not permitir_cambio_decision_previa:
            raise TransicionAlertaInvalidaError(
                f'La alerta #{alerta.id_alerta} ya tiene decisión previa "{estado_anterior}" '
                f'y no puede pasar automáticamente a "{nuevo_estado}".'
            )

        alerta.estado_revision = nuevo_estado
        alerta.peticion_principal_id = id_principal
        alerta.revisado_por = revisor_obj
        alerta.observacion_revision = obs_limpia
        alerta.fecha_revision = _ahora_local()
        alerta.save(
            update_fields=[
                'estado_revision',
                'peticion_principal',
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
                ),
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

    return qs.order_by('-score_combinado', '-fecha_creacion', '-id_alerta')


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
