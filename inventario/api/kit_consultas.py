"""
Consultas de Solo Lectura para el Módulo de Kit de Inventario.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.

Provee funciones de consulta y agregación de base de datos sin lógica en las vistas:
- Snapshot móvil (copia local para uso offline)
- Alertas de stock y vencimiento (agotados, críticos, bajos, vencidos, próximos a vencer)
- Construcción y filtrado de QuerySets para Kardex y Conflictos
- Validación de parámetros de consulta con errores canónicos (PARAMETRO_INVALIDO)
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from django.db.models import Sum, Case, When, IntegerField, Q, QuerySet
from django.utils import timezone

from inventario.models import (
    KitVeterinarioLotes,
    InventarioKardex,
    InventarioConsumosRegistro,
)
from inventario.constants import (
    STOCK_UMBRAL_CRITICO,
    STOCK_UMBRAL_BAJO,
    VENTANA_DIAS_VENCIMIENTO,
    CONSUMO_ESTADO_RECHAZADO_CONFLICTO,
)
from inventario.services.exceptions import InventarioDomainError
from inventario.services.kit_service import _obtener_fecha_hoy


# ============================================================================
# 1. EXCEPCIÓN DE PARÁMETROS Y VALIDACIONES
# ============================================================================

class ParametroInvalidoError(InventarioDomainError):
    """Lanzada cuando un parámetro de query recibido en los endpoints es inválido o malformado."""
    def __init__(self, message: str, extra: Optional[Dict[str, Any]] = None):
        super().__init__(message, codigo="PARAMETRO_INVALIDO", extra=extra)


def validar_parametro_entero_positivo(valor: Optional[str], nombre_param: str) -> Optional[int]:
    """
    Valida que una cadena de parámetro de consulta represente un entero estricto > 0.
    Acepta exclusivamente dígitos ASCII (0-9). Cualquier carácter Unicode, vacío o no numérico
    lanza ParametroInvalidoError (HTTP 400).
    """
    if valor is None:
        return None
    val_strip = str(valor).strip()
    if not (val_strip.isascii() and val_strip.isdigit()):
        raise ParametroInvalidoError(f"El parámetro '{nombre_param}' debe ser un entero mayor a cero.")
    val_int = int(val_strip)
    if val_int <= 0:
        raise ParametroInvalidoError(f"El parámetro '{nombre_param}' debe ser un entero mayor a cero.")
    return val_int


def validar_parametro_fecha_iso(valor: Optional[str], nombre_param: str) -> Optional[Any]:
    """Valida que una cadena de parámetro de consulta tenga formato ISO YYYY-MM-DD."""
    if valor is None:
        return None
    try:
        return datetime.strptime(str(valor).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ParametroInvalidoError(
            f"El parámetro '{nombre_param}' debe tener formato de fecha válido (YYYY-MM-DD)."
        )


def validar_parametro_booleano(valor: Optional[str], nombre_param: str) -> Optional[bool]:
    """Valida que una cadena de parámetro sea 'true' o 'false'."""
    if valor is None:
        return None
    val_clean = str(valor).strip().lower()
    if val_clean == 'true':
        return True
    if val_clean == 'false':
        return False
    raise ParametroInvalidoError(f"El parámetro '{nombre_param}' debe ser 'true' o 'false'.")


def validar_parametro_choice(valor: Optional[str], permitidos: List[str], nombre_param: str) -> Optional[str]:
    """Valida que una cadena pertenezca a la lista de opciones permitidas."""
    if valor is None:
        return None
    val_clean = str(valor).strip().upper()
    if val_clean not in permitidos:
        raise ParametroInvalidoError(
            f"El parámetro '{nombre_param}' ('{valor}') no es válido. Opciones permitidas: {permitidos}."
        )
    return val_clean


def validar_rango_fechas(desde: Optional[Any], hasta: Optional[Any]) -> None:
    """Verifica que 'desde' no sea posterior a 'hasta' si ambos están presentes."""
    if desde and hasta and desde > hasta:
        raise ParametroInvalidoError("El parámetro 'desde' no puede ser posterior a 'hasta'.")


# ============================================================================
# 2. FUNCIONES DE CONSULTA
# ============================================================================

def consultar_snapshot_movil(id_veterinario: int, id_medicamento: Optional[int] = None) -> Dict[str, Any]:
    """
    Calcula el snapshot del kit del veterinario para persistencia local offline:
    - Retorna timestamp en época (segundos enteros).
    - Suma cantidad_actual de lotes VIGENTES (fecha_vencimiento IS NULL o fecha_vencimiento >= hoy).
    - Incluye medicamentos con saldo 0 siempre que pertenezcan al kit del veterinario
      (lotes con cantidad_referencia > 0 o cantidad_actual > 0).
    - Permite filtrar por id_medicamento opcional.
    """
    hoy = _obtener_fecha_hoy()
    ahora = timezone.now()
    epoch_timestamp = int(ahora.timestamp())

    qs = KitVeterinarioLotes.objects.filter(id_veterinario=id_veterinario)
    if id_medicamento is not None:
        qs = qs.filter(id_medicamento=id_medicamento)

    # Filtrar solo medicamentos que pertenezcan al kit (referencia > 0 o stock físico > 0)
    qs = qs.filter(
        Q(cantidad_referencia__gt=0) | Q(cantidad_actual__gt=0)
    )

    filas = qs.values(
        'id_medicamento',
        'id_medicamento__nombre'
    ).annotate(
        saldo_disponible=Sum(
            Case(
                When(
                    Q(fecha_vencimiento__isnull=True) | Q(fecha_vencimiento__gte=hoy),
                    then='cantidad_actual'
                ),
                default=0,
                output_field=IntegerField()
            )
        )
    ).order_by('id_medicamento__nombre', 'id_medicamento')

    stock = [
        {
            'id_medicamento': f['id_medicamento'],
            'nombre': f['id_medicamento__nombre'] or f"Medicamento #{f['id_medicamento']}",
            'saldo_disponible': f['saldo_disponible'] if f['saldo_disponible'] is not None else 0,
        }
        for f in filas
    ]

    return {
        'timestamp': epoch_timestamp,
        'stock': stock,
    }


def consultar_alertas_kit(
    es_gestor: bool,
    id_veterinario_solicitante: int,
    id_veterinario_filtro: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Calcula las alertas de stock y vencimiento para el kit:
    - Por medicamento (y por veterinario cuando es gestor):
        * agotados: saldo_vigente == 0
        * criticos: 0 < saldo_vigente < STOCK_UMBRAL_CRITICO
        * bajos: STOCK_UMBRAL_CRITICO <= saldo_vigente < STOCK_UMBRAL_BAJO
    - Solo medicamentos que el veterinario tenga en su kit (cantidad_referencia > 0 o cantidad_actual > 0).
    - vencidos: lotes del kit con cantidad_actual > 0 y fecha_vencimiento < hoy:
        id_kit_lote, id_inventario, id_medicamento, nombre, cantidad, fecha_vencimiento,
        dias_vencido y (si es gestor) id_veterinario y nombre_veterinario.
    - proximos_a_vencer: lotes del kit con cantidad_actual > 0 y fecha_vencimiento entre hoy
        y hoy + VENTANA_DIAS_VENCIMIENTO:
        id_kit_lote, id_inventario, id_medicamento, nombre, cantidad, fecha_vencimiento,
        dias_restantes y (si es gestor) id_veterinario y nombre_veterinario.
    """
    hoy = _obtener_fecha_hoy()

    base_qs = KitVeterinarioLotes.objects.all()
    if not es_gestor:
        base_qs = base_qs.filter(id_veterinario=id_veterinario_solicitante)
    elif id_veterinario_filtro is not None:
        base_qs = base_qs.filter(id_veterinario=id_veterinario_filtro)

    # Filtrar solo medicamentos que el veterinario tenga asignados en su kit (referencia o stock físico)
    base_qs = base_qs.filter(
        Q(cantidad_referencia__gt=0) | Q(cantidad_actual__gt=0)
    )

    if es_gestor:
        group_fields = [
            'id_veterinario',
            'id_veterinario__nombre',
            'id_veterinario__apellido',
            'id_medicamento',
            'id_medicamento__nombre',
        ]
    else:
        group_fields = [
            'id_medicamento',
            'id_medicamento__nombre',
        ]

    saldos_qs = base_qs.values(*group_fields).annotate(
        saldo_vigente=Sum(
            Case(
                When(
                    Q(fecha_vencimiento__isnull=True) | Q(fecha_vencimiento__gte=hoy),
                    then='cantidad_actual'
                ),
                default=0,
                output_field=IntegerField()
            )
        )
    ).order_by('id_medicamento__nombre', 'id_medicamento')

    agotados = []
    criticos = []
    bajos = []

    for fila in saldos_qs:
        saldo = fila['saldo_vigente'] if fila['saldo_vigente'] is not None else 0
        item = {
            'id_medicamento': fila['id_medicamento'],
            'nombre': fila['id_medicamento__nombre'] or f"Medicamento #{fila['id_medicamento']}",
            'saldo_vigente': saldo,
        }
        if es_gestor:
            item['id_veterinario'] = fila['id_veterinario']
            nom_vet = f"{fila['id_veterinario__nombre']} {fila['id_veterinario__apellido']}".strip()
            item['nombre_veterinario'] = nom_vet

        if saldo == 0:
            agotados.append(item)
        elif 0 < saldo < STOCK_UMBRAL_CRITICO:
            criticos.append(item)
        elif STOCK_UMBRAL_CRITICO <= saldo < STOCK_UMBRAL_BAJO:
            bajos.append(item)

    # 1. Lotes vencidos en kit (fecha_vencimiento < hoy y cantidad_actual > 0)
    lotes_vencidos_qs = base_qs.filter(
        cantidad_actual__gt=0,
        fecha_vencimiento__lt=hoy,
    ).select_related('id_medicamento', 'id_veterinario').order_by('fecha_vencimiento', 'id_kit_lote')

    vencidos = []
    for lote in lotes_vencidos_qs:
        dias_vencido = (hoy - lote.fecha_vencimiento).days
        item_vencido = {
            'id_kit_lote': lote.id_kit_lote,
            'id_inventario': lote.id_inventario_id,
            'id_medicamento': lote.id_medicamento_id,
            'nombre': lote.id_medicamento.nombre if lote.id_medicamento else f"Medicamento #{lote.id_medicamento_id}",
            'cantidad': lote.cantidad_actual,
            'fecha_vencimiento': lote.fecha_vencimiento.isoformat() if lote.fecha_vencimiento else None,
            'dias_vencido': dias_vencido,
        }
        if es_gestor:
            item_vencido['id_veterinario'] = lote.id_veterinario_id
            nom_vet = f"{lote.id_veterinario.nombre} {lote.id_veterinario.apellido}".strip() if lote.id_veterinario else ''
            item_vencido['nombre_veterinario'] = nom_vet
        vencidos.append(item_vencido)

    # 2. Lotes próximos a vencer (fecha_vencimiento entre hoy y hoy + VENTANA_DIAS_VENCIMIENTO)
    limite_vencimiento = hoy + timedelta(days=VENTANA_DIAS_VENCIMIENTO)
    lotes_vence_qs = base_qs.filter(
        cantidad_actual__gt=0,
        fecha_vencimiento__gte=hoy,
        fecha_vencimiento__lte=limite_vencimiento,
    ).select_related('id_medicamento', 'id_veterinario').order_by('fecha_vencimiento', 'id_kit_lote')

    proximos_a_vencer = []
    for lote in lotes_vence_qs:
        dias_restantes = (lote.fecha_vencimiento - hoy).days
        item_venc = {
            'id_kit_lote': lote.id_kit_lote,
            'id_inventario': lote.id_inventario_id,
            'id_medicamento': lote.id_medicamento_id,
            'nombre': lote.id_medicamento.nombre if lote.id_medicamento else f"Medicamento #{lote.id_medicamento_id}",
            'cantidad': lote.cantidad_actual,
            'fecha_vencimiento': lote.fecha_vencimiento.isoformat() if lote.fecha_vencimiento else None,
            'dias_restantes': dias_restantes,
        }
        if es_gestor:
            item_venc['id_veterinario'] = lote.id_veterinario_id
            nom_vet = f"{lote.id_veterinario.nombre} {lote.id_veterinario.apellido}".strip() if lote.id_veterinario else ''
            item_venc['nombre_veterinario'] = nom_vet
        proximos_a_vencer.append(item_venc)

    return {
        'agotados': agotados,
        'criticos': criticos,
        'bajos': bajos,
        'vencidos': vencidos,
        'proximos_a_vencer': proximos_a_vencer,
    }


