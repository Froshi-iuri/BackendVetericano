"""
Servicio de Solicitudes de Kit (Reposición y Devolución).
vetericano / CBA Popayán - Módulo de Inventario de Dos Niveles.

Proporciona las operaciones de ciclo de vida de solicitudes de kit:
1. crear_solicitud_reposicion: Veterinario solicita reposición (específica o total).
2. crear_solicitud_devolucion: Veterinario solicita devolución de lotes hacia bodega general.
3. aprobar_solicitud: Gestor aprueba reposición (asignando stock) o devolución (reintegrando/dando baja).
4. rechazar_solicitud: Gestor rechaza solicitud pendiente.
5. cancelar_solicitud: Veterinario cancela su propia solicitud pendiente.

Orden Global de Locks (R12):
0. usuarios (select_for_update(no_key=True)) SOLO en crear_solicitud_reposicion y crear_solicitud_devolucion
   - Serializa deterministamente creaciones concurrentes por veterinario, evitando condiciones de carrera
     incluso cuando inicialmente existen 0 solicitudes pendientes.
1. solicitudes_kit (select_for_update(no_key=True))
2. inventarios (select_for_update(no_key=True)) - al delegar en asignar/procesar devolución
3. kit_veterinario_lotes (select_for_update())
"""

from typing import Any, Dict, List, Optional
from django.db import transaction
from django.utils import timezone

from medicamentos.models import Medicamentos
from users.models import Usuarios
from inventario.models import (
    SolicitudesKit,
    SolicitudesKitDetalles,
    KitVeterinarioLotes,
)
from inventario.constants import (
    SOLICITUD_TIPO_REPOSICION,
    SOLICITUD_TIPO_DEVOLUCION,
    SOLICITUD_ESTADO_PENDIENTE,
    SOLICITUD_ESTADO_APROBADA,
    SOLICITUD_ESTADO_PARCIAL,
    SOLICITUD_ESTADO_RECHAZADA,
    SOLICITUD_ESTADO_CANCELADA,
    ROL_VETERINARIO,
    INVENTARIO_GESTOR_ROLES,
)
from inventario.services.exceptions import (
    CantidadInvalidaError,
    KitCompletoError,
    MedicamentoInvalidoError,
    PermisoGestorRequeridoError,
    SolicitudDuplicadaError,
    SolicitudEstadoInvalidaError,
    SolicitudInvalidaError,
    SolicitudNoEncontradaError,
    SolicitudPermisoError,
    SolicitudSinCantidadAprobadaError,
    StockInsuficienteKitError,
    UsuarioNoVeterinarioError,
)
from inventario.services import kit_service
from inventario.services.kit_service import _obtener_fecha_hoy, _es_entero_valido


def _bloquear_y_validar_veterinario(id_veterinario: int) -> Usuarios:
    """
    Lock Nivel 0 sobre la fila de 'usuarios' para serializar creaciones
    concurrentes del mismo veterinario y verificar que exista, esté activo
    y posea rol ROL_VETERINARIO.
    """
    if not _es_entero_valido(id_veterinario, minimo=1):
        raise CantidadInvalidaError("id_veterinario debe ser un entero mayor a cero.")
    vet = Usuarios.objects.select_for_update(no_key=True).filter(pk=id_veterinario).first()
    if not vet or not vet.activo or vet.id_rol_id != ROL_VETERINARIO:
        raise UsuarioNoVeterinarioError(
            f"El usuario #{id_veterinario} no existe, está inactivo o no posee rol de "
            f"Veterinario (rol requerido: {ROL_VETERINARIO})."
        )
    return vet


def _validar_veterinario(id_veterinario: int) -> Usuarios:
    """Verifica que el usuario exista, esté activo y cuente con rol de Veterinario (sin bloqueo de escritura)."""
    if not _es_entero_valido(id_veterinario, minimo=1):
        raise CantidadInvalidaError("id_veterinario debe ser un entero mayor a cero.")
    vet = Usuarios.objects.filter(pk=id_veterinario).first()
    if not vet or not vet.activo or vet.id_rol_id != ROL_VETERINARIO:
        raise UsuarioNoVeterinarioError(
            f"El usuario #{id_veterinario} no existe, está inactivo o no posee rol de "
            f"Veterinario (rol requerido: {ROL_VETERINARIO})."
        )
    return vet


