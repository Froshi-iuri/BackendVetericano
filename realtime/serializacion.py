"""
Ítem unificado de notificación (contrato v1, §5). Lo usan el REST central y el `data` de NP.
"""

MAPA_TIPO_PETICION = {
    'NUEVA_PETICION': 'peticion.creada',
    'PETICION_URGENTE': 'peticion.urgente',
    'ASIGNACION': 'peticion.asignada',
    'REASIGNACION': 'peticion.reasignada',
    'DESASIGNACION': 'peticion.desasignada',
    'CAMBIO_ESTADO': 'peticion.estado_actualizado',
    'POSIBLE_DUPLICADO': 'peticion.posible_duplicado',
}

PREFIJO_PETICION = 'pet'
PREFIJO_SISTEMA = 'sis'


def _iso(valor):
    return valor.isoformat() if valor else None


def item_peticion(notif) -> dict:
    """`notif` es una NotificacionPeticion (usar select_related('id_peticion'))."""
    peticion = getattr(notif, 'id_peticion', None)
    return {
        'clave': f'{PREFIJO_PETICION}-{notif.id_notificacion}',
        'origen': 'peticiones',
        'id_notificacion': notif.id_notificacion,
        'event_type': MAPA_TIPO_PETICION.get(notif.tipo, 'peticion.creada'),
        'modulo': 'peticiones',
        'tipo': notif.tipo,
        'titulo': notif.titulo,
        'mensaje': notif.mensaje,
        'prioridad': getattr(peticion, 'prioridad', None),
        'leida': bool(notif.leida),
        'fecha_creacion': _iso(notif.fecha_creacion),
        'fecha_lectura': _iso(notif.fecha_lectura),
        'referencia': {
            'id_peticion': notif.id_peticion_id,
            'numero_radicado': getattr(peticion, 'numero_radicado', None),
        },
    }


def item_sistema(notif) -> dict:
    """`notif` es una realtime.NotificacionSistema."""
    return {
        'clave': f'{PREFIJO_SISTEMA}-{notif.id_notificacion}',
        'origen': 'sistema',
        'id_notificacion': notif.id_notificacion,
        'event_type': notif.event_type,
        'modulo': notif.modulo,
        'tipo': notif.tipo,
        'titulo': notif.titulo,
        'mensaje': notif.mensaje,
        'prioridad': notif.prioridad,
        'leida': bool(notif.leida),
        'fecha_creacion': _iso(notif.fecha_creacion),
        'fecha_lectura': _iso(notif.fecha_lectura),
        'referencia': notif.referencia or {},
    }


def parsear_clave(clave: str):
    """'pet-12' -> ('pet', 12). Devuelve (None, None) si no es válida."""
    if not isinstance(clave, str) or '-' not in clave:
        return None, None
    prefijo, _, numero = clave.partition('-')
    if prefijo not in (PREFIJO_PETICION, PREFIJO_SISTEMA) or not numero.isdigit():
        return None, None
    return prefijo, int(numero)
