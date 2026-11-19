"""
Servicio de dominio para Compras, Detalles de Compra e Integridad de Lotes en Bodega General.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.

Garantiza:
- Creación atómica de Compra + DetalleCompra + Inventarios + Kardex (ENTRADA_COMPRA).
- Validación estricta de payloads de compra y detalle de compra.
- Protección de integridad referencial y trazabilidad con bloqueos select_for_update(no_key=True)
  siguiendo el orden global: compra -> detalle_compra -> inventarios -> kit_veterinario_lotes.
"""

from datetime import date, datetime
from django.db import transaction

from inventario import notificaciones_realtime as notif_rt
from inventario.constants import (
    TIPO_KARDEX_ENTRADA_COMPRA,
    UBICACION_KARDEX_GENERAL,
)
from inventario.models import (
    Compra,
    DetalleCompra,
    InventarioKardex,
    Inventarios,
    KitVeterinarioLotes,
    Proveedores,
    SolicitudesKitDetalles,
)
from inventario.services.exceptions import (
    CantidadInvalidaError,
    ConflictoOperacionInventarioError,
    InventarioDomainError,
    MedicamentoInvalidoError,
)
from inventario.services.kit_service import _es_entero_valido, _validar_gestor
from medicamentos.models import Medicamentos


ESTADOS_LOTE_PERMITIDOS = ('Disponible', 'Agotado', 'Cuarentena')


def normalizar_fecha_vencimiento(valor) -> date | None:
    """Valida y normaliza una fecha de vencimiento opcional (ISO YYYY-MM-DD o date)."""
    if valor is None or valor == '':
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor.strip())
        except ValueError as exc:
            raise InventarioDomainError(
                f"Fecha de vencimiento inválida '{valor}'. Use formato YYYY-MM-DD.",
                codigo="FECHA_VENCIMIENTO_INVALIDA",
            ) from exc
    raise InventarioDomainError(
        "La fecha de vencimiento debe ser una cadena YYYY-MM-DD o nula.",
        codigo="FECHA_VENCIMIENTO_INVALIDA",
    )


def _resolver_pk(valor, campo: str) -> int:
    """Extrae la llave primaria entera positiva desde instancia ORM o entero estricto."""
    if hasattr(valor, 'pk') and not isinstance(valor, bool):
        pk = valor.pk
    else:
        pk = valor
    if not _es_entero_valido(pk, minimo=1):
        raise InventarioDomainError(
            f"El campo '{campo}' debe ser un identificador entero positivo válido.",
            codigo="IDENTIFICADOR_INVALIDO",
        )
    return pk


def _validar_medicamento_activo(id_medicamento) -> Medicamentos:
    """Verifica que el medicamento exista en catálogo y se encuentre activo."""
    med_pk = _resolver_pk(id_medicamento, 'id_medicamento')
    medicamento = Medicamentos.objects.filter(pk=med_pk).first()
    if not medicamento:
        raise MedicamentoInvalidoError(
            f"El medicamento #{med_pk} no existe en el catálogo."
        )
    if not medicamento.activo:
        raise MedicamentoInvalidoError(
            f"El medicamento '{medicamento.nombre}' (#{med_pk}) está inactivo y no puede ingresarse por compra."
        )
    return medicamento


def _validar_cantidad_compra(cantidad) -> int:
    """Valida que la cantidad ingresada por compra sea un entero estricto mayor a cero."""
    if not _es_entero_valido(cantidad, minimo=1):
        raise CantidadInvalidaError(
            "La cantidad del detalle de compra debe ser un número entero mayor a cero."
        )
    return cantidad


def validar_item_detalle_compra(item: dict) -> dict:
    """Valida un renglón de compra y retorna sus objetos y valores normalizados."""
    if not isinstance(item, dict):
        raise InventarioDomainError(
            "Cada elemento de 'detalles' debe ser un objeto JSON.",
            codigo="DETALLE_COMPRA_INVALIDO",
        )

    medicamento = _validar_medicamento_activo(item.get('id_medicamento'))
    cantidad = _validar_cantidad_compra(item.get('cantidad'))
    fecha_vencimiento = normalizar_fecha_vencimiento(item.get('fecha_vencimiento'))

    return {
        'medicamento': medicamento,
        'cantidad': cantidad,
        'fecha_vencimiento': fecha_vencimiento,
    }