def _validar_gestor(id_gestor: int) -> Usuarios:
    """Verifica que el usuario exista, esté activo y cuente con rol de gestor de inventario."""
    if not _es_entero_valido(id_gestor, minimo=1):
        raise CantidadInvalidaError("id_gestor debe ser un entero mayor a cero.")
    gestor = Usuarios.objects.filter(pk=id_gestor).first()
    if not gestor or not gestor.activo or gestor.id_rol_id not in INVENTARIO_GESTOR_ROLES:
        raise PermisoGestorRequeridoError(
            f"El usuario #{id_gestor} no existe, está inactivo o no posee rol de gestor de inventario "
            f"(roles autorizados: {INVENTARIO_GESTOR_ROLES})."
        )
    return gestor


@transaction.atomic
def crear_solicitud_reposicion(
    id_veterinario: int,
    motivo: str,
    items: Optional[List[Dict[str, Any]]] = None,
    es_reposicion_total: bool = False,
) -> Dict[str, Any]:
    """
    Crea una solicitud de reposición de stock para el kit de un veterinario.

    Parámetros:
    - id_veterinario: ID del veterinario solicitante.
    - motivo: Texto descriptivo obligatorio de la reposición (no vacío).
    - items: Lista de dicts [{'id_medicamento': int, 'cantidad': int}] cuando es_reposicion_total=False.
    - es_reposicion_total: Si es True, calcula automáticamente la necesidad de cada fármaco activo.

    Orden de Locks:
    0. usuarios (select_for_update(no_key=True)) sobre id_veterinario
       - Serializa intentos de creación concurrentes para el mismo veterinario, evitando condiciones
         de carrera cuando aún no existen solicitudes pendientes en la base de datos.
    1. solicitudes_kit (select_for_update(no_key=True)) para solicitudes PENDIENTE de tipo REPOSICION del veterinario
    """
    if not isinstance(motivo, str) or not motivo.strip():
        raise SolicitudInvalidaError("El motivo de la reposición es obligatorio y no puede estar vacío.")

    if not isinstance(es_reposicion_total, bool):
        raise SolicitudInvalidaError("es_reposicion_total debe ser un valor booleano.")

    # Lock 0: usuarios(no_key=True) sobre id_veterinario
    _bloquear_y_validar_veterinario(id_veterinario)

    # Lock 1: solicitudes_kit(no_key=True) para id_veterinario con estado PENDIENTE y tipo REPOSICION
    solicitudes_repo_pendientes = list(
        SolicitudesKit.objects.select_for_update(no_key=True).filter(
            id_veterinario=id_veterinario,
            tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
            estado=SOLICITUD_ESTADO_PENDIENTE
        ).order_by('id_solicitud')
    )

    # Máximo una reposición total PENDIENTE por veterinario
    if es_reposicion_total and any(s.es_reposicion_total for s in solicitudes_repo_pendientes):
        raise SolicitudDuplicadaError(
            "El veterinario ya cuenta con una solicitud de reposición total en estado PENDIENTE."
        )

    sol_repo_pend_ids = [s.id_solicitud for s in solicitudes_repo_pendientes]
    meds_en_reposiciones_pendientes = set(
        SolicitudesKitDetalles.objects.filter(
            id_solicitud__in=sol_repo_pend_ids
        ).values_list('id_medicamento_id', flat=True)
    ) if sol_repo_pend_ids else set()

    hoy = _obtener_fecha_hoy()
    items_finales = []

    if es_reposicion_total:
        if items:
            raise SolicitudInvalidaError("Si es_reposicion_total=True, no se deben enviar items específicos.")

        # Candidatos: medicamentos que tengan al menos un lote con cantidad_referencia > 0
        lotes_kit_ref = list(
            KitVeterinarioLotes.objects.filter(
                id_veterinario=id_veterinario,
                cantidad_referencia__gt=0
            ).order_by('id_kit_lote')
        )
        med_ids_candidatos = sorted(set(kl.id_medicamento_id for kl in lotes_kit_ref))

        # Consultar todos los lotes del kit para esos medicamentos
        todos_lotes_meds = list(
            KitVeterinarioLotes.objects.filter(
                id_veterinario=id_veterinario,
                id_medicamento__in=med_ids_candidatos
            ).order_by('id_kit_lote')
        )

        meds_activos_map = {
            m.id_medicamento: m
            for m in Medicamentos.objects.filter(
                pk__in=med_ids_candidatos,
                activo=True
            )
        }

        for med_id in med_ids_candidatos:
            if med_id not in meds_activos_map:
                continue  # Fármaco desactivado en catálogo institucional, se omite (Decisión 8)

            lotes_med = [kl for kl in todos_lotes_meds if kl.id_medicamento_id == med_id]
            # necesidad = max(0, suma de cantidad_referencia de TODOS los lotes - suma de cantidad_actual de lotes VIGENTES)
            suma_referencia_total = sum(kl.cantidad_referencia for kl in lotes_med)
            suma_actual_vigente = sum(
                kl.cantidad_actual for kl in lotes_med
                if kl.fecha_vencimiento is None or kl.fecha_vencimiento >= hoy
            )
            necesidad = max(0, suma_referencia_total - suma_actual_vigente)

            if necesidad > 0:
                if med_id in meds_en_reposiciones_pendientes:
                    raise SolicitudDuplicadaError(
                        f"Ya existe una solicitud de reposición pendiente que incluye el medicamento #{med_id} "
                        f"({meds_activos_map[med_id].nombre})."
                    )
                items_finales.append({
                    'id_medicamento': med_id,
                    'cantidad': necesidad,
                })

        if not items_finales:
            raise KitCompletoError(
                "El kit no requiere reposición: todos los medicamentos vigentes cuentan con stock completo "
                "según su cantidad de referencia."
            )
    else:
        if not items or not isinstance(items, list):
            raise SolicitudInvalidaError("items es obligatorio y debe ser una lista no vacía cuando es_reposicion_total=False.")

        med_ids_vistos = set()
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise SolicitudInvalidaError(f"El item en la posición {idx} debe ser un objeto/dict.")

            med_id = item.get('id_medicamento')
            cantidad = item.get('cantidad')

            if not _es_entero_valido(med_id, minimo=1):
                raise CantidadInvalidaError(f"id_medicamento en el item {idx} debe ser un entero mayor a cero.")
            if not _es_entero_valido(cantidad, minimo=1):
                raise CantidadInvalidaError(f"cantidad en el item {idx} debe ser un entero mayor a cero.")

            if med_id in med_ids_vistos:
                raise SolicitudInvalidaError(f"No se permiten medicamentos duplicados en los items (duplicado: #{med_id}).")
            med_ids_vistos.add(med_id)

            if med_id in meds_en_reposiciones_pendientes:
                raise SolicitudDuplicadaError(
                    f"Ya existe una solicitud de reposición pendiente que incluye el medicamento #{med_id}."
                )

            med = Medicamentos.objects.filter(pk=med_id).first()
            if not med:
                raise MedicamentoInvalidoError(f"El medicamento #{med_id} no existe.")
            if not med.activo:
                raise MedicamentoInvalidoError(
                    f"El medicamento #{med_id} ({med.nombre}) está inactivo y no puede ser solicitado para reposición."
                )

            items_finales.append({
                'id_medicamento': med_id,
                'cantidad': cantidad,
            })

    solicitud = SolicitudesKit.objects.create(
        tipo_solicitud=SOLICITUD_TIPO_REPOSICION,
        estado=SOLICITUD_ESTADO_PENDIENTE,
        id_veterinario_id=id_veterinario,
        motivo_solicitud=motivo.strip(),
        es_reposicion_total=es_reposicion_total,
    )

    detalles_creados = []
    for item in items_finales:
        det = SolicitudesKitDetalles.objects.create(
            id_solicitud=solicitud,
            id_medicamento_id=item['id_medicamento'],
            id_inventario_origen=None,
            cantidad_solicitada=item['cantidad'],
            cantidad_aprobada=0,
            es_vencido=False,
        )
        detalles_creados.append(det)

    return {
        'id_solicitud': solicitud.id_solicitud,
        'tipo_solicitud': solicitud.tipo_solicitud,
        'estado': solicitud.estado,
        'id_veterinario': solicitud.id_veterinario_id,
        'fecha_solicitud': solicitud.fecha_solicitud,
        'motivo_solicitud': solicitud.motivo_solicitud,
        'es_reposicion_total': solicitud.es_reposicion_total,
        'detalles': [
            {
                'id_detalle_solicitud': d.id_detalle_solicitud,
                'id_medicamento': d.id_medicamento_id,
                'cantidad_solicitada': d.cantidad_solicitada,
                'cantidad_aprobada': d.cantidad_aprobada,
                'es_vencido': d.es_vencido,
            }
            for d in detalles_creados
        ],
    }


