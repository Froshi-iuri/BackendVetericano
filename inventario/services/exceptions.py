"""
Excepciones de dominio para el módulo de inventario de dos niveles:
Bodega General CBA + Kit por Veterinario.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.
"""


class InventarioDomainError(Exception):
    """
    Excepción base para todos los errores de lógica de negocio del inventario.
    Permite encapsular un mensaje legible, un código canónico de error y metadatos adicionales.
    """
    def __init__(self, message: str, codigo: str = "INVENTARIO_ERROR", extra: dict | None = None):
        super().__init__(message)
        self.message = message
        self.codigo = codigo
        self.extra = extra or {}

    def __str__(self):
        return f"[{self.codigo}] {self.message}"


class StockInsuficienteBodegaError(InventarioDomainError):
    """
    Lanzada cuando Bodega General no cuenta con existencias disponibles y vigentes
    suficientes para cubrir la cantidad solicitada en una asignación.
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="STOCK_INSUFICIENTE_BODEGA", extra=extra)


class StockInsuficienteKitError(InventarioDomainError):
    """
    Lanzada cuando el kit del veterinario no cuenta con saldo vigente suficiente
    para procesar un retiro directo o una devolución hacia bodega.
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="STOCK_INSUFICIENTE_KIT", extra=extra)


class MedicamentoSinLotesAsignablesError(InventarioDomainError):
    """
    Lanzada cuando un medicamento no posee ningún lote con saldo disponible,
    vigente y no agotado en bodega general.
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="MEDICAMENTO_SIN_LOTES_ASIGNABLES", extra=extra)


class UsuarioNoVeterinarioError(InventarioDomainError):
    """
    Lanzada cuando el usuario objetivo de la operación de kit no existe,
    no está activo o no posee el rol institucional de Veterinario (Rol 3).
    """
    def __init__(self, message: str = "El usuario especificado no es un veterinario activo", extra: dict | None = None):
        super().__init__(message, codigo="USUARIO_NO_VETERINARIO", extra=extra)


class PermisoGestorRequeridoError(InventarioDomainError):
    """
    Lanzada cuando el usuario que ejecuta la acción no cuenta con rol autorizado
    para gestionar inventario (actualmente Rol 1: Administrador).
    """
    def __init__(self, message: str = "El usuario no cuenta con permisos de gestor de inventario", extra: dict | None = None):
        super().__init__(message, codigo="PERMISO_GESTOR_REQUERIDO", extra=extra)


class MedicamentoInvalidoError(InventarioDomainError):
    """
    Lanzada cuando el medicamento no existe en el catálogo institucional,
    o cuando se intenta asignar stock de un medicamento marcado como inactivo (descontinuado).
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="MEDICAMENTO_INVALIDO", extra=extra)


class SolicitudInvalidaError(InventarioDomainError):
    """
    Lanzada cuando una solicitud de kit no existe, no corresponde al tipo requerido
    (ej. no es DEVOLUCION), no está en estado PENDIENTE, o sus detalles no coinciden.
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="SOLICITUD_INVALIDA", extra=extra)


class CantidadInvalidaError(InventarioDomainError):
    """
    Lanzada cuando los parámetros numéricos de cantidad son <= 0, cuando la cantidad
    aprobada supera la solicitada, o ante parámetros de cantidad_referencia contradictorios.
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="CANTIDAD_INVALIDA", extra=extra)


class ConflictoIdempotenciaConsumoError(InventarioDomainError):
    """
    Lanzada cuando un client_uuid ya registrado en inventario_consumos_registro se reenvía
    con distinto veterinario, medicamento o cantidad solicitada, o cuando un consumo
    previamente revertido o rechazado en conflicto se intenta procesar en modo online.
    Corresponde a HTTP 400 Bad Request.
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="CONFLICTO_IDEMPOTENCIA_CONSUMO", extra=extra)


class ConsumoInvalidoError(InventarioDomainError):
    """
    Lanzada cuando la estructura del payload de consumo es inválida: lista vacía,
    client_uuid malformado o duplicado en el mismo payload, es_offline no booleano,
    origen_tipo inválido, o ausencia/contradicción de identificadores de origen en modo DIRECTO.
    Corresponde a HTTP 400 Bad Request.
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="CONSUMO_INVALIDO", extra=extra)


class SolicitudNoEncontradaError(InventarioDomainError):
    """
    Lanzada cuando la solicitud de kit solicitada no existe en la base de datos.
    Corresponde a HTTP 404 Not Found.
    """
    def __init__(self, message: str = "La solicitud de kit especificada no existe.", extra: dict | None = None):
        super().__init__(message, codigo="SOLICITUD_NO_ENCONTRADA", extra=extra)


class SolicitudEstadoInvalidaError(InventarioDomainError):
    """
    Lanzada cuando se intenta operar sobre una solicitud que no se encuentra en estado PENDIENTE
    (ya fue aprobada, parcialmente resuelta, rechazada o cancelada).
    Corresponde a HTTP 409 Conflict.
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="SOLICITUD_ESTADO_INVALIDA", extra=extra)


class SolicitudDuplicadaError(InventarioDomainError):
    """
    Lanzada cuando el veterinario intenta crear una solicitud de reposición total teniendo ya otra PENDIENTE,
    o cuando los medicamentos solicitados ya están incluidos en otra solicitud PENDIENTE del mismo profesional.
    Corresponde a HTTP 409 Conflict.
    """
    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message, codigo="SOLICITUD_DUPLICADA", extra=extra)


class SolicitudPermisoError(InventarioDomainError):
    """
    Lanzada cuando un veterinario intenta cancelar o manipular una solicitud que no le pertenece.
    Corresponde a HTTP 403 Forbidden.
    """
    def __init__(self, message: str = "No tiene permiso para operar sobre esta solicitud de kit.", extra: dict | None = None):
        super().__init__(message, codigo="SOLICITUD_PERMISO_DENEGADO", extra=extra)


class KitCompletoError(InventarioDomainError):
    """
    Lanzada cuando en una reposición total ('es_reposicion_total=True') todos los medicamentos
    del kit ya tienen su stock vigente completo respecto a las cantidades de referencia.
    Corresponde a HTTP 409 Conflict.
    """
    def __init__(self, message: str = "El kit ya está completo.", extra: dict | None = None):
        super().__init__(message, codigo="KIT_COMPLETO", extra=extra)


class SolicitudSinCantidadAprobadaError(InventarioDomainError):
    """
    Lanzada cuando el gestor intenta aprobar una solicitud pero todas las cantidades aprobadas son 0.
    Corresponde a HTTP 400 Bad Request (debe utilizarse rechazar_solicitud).
    """
    def __init__(self, message: str = "No se puede aprobar una solicitud con todas las cantidades en cero. Use rechazar_solicitud.", extra: dict | None = None):
        super().__init__(message, codigo="SOLICITUD_SIN_CANTIDAD_APROBADA", extra=extra)