def _crear_lote_y_kardex_compra(
    gestor,
    compra: Compra,
    detalle: DetalleCompra,
    medicamento: Medicamentos,
    cantidad: int,
    fecha_vencimiento: date | None = None,
) -> Inventarios:
    """Crea el lote físico en Bodega General y su movimiento inmutable ENTRADA_COMPRA."""
    lote = Inventarios.objects.create(
        id_detalle_compra=detalle,
        cantidad_actual=cantidad,
        fecha_vencimiento=fecha_vencimiento,
        estado='Disponible',
    )
    InventarioKardex.objects.create(
        tipo_movimiento=TIPO_KARDEX_ENTRADA_COMPRA,
        ubicacion=UBICACION_KARDEX_GENERAL,
        id_usuario_ejecuta=gestor,
        id_veterinario=None,
        id_medicamento=medicamento,
        id_inventario=lote,
        cantidad=cantidad,
        saldo_anterior=0,
        saldo_resultante=cantidad,
        motivo=f"Entrada a Bodega General por compra #{compra.pk} (detalle #{detalle.pk}).",
    )
    return lote


@transaction.atomic
def registrar_compra(id_usuario: int, id_proveedor, detalles) -> Compra:
    """
    Registra atómicamente una compra, sus detalles, un lote físico por renglón
    en Inventarios y su movimiento ENTRADA_COMPRA en InventarioKardex.
    """
    gestor = _validar_gestor(id_usuario)
    proveedor_pk = _resolver_pk(id_proveedor, 'id_proveedor')
    proveedor = Proveedores.objects.filter(pk=proveedor_pk).first()
    if not proveedor:
        raise InventarioDomainError(
            f"El proveedor #{proveedor_pk} no existe.",
            codigo="PROVEEDOR_INVALIDO",
        )

    if not isinstance(detalles, list) or len(detalles) == 0:
        raise InventarioDomainError(
            "Debe incluir una lista no vacía en 'detalles' para registrar la compra.",
            codigo="DETALLES_COMPRA_REQUERIDOS",
        )

    items_validados = [validar_item_detalle_compra(item) for item in detalles]

    compra = Compra.objects.create(id_proveedor=proveedor)
    med_ids = []
    for item in items_validados:
        medicamento = item['medicamento']
        cantidad = item['cantidad']
        fecha_vencimiento = item['fecha_vencimiento']

        detalle = DetalleCompra.objects.create(
            id_compra=compra,
            id_medicamento=medicamento,
            cantidad=cantidad,
        )
        _crear_lote_y_kardex_compra(
            gestor=gestor,
            compra=compra,
            detalle=detalle,
            medicamento=medicamento,
            cantidad=cantidad,
            fecha_vencimiento=fecha_vencimiento,
        )
        med_ids.append(medicamento.pk)

    notif_rt.alertar_bodega(med_ids=med_ids)
    return compra


@transaction.atomic
def agregar_detalle_compra(
    id_usuario: int,
    id_compra,
    id_medicamento,
    cantidad,
    fecha_vencimiento=None,
) -> DetalleCompra:
    """
    Agrega atómicamente un detalle a una compra existente bloqueada con
    select_for_update(no_key=True), creando su lote y su Kardex ENTRADA_COMPRA.
    """
    gestor = _validar_gestor(id_usuario)
    compra_pk = _resolver_pk(id_compra, 'id_compra')

    compra = Compra.objects.select_for_update(no_key=True).filter(pk=compra_pk).first()
    if not compra:
        raise InventarioDomainError(
            f"La compra #{compra_pk} no existe.",
            codigo="COMPRA_NO_ENCONTRADA",
        )

    medicamento = _validar_medicamento_activo(id_medicamento)
    cantidad_validada = _validar_cantidad_compra(cantidad)
    fecha_validada = normalizar_fecha_vencimiento(fecha_vencimiento)

    detalle = DetalleCompra.objects.create(
        id_compra=compra,
        id_medicamento=medicamento,
        cantidad=cantidad_validada,
    )
    _crear_lote_y_kardex_compra(
        gestor=gestor,
        compra=compra,
        detalle=detalle,
        medicamento=medicamento,
        cantidad=cantidad_validada,
        fecha_vencimiento=fecha_validada,
    )

    notif_rt.alertar_bodega(med_ids=[medicamento.pk])
    return detalle