@transaction.atomic
def crear_solicitud_devolucion(
    id_veterinario: int,
    items: List[Dict[str, Any]],
    motivo: str,
) -> Dict[str, Any]:
    """
    Crea una solicitud de devolución de stock desde el kit hacia bodega general.

    Parámetros:
    - id_veterinario: ID del veterinario que solicita devolver stock.
    - items: Lista de dicts [{'id_medicamento': int, 'cantidad': int}] exclusivamente.
    - motivo: Explicación obligatoria y no vacía de la devolución.

    Orden de Locks:
    0. usuarios (select_for_update(no_key=True)) sobre id_veterinario
       - Serializa deterministamente creaciones concurrentes por el mismo veterinario,
         impidiendo que dos transacciones concurrentes calculen stock disponible
         y comprometido de forma solapada cuando no existen solicitudes previas.
    1. solicitudes_kit (select_for_update(no_key=True)) para solicitudes PENDIENTE de tipo DEVOLUCION del veterinario
    3. kit_veterinario_lotes (select_for_update()) para los medicamentos de items
       (Se omite lock sobre inventarios de bodega general porque la creación no muta bodega).
    """
    # Validar motivo obligatorio y no vacío
    if not isinstance(motivo, str) or not motivo.strip():
        raise SolicitudInvalidaError("El motivo de la devolución es obligatorio y no puede estar vacío.")

    # Validar items no vacío
    if not items or not isinstance(items, list):
        raise SolicitudInvalidaError("items es obligatorio y debe ser una lista no vacía.")

    # Lock 0: usuarios(no_key=True) sobre id_veterinario
    _bloquear_y_validar_veterinario(id_veterinario)

    # Validar estructura estricta de items (solo id_medicamento y cantidad)
    med_ids_vistos = set()
    items_validados = []

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise SolicitudInvalidaError(f"El item en la posición {idx} debe ser un objeto/dict.")

        if 'es_vencido' in item or 'id_inventario_origen' in item:
            raise SolicitudInvalidaError(
                f"El item en la posición {idx} no debe incluir 'es_vencido' ni 'id_inventario_origen'."
            )

        med_id = item.get('id_medicamento')
        cantidad = item.get('cantidad')

        if not _es_entero_valido(med_id, minimo=1):
            raise CantidadInvalidaError(f"id_medicamento en el item {idx} debe ser un entero mayor a cero.")
        if not _es_entero_valido(cantidad, minimo=1):
            raise CantidadInvalidaError(f"cantidad en el item {idx} debe ser un entero mayor a cero.")

        if med_id in med_ids_vistos:
            raise SolicitudInvalidaError(
                f"No se permiten medicamentos duplicados en los items de devolución (duplicado: #{med_id})."
            )
        med_ids_vistos.add(med_id)

        items_validados.append({
            'id_medicamento': med_id,
            'cantidad': cantidad,
        })

    # Lock 1: solicitudes_kit(no_key=True) para id_veterinario con estado PENDIENTE y tipo DEVOLUCION
    solicitudes_dev_pendientes = list(
        SolicitudesKit.objects.select_for_update(no_key=True).filter(
            id_veterinario=id_veterinario,
            tipo_solicitud=SOLICITUD_TIPO_DEVOLUCION,
            estado=SOLICITUD_ESTADO_PENDIENTE
        ).order_by('id_solicitud')
    )
    sol_dev_pend_ids = [s.id_solicitud for s in solicitudes_dev_pendientes]

    # Validar existencia de los medicamentos en el catálogo (no exige activo=True según Decisión 8)
    med_ids_unicos = sorted(med_ids_vistos)
    meds_existentes = {m.id_medicamento: m for m in Medicamentos.objects.filter(pk__in=med_ids_unicos)}
    for med_id in med_ids_unicos:
        if med_id not in meds_existentes:
            raise MedicamentoInvalidoError(f"El medicamento #{med_id} no existe.")

    # Lock 3: kit_veterinario_lotes del veterinario para los id_medicamento de items
    kit_lotes = list(
        KitVeterinarioLotes.objects.select_for_update().filter(
            id_veterinario=id_veterinario,
            id_medicamento__in=med_ids_unicos
        ).order_by('id_kit_lote')
    )

    detalles_dev_pend = list(
        SolicitudesKitDetalles.objects.filter(
            id_solicitud__in=sol_dev_pend_ids,
            id_medicamento__in=med_ids_unicos
        )
    ) if sol_dev_pend_ids else []

    # Validar que cantidad <= stock_total del kit (INCLUYE vencidos) - comprometido
    for item in items_validados:
        med_id = item['id_medicamento']
        cant_pedida = item['cantidad']
        med_obj = meds_existentes[med_id]

        stock_total = sum(kl.cantidad_actual for kl in kit_lotes if kl.id_medicamento_id == med_id)
        comprometido = sum(d.cantidad_solicitada for d in detalles_dev_pend if d.id_medicamento_id == med_id)
        disponible = stock_total - comprometido

        if cant_pedida > disponible:
            raise StockInsuficienteKitError(
                f"Stock insuficiente en el kit de {med_obj.nombre}: disponible {disponible} "
                f"(stock total: {stock_total}, comprometido: {comprometido}), solicitado {cant_pedida}",
                extra={
                    'id_medicamento': med_id,
                    'stock_total': stock_total,
                    'comprometido': comprometido,
                    'solicitado': cant_pedida,
                }
            )

    solicitud = SolicitudesKit.objects.create(
        tipo_solicitud=SOLICITUD_TIPO_DEVOLUCION,
        estado=SOLICITUD_ESTADO_PENDIENTE,
        id_veterinario_id=id_veterinario,
        motivo_solicitud=motivo.strip(),
        es_reposicion_total=False,
    )

    detalles_creados = []
    for item in items_validados:
        det = SolicitudesKitDetalles.objects.create(
            id_solicitud=solicitud,
            id_medicamento_id=item['id_medicamento'],
            id_inventario_origen=None,
            cantidad_solicitada=item['cantidad'],
            cantidad_aprobada=0,
            es_vencido=False,
        )
        detalles_creados.append(det)

    return {
        'id_solicitud': solicitud.id_solicitud,
        'tipo_solicitud': solicitud.tipo_solicitud,
        'estado': solicitud.estado,
        'id_veterinario': solicitud.id_veterinario_id,
        'fecha_solicitud': solicitud.fecha_solicitud,
        'motivo_solicitud': solicitud.motivo_solicitud,
        'es_reposicion_total': solicitud.es_reposicion_total,
        'detalles': [
            {
                'id_detalle_solicitud': d.id_detalle_solicitud,
                'id_medicamento': d.id_medicamento_id,
                'id_inventario_origen': d.id_inventario_origen_id,
                'cantidad_solicitada': d.cantidad_solicitada,
                'cantidad_aprobada': d.cantidad_aprobada,
                'es_vencido': d.es_vencido,
            }
            for d in detalles_creados
        ],
    }