def construir_queryset_kardex(
    es_gestor: bool,
    id_veterinario_solicitante: int,
    filtros: Dict[str, Any],
) -> QuerySet:
    """
    Construye el QuerySet para consulta inmutable de Kardex:
    - Veterinario ve SOLO sus propios movimientos forzado en el queryset.
    - Gestor ve todos y puede filtrar por id_veterinario.
    - Soporta filtros validados: id_medicamento, tipo_movimiento, ubicacion,
      id_inventario, desde y hasta (inclusivo).
    - Aplica select_related y ordenamiento cronológico inverso.
    """
    qs = InventarioKardex.objects.select_related(
        'id_medicamento',
        'id_usuario_ejecuta',
        'id_veterinario',
    )

    if not es_gestor:
        qs = qs.filter(id_veterinario=id_veterinario_solicitante)
    elif filtros.get('id_veterinario') is not None:
        qs = qs.filter(id_veterinario=filtros['id_veterinario'])

    if filtros.get('id_medicamento') is not None:
        qs = qs.filter(id_medicamento=filtros['id_medicamento'])

    if filtros.get('tipo_movimiento'):
        qs = qs.filter(tipo_movimiento=filtros['tipo_movimiento'])

    if filtros.get('ubicacion'):
        qs = qs.filter(ubicacion=filtros['ubicacion'])

    if filtros.get('id_inventario') is not None:
        qs = qs.filter(id_inventario=filtros['id_inventario'])

    if filtros.get('desde') is not None:
        dt_desde = datetime.combine(filtros['desde'], datetime.min.time())
        qs = qs.filter(fecha_hora__gte=dt_desde)

    if filtros.get('hasta') is not None:
        dt_hasta = datetime.combine(filtros['hasta'] + timedelta(days=1), datetime.min.time())
        qs = qs.filter(fecha_hora__lt=dt_hasta)

    return qs.order_by('-fecha_hora', '-id_kardex')


def construir_queryset_conflictos(filtros: Dict[str, Any]) -> QuerySet:
    """
    Construye el QuerySet para consulta de consumos en RECHAZADO_CONFLICTO:
    - Filtro base obligatorio: estado == RECHAZADO_CONFLICTO.
    - Filtros opcionales: resuelto (True/False), id_veterinario, id_medicamento.
    - Aplica select_related y ordenamiento cronológico inverso.
    """
    qs = InventarioConsumosRegistro.objects.filter(
        estado=CONSUMO_ESTADO_RECHAZADO_CONFLICTO
    ).select_related(
        'id_veterinario',
        'id_medicamento',
        'resuelto_por',
    )

    if filtros.get('resuelto') is not None:
        if filtros['resuelto'] is True:
            qs = qs.filter(fecha_resolucion_conflicto__isnull=False)
        else:
            qs = qs.filter(fecha_resolucion_conflicto__isnull=True)

    if filtros.get('id_veterinario') is not None:
        qs = qs.filter(id_veterinario=filtros['id_veterinario'])

    if filtros.get('id_medicamento') is not None:
        qs = qs.filter(id_medicamento=filtros['id_medicamento'])

    return qs.order_by('-fecha_consumo', '-id_consumo')