def verificar_compra_sin_dependencias(compra_id: int, accion: str) -> Compra:
    """
    Bloquea la compra y verifica que no posea detalles ni lotes antes de modificar
    el proveedor o eliminar la cabecera.
    """
    compra = Compra.objects.select_for_update(no_key=True).filter(pk=compra_id).first()
    if not compra:
        raise ConflictoOperacionInventarioError(
            f"La compra #{compra_id} no existe o ya fue eliminada.",
            codigo="COMPRA_NO_ENCONTRADA",
        )

    tiene_detalles = DetalleCompra.objects.filter(id_compra=compra).exists()
    tiene_lotes = Inventarios.objects.filter(id_detalle_compra__id_compra=compra).exists()
    if tiene_detalles or tiene_lotes:
        raise ConflictoOperacionInventarioError(
            f"No se puede {accion} la compra #{compra.pk} porque tiene detalles de compra o lotes "
            f"registrados en inventario/Kardex.",
            codigo="COMPRA_CON_DEPENDENCIAS",
        )
    return compra


def verificar_detalle_compra_sin_dependencias(detalle_id: int, accion: str) -> DetalleCompra:
    """
    Bloquea el detalle de compra y sus lotes vinculados en orden ascendente y verifica
    que no existan lotes, asignaciones a kit, solicitudes ni movimientos en Kardex.
    """
    detalle = DetalleCompra.objects.select_for_update(no_key=True).filter(pk=detalle_id).first()
    if not detalle:
        raise ConflictoOperacionInventarioError(
            f"El detalle de compra #{detalle_id} no existe o ya fue eliminado.",
            codigo="DETALLE_COMPRA_NO_ENCONTRADO",
        )

    lotes_ids = list(
        Inventarios.objects.select_for_update(no_key=True)
        .filter(id_detalle_compra=detalle)
        .order_by('id_inventario')
        .values_list('id_inventario', flat=True)
    )
    if lotes_ids:
        tiene_kits = KitVeterinarioLotes.objects.filter(id_inventario_id__in=lotes_ids).exists()
        tiene_solicitudes = SolicitudesKitDetalles.objects.filter(id_inventario_origen_id__in=lotes_ids).exists()
        tiene_kardex = InventarioKardex.objects.filter(id_inventario_id__in=lotes_ids).exists()
        raise ConflictoOperacionInventarioError(
            f"No se puede {accion} el detalle de compra #{detalle.pk} porque posee lotes físicos "
            f"y trazabilidad asociada en inventario"
            f"{' (kits/solicitudes/Kardex activos)' if (tiene_kits or tiene_solicitudes or tiene_kardex) else ''}.",
            codigo="DETALLE_COMPRA_CON_DEPENDENCIAS",
        )
    return detalle


def lote_tiene_historial_operativo(lote: Inventarios) -> bool:
    """
    Indica si un lote ya participó en asignaciones a kit, solicitudes o movimientos
    de Kardex distintos al ingreso inicial ENTRADA_COMPRA.
    """
    return (
        KitVeterinarioLotes.objects.filter(id_inventario=lote).exists()
        or SolicitudesKitDetalles.objects.filter(id_inventario_origen=lote).exists()
        or InventarioKardex.objects.filter(id_inventario=lote)
        .exclude(tipo_movimiento=TIPO_KARDEX_ENTRADA_COMPRA)
        .exists()
    )


def verificar_lote_eliminable(lote_id: int) -> Inventarios:
    """
    Bloquea el lote en Bodega General y valida que no tenga existencias, vínculos
    de compra/salida, historial en Kardex ni referencias en kits o solicitudes.
    """
    lote = Inventarios.objects.select_for_update(no_key=True).filter(pk=lote_id).first()
    if not lote:
        raise ConflictoOperacionInventarioError(
            f"El lote #{lote_id} no existe o ya fue eliminado.",
            codigo="LOTE_NO_ENCONTRADO",
        )

    motivos = []
    if lote.cantidad_actual and lote.cantidad_actual > 0:
        motivos.append(f"tiene stock actual ({lote.cantidad_actual})")
    if lote.id_detalle_compra_id is not None:
        motivos.append("está vinculado a un detalle de compra")
    if lote.id_detalle_salida_id is not None:
        motivos.append("está vinculado a un detalle de salida")
    if KitVeterinarioLotes.objects.filter(id_inventario=lote).exists():
        motivos.append("está referenciado en kits veterinarios")
    if SolicitudesKitDetalles.objects.filter(id_inventario_origen=lote).exists():
        motivos.append("está referenciado en detalles de solicitudes de kit")
    if InventarioKardex.objects.filter(id_inventario=lote).exists():
        motivos.append("posee movimientos históricos en inventario_kardex")

    if motivos:
        raise ConflictoOperacionInventarioError(
            f"No se puede eliminar el lote #{lote.pk}: {', '.join(motivos)}.",
            codigo="LOTE_CON_DEPENDENCIAS",
        )
    return lote