@transaction.atomic
def aprobar_solicitud(
    id_gestor: int,
    id_solicitud: int,
    detalles_aprobados: Optional[List[Dict[str, Any]]] = None,
    observaciones_gestor: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Aprueba una solicitud de kit (reposición o devolución) de forma total o parcial.

    Parámetros:
    - id_gestor: ID del usuario gestor con rol autorizado.
    - id_solicitud: ID de la solicitud PENDIENTE.
    - detalles_aprobados: Lista de dicts [{'id_detalle_solicitud': int, 'cantidad_aprobada': int}].
                          Si es None, aprueba el 100% de lo solicitado en cada detalle.
                          Si viene parcial, los detalles omitidos valen 0.
    - observaciones_gestor: Comentarios opcionales del gestor (str o None).

    Orden de Locks:
    1. solicitudes_kit (select_for_update(no_key=True)) por id_solicitud
    2. inventarios (select_for_update(no_key=True)) - delegado en kit_service
    3. kit_veterinario_lotes (select_for_update()) - delegado en kit_service
    """
    _validar_gestor(id_gestor)

    if observaciones_gestor is not None and not isinstance(observaciones_gestor, str):
        raise SolicitudInvalidaError("observaciones_gestor debe ser una cadena de texto o None.")

    if not _es_entero_valido(id_solicitud, minimo=1):
        raise CantidadInvalidaError("id_solicitud debe ser un entero mayor a cero.")

    # Lock 1: solicitudes_kit(no_key=True) por id_solicitud
    sol = SolicitudesKit.objects.select_for_update(no_key=True).filter(pk=id_solicitud).first()
    if not sol:
        raise SolicitudNoEncontradaError(f"La solicitud #{id_solicitud} no existe.")

    if sol.estado != SOLICITUD_ESTADO_PENDIENTE:
        raise SolicitudEstadoInvalidaError(
            f"La solicitud #{id_solicitud} no está en estado {SOLICITUD_ESTADO_PENDIENTE} "
            f"(estado actual: {sol.estado})."
        )

    # Lock de los detalles de la solicitud
    detalles_qs = list(sol.detalles.select_for_update().order_by('id_detalle_solicitud'))
    detalles_map = {d.id_detalle_solicitud: d for d in detalles_qs}

    if detalles_aprobados is None:
        cantidades_map = {d.id_detalle_solicitud: d.cantidad_solicitada for d in detalles_qs}
    else:
        if not isinstance(detalles_aprobados, list):
            raise CantidadInvalidaError("detalles_aprobados debe ser una lista de dicts.")

        # Con detalles_aprobados parcial, los detalles omitidos valen 0
        cantidades_map = {d.id_detalle_solicitud: 0 for d in detalles_qs}
        ids_vistos = set()

        for idx, item in enumerate(detalles_aprobados):
            if not isinstance(item, dict):
                raise CantidadInvalidaError(f"El elemento {idx} de detalles_aprobados debe ser un dict.")

            det_id = item.get('id_detalle_solicitud')
            cant_aprob = item.get('cantidad_aprobada')

            if not _es_entero_valido(det_id, minimo=1):
                raise CantidadInvalidaError(f"id_detalle_solicitud en item {idx} debe ser un entero mayor a cero.")
            if not _es_entero_valido(cant_aprob, minimo=0):
                raise CantidadInvalidaError(
                    f"cantidad_aprobada en item {idx} debe ser un entero mayor o igual a cero."
                )

            if det_id not in detalles_map:
                raise SolicitudInvalidaError(
                    f"El detalle #{det_id} no pertenece a la solicitud #{id_solicitud}."
                )
            if det_id in ids_vistos:
                raise SolicitudInvalidaError(f"El detalle #{det_id} está duplicado en detalles_aprobados.")
            ids_vistos.add(det_id)

            d_orig = detalles_map[det_id]
            if cant_aprob > d_orig.cantidad_solicitada:
                raise SolicitudInvalidaError(
                    f"La cantidad aprobada ({cant_aprob}) en el detalle #{det_id} no puede superar "
                    f"la cantidad solicitada ({d_orig.cantidad_solicitada})."
                )
            cantidades_map[det_id] = cant_aprob

    if sum(cantidades_map.values()) == 0:
        raise SolicitudSinCantidadAprobadaError(
            "No se puede aprobar la solicitud con una cantidad total aprobada de 0. "
            "Para rechazar una solicitud completa, utilice el flujo de rechazo."
        )

    if sol.tipo_solicitud == SOLICITUD_TIPO_REPOSICION:
        # Construir items a asignar
        items_a_asignar = []
        for d in detalles_qs:
            cant_aprob = cantidades_map[d.id_detalle_solicitud]
            d.cantidad_aprobada = cant_aprob
            d.save(update_fields=['cantidad_aprobada'])
            if cant_aprob > 0:
                items_a_asignar.append({
                    'id_medicamento': d.id_medicamento_id,
                    'cantidad': cant_aprob,
                })

        # Construir motivo obligatorio para asignar_stock_a_kit
        motivo_asignacion = f"Reposición aprobada (solicitud #{id_solicitud})"
        if observaciones_gestor and observaciones_gestor.strip():
            motivo_asignacion += f" - {observaciones_gestor.strip()}"

        # Delegar dotación en kit_service (adquiere Lock 2 inventarios y Lock 3 kit_veterinario_lotes)
        res_asignacion = kit_service.asignar_stock_a_kit(
            id_gestor=id_gestor,
            id_veterinario=sol.id_veterinario_id,
            items=items_a_asignar,
            motivo=motivo_asignacion,
            id_solicitud=sol.id_solicitud,
        )

        ahora = timezone.now()
        es_parcial = any(d.cantidad_aprobada < d.cantidad_solicitada for d in detalles_qs)
        sol.estado = SOLICITUD_ESTADO_PARCIAL if es_parcial else SOLICITUD_ESTADO_APROBADA
        sol.id_gestor_id = id_gestor
        sol.fecha_resolucion = ahora
        if observaciones_gestor is not None:
            sol.observaciones_gestor = observaciones_gestor.strip()
        sol.save(update_fields=['estado', 'id_gestor', 'fecha_resolucion', 'observaciones_gestor'])

        return {
            'id_solicitud': sol.id_solicitud,
            'tipo_solicitud': sol.tipo_solicitud,
            'estado': sol.estado,
            'id_veterinario': sol.id_veterinario_id,
            'id_gestor': sol.id_gestor_id,
            'fecha_resolucion': sol.fecha_resolucion,
            'observaciones_gestor': sol.observaciones_gestor,
            'detalles': [
                {
                    'id_detalle_solicitud': d.id_detalle_solicitud,
                    'id_medicamento': d.id_medicamento_id,
                    'cantidad_solicitada': d.cantidad_solicitada,
                    'cantidad_aprobada': d.cantidad_aprobada,
                    'es_vencido': d.es_vencido,
                }
                for d in detalles_qs
            ],
            'asignacion': res_asignacion,
        }

    elif sol.tipo_solicitud == SOLICITUD_TIPO_DEVOLUCION:
        # Preparar lista de aprobaciones para kit_service.procesar_devolucion_aprobada
        aprobaciones_devolucion = [
            {
                'id_detalle_solicitud': d.id_detalle_solicitud,
                'cantidad_aprobada': cantidades_map[d.id_detalle_solicitud],
            }
            for d in detalles_qs
        ]

        # procesar_devolucion_aprobada ya actualiza detalles, cabecera (APROBADA/PARCIAL) y kardex.
        # No se vuelve a guardar la cabecera aquí para evitar pisar su estado.
        return kit_service.procesar_devolucion_aprobada(
            id_gestor=id_gestor,
            id_solicitud=sol.id_solicitud,
            aprobaciones=aprobaciones_devolucion,
            observaciones_gestor=observaciones_gestor,
        )

    else:
        raise SolicitudInvalidaError(f"Tipo de solicitud no reconocido: {sol.tipo_solicitud}")


@transaction.atomic
def rechazar_solicitud(
    id_gestor: int,
    id_solicitud: int,
    observaciones_gestor: str,
) -> Dict[str, Any]:
    """
    Rechaza una solicitud de kit pendiente.

    Parámetros:
    - id_gestor: ID del usuario gestor.
    - id_solicitud: ID de la solicitud a rechazar.
    - observaciones_gestor: Justificación obligatoria y no vacía del rechazo.
    """
    _validar_gestor(id_gestor)

    if not isinstance(observaciones_gestor, str) or not observaciones_gestor.strip():
        raise SolicitudInvalidaError("observaciones_gestor es obligatorio y no puede estar vacío.")

    if not _es_entero_valido(id_solicitud, minimo=1):
        raise CantidadInvalidaError("id_solicitud debe ser un entero mayor a cero.")

    # Lock 1: solicitudes_kit(no_key=True) por id_solicitud
    sol = SolicitudesKit.objects.select_for_update(no_key=True).filter(pk=id_solicitud).first()
    if not sol:
        raise SolicitudNoEncontradaError(f"La solicitud #{id_solicitud} no existe.")

    if sol.estado != SOLICITUD_ESTADO_PENDIENTE:
        raise SolicitudEstadoInvalidaError(
            f"La solicitud #{id_solicitud} no está en estado {SOLICITUD_ESTADO_PENDIENTE} "
            f"(estado actual: {sol.estado})."
        )

    # Bloquear y actualizar detalles a cantidad_aprobada = 0
    detalles = list(sol.detalles.select_for_update().order_by('id_detalle_solicitud'))
    for d in detalles:
        d.cantidad_aprobada = 0
        d.save(update_fields=['cantidad_aprobada'])

    ahora = timezone.now()
    sol.estado = SOLICITUD_ESTADO_RECHAZADA
    sol.id_gestor_id = id_gestor
    sol.fecha_resolucion = ahora
    sol.observaciones_gestor = observaciones_gestor.strip()
    sol.save(update_fields=['estado', 'id_gestor', 'fecha_resolucion', 'observaciones_gestor'])

    return {
        'id_solicitud': sol.id_solicitud,
        'tipo_solicitud': sol.tipo_solicitud,
        'estado': sol.estado,
        'id_veterinario': sol.id_veterinario_id,
        'id_gestor': sol.id_gestor_id,
        'fecha_resolucion': sol.fecha_resolucion,
        'observaciones_gestor': sol.observaciones_gestor,
        'detalles': [
            {
                'id_detalle_solicitud': d.id_detalle_solicitud,
                'id_medicamento': d.id_medicamento_id,
                'cantidad_solicitada': d.cantidad_solicitada,
                'cantidad_aprobada': d.cantidad_aprobada,
                'es_vencido': d.es_vencido,
            }
            for d in detalles
        ],
    }


@transaction.atomic
def cancelar_solicitud(
    id_veterinario: int,
    id_solicitud: int,
) -> Dict[str, Any]:
    """
    Cancela una solicitud de kit propia por parte del veterinario solicitante.

    Parámetros:
    - id_veterinario: ID del veterinario autenticado.
    - id_solicitud: ID de la solicitud a cancelar.
    """
    _validar_veterinario(id_veterinario)

    if not _es_entero_valido(id_solicitud, minimo=1):
        raise CantidadInvalidaError("id_solicitud debe ser un entero mayor a cero.")

    # Lock 1: solicitudes_kit(no_key=True) por id_solicitud
    sol = SolicitudesKit.objects.select_for_update(no_key=True).filter(pk=id_solicitud).first()
    if not sol:
        raise SolicitudNoEncontradaError(f"La solicitud #{id_solicitud} no existe.")

    if sol.id_veterinario_id != id_veterinario:
        raise SolicitudPermisoError(
            f"La solicitud #{id_solicitud} no pertenece al veterinario #{id_veterinario}."
        )

    if sol.estado != SOLICITUD_ESTADO_PENDIENTE:
        raise SolicitudEstadoInvalidaError(
            f"La solicitud #{id_solicitud} no está en estado {SOLICITUD_ESTADO_PENDIENTE} "
            f"(estado actual: {sol.estado})."
        )

    ahora = timezone.now()
    sol.estado = SOLICITUD_ESTADO_CANCELADA
    sol.fecha_resolucion = ahora
    sol.save(update_fields=['estado', 'fecha_resolucion'])

    detalles = list(sol.detalles.all().order_by('id_detalle_solicitud'))

    return {
        'id_solicitud': sol.id_solicitud,
        'tipo_solicitud': sol.tipo_solicitud,
        'estado': sol.estado,
        'id_veterinario': sol.id_veterinario_id,
        'fecha_resolucion': sol.fecha_resolucion,
        'detalles': [
            {
                'id_detalle_solicitud': d.id_detalle_solicitud,
                'id_medicamento': d.id_medicamento_id,
                'cantidad_solicitada': d.cantidad_solicitada,
                'cantidad_aprobada': d.cantidad_aprobada,
                'es_vencido': d.es_vencido,
            }
            for d in detalles
        ],
    }
