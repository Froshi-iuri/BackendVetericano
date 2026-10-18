"""
Módulo de servicios del inventario en dos niveles:
Bodega General CBA + Kit por Veterinario.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.
"""

from inventario.services.exceptions import (
    InventarioDomainError,
    StockInsuficienteBodegaError,
    StockInsuficienteKitError,
    MedicamentoSinLotesAsignablesError,
    UsuarioNoVeterinarioError,
    PermisoGestorRequeridoError,
    MedicamentoInvalidoError,
    SolicitudInvalidaError,
    CantidadInvalidaError,
    ConflictoIdempotenciaConsumoError,
    ConsumoInvalidoError,
    SolicitudNoEncontradaError,
    SolicitudEstadoInvalidaError,
    SolicitudDuplicadaError,
    SolicitudPermisoError,
    KitCompletoError,
    SolicitudSinCantidadAprobadaError,
)
from inventario.services.kit_service import (
    asignar_stock_a_kit,
    retirar_stock_de_kit,
    procesar_devolucion_aprobada,
)
from inventario.services.consumo_service import (
    descontar_consumo_kit,
)
from inventario.services.solicitudes_service import (
    crear_solicitud_reposicion,
    crear_solicitud_devolucion,
    aprobar_solicitud,
    rechazar_solicitud,
    cancelar_solicitud,
)

__all__ = [
    'InventarioDomainError',
    'StockInsuficienteBodegaError',
    'StockInsuficienteKitError',
    'MedicamentoSinLotesAsignablesError',
    'UsuarioNoVeterinarioError',
    'PermisoGestorRequeridoError',
    'MedicamentoInvalidoError',
    'SolicitudInvalidaError',
    'CantidadInvalidaError',
    'ConflictoIdempotenciaConsumoError',
    'ConsumoInvalidoError',
    'SolicitudNoEncontradaError',
    'SolicitudEstadoInvalidaError',
    'SolicitudDuplicadaError',
    'SolicitudPermisoError',
    'KitCompletoError',
    'SolicitudSinCantidadAprobadaError',
    'asignar_stock_a_kit',
    'retirar_stock_de_kit',
    'procesar_devolucion_aprobada',
    'descontar_consumo_kit',
    'crear_solicitud_reposicion',
    'crear_solicitud_devolucion',
    'aprobar_solicitud',
    'rechazar_solicitud',
    'cancelar_solicitud',
]


