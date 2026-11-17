"""
Catálogo de eventos (contrato v1, §4) y construcción del sobre (§3).
"""
import uuid

from django.utils import timezone

SCHEMA_VERSION = '1'

NP = 'NP'  # notificación persistente
AO = 'AO'  # alerta operativa
AS = 'AS'  # actualización silenciosa

# event_type -> módulo
CATALOGO = {
    # Peticiones
    'peticion.creada': 'peticiones',
    'peticion.urgente': 'peticiones',
    'peticion.asignada': 'peticiones',
    'peticion.reasignada': 'peticiones',
    'peticion.desasignada': 'peticiones',
    'peticion.asignacion_actualizada': 'peticiones',
    'peticion.estado_actualizado': 'peticiones',
    'peticion.posible_duplicado': 'peticiones',
    'peticion.duplicado_resuelto': 'peticiones',
    'peticion.seguimiento_creado': 'peticiones',
    # Inventario general y kit veterinario
    'inventario.movimiento_bodega': 'inventario',
    'inventario.alerta_bodega': 'inventario',
    'kit.stock_actualizado': 'inventario',
    'kit.alerta_stock_vencimiento': 'inventario',
    'kit.solicitud_creada': 'inventario',
    'kit.solicitud_resuelta': 'inventario',
    'kit.solicitud_cancelada': 'inventario',
    'kit.conflicto_sincronizacion': 'inventario',
    # Medicamentos y clínica
    'catalogo.medicamentos_actualizado': 'medicamentos',
    'clinica.atencion_registrada': 'clinica',
    'catalogo.clinico_actualizado': 'clinica',
    # Adopciones y voluntariado
    'adopcion.catalogo_actualizado': 'adopciones',
    'voluntariado.postulacion_creada': 'voluntariado',
    'voluntariado.postulacion_resuelta': 'voluntariado',
    'voluntariado.evento_actualizado': 'voluntariado',
    # Usuarios, sedes y seguridad
    'admin.usuario_gestionado': 'usuarios',
    'admin.sede_actualizada': 'sedes',
    'sesion.revocada': 'seguridad',
    # Transversal
    'notificacion.leida': 'transversal',
    'notificacion.todas_leidas': 'transversal',
}

CATEGORIAS = (NP, AO, AS)
NIVELES_AO = ('info', 'warning', 'danger')


class EventoDesconocido(ValueError):
    pass


def modulo_de(event_type: str) -> str:
    try:
        return CATALOGO[event_type]
    except KeyError as exc:
        raise EventoDesconocido(f"event_type fuera del catálogo: {event_type!r}") from exc


def construir_sobre(event_type: str, categoria: str, data: dict) -> dict:
    """Sobre v1 que recibe el cliente (`type: event`)."""
    if categoria not in CATEGORIAS:
        raise ValueError(f"categoría inválida: {categoria!r}")
    return {
        'type': 'event',
        'schema_version': SCHEMA_VERSION,
        'event_id': str(uuid.uuid4()),
        'event_type': event_type,
        'categoria': categoria,
        'modulo': modulo_de(event_type),
        'timestamp': timezone.now().isoformat(timespec='seconds'),
        'data': data or {},
    }


def grupo_usuario(id_usuario) -> str:
    return f'user_{int(id_usuario)}'


def grupo_rol(id_rol) -> str:
    return f'rol_{int(id_rol)}'
