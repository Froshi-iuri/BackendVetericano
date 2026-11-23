"""
Expresiones de orden compartidas por los listados (prioridad y estados operativos).

Solo se usan en ``order_by``: no agregan campos a la respuesta JSON.
"""

from django.db.models import Case, IntegerField, Q, Value, When

# Estados de petición que ya no requieren gestión (ver ESTADOS_TERMINALES_NORM en peticiones/utils.py),
# con sus variantes de género y tilde porque en la base de datos el nombre es texto libre.
ESTADOS_PETICION_TERMINADOS = (
    'Finalizada', 'Finalizado',
    'Resuelta', 'Resuelto',
    'Cancelada', 'Cancelado',
    'Rechazada', 'Rechazado',
    'Alta médica', 'Alta medica',
    'Fallecido', 'Fallecida',
    'Duplicada', 'Duplicado',
)

# Prioridad en texto libre: Urgente / Alta / Crítica / Emergencia primero, luego Media, luego el resto.
PRIORIDAD_URGENTE_CONTIENE = ('urgen', 'emergen', 'crític', 'critic', 'alta')
PRIORIDAD_MEDIA_CONTIENE = ('media',)


def _alguno(campo, lookup, valores):
    condicion = Q()
    for valor in valores:
        condicion |= Q(**{f'{campo}__{lookup}': valor})
    return condicion


def orden_prioridad(campo='prioridad'):
    """0 = urgente/alta, 1 = media, 2 = resto (incluye vacío)."""
    return Case(
        When(_alguno(campo, 'icontains', PRIORIDAD_URGENTE_CONTIENE), then=Value(0)),
        When(_alguno(campo, 'icontains', PRIORIDAD_MEDIA_CONTIENE), then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )


def orden_peticion_terminada(campo='id_estado__nombre'):
    """0 = petición abierta, 1 = petición en estado terminal."""
    return Case(
        When(_alguno(campo, 'iexact', ESTADOS_PETICION_TERMINADOS), then=Value(1)),
        default=Value(0),
        output_field=IntegerField(),
    )


def orden_valor_primero(campo, valor):
    """0 = el campo coincide con ``valor`` (p. ej. 'PENDIENTE'), 1 = cualquier otro."""
    return Case(
        When(Q(**{f'{campo}__iexact': valor}), then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )


def ordenar_peticiones(queryset):
    """Abiertas antes que terminadas; dentro, urgentes primero; luego de la más reciente a la más antigua."""
    return queryset.order_by(
        orden_peticion_terminada(),
        orden_prioridad(),
        '-fecha',
        '-id_peticion',
    )
