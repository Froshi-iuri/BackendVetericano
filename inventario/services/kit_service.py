"""
Servicio del Kit de Insumos y Medicamentos por Veterinario.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.

Implementa las operaciones nucleares del inventario en dos niveles:
1. asignar_stock_a_kit: Bodega General CBA -> Kit Veterinario.
2. retirar_stock_de_kit: Kit Veterinario -> Bodega General CBA (retiro directo por gestor).
3. procesar_devolucion_aprobada: Kit Veterinario -> Bodega General CBA (resolución de solicitud).

Normativa de concurrencia y seguridad:
- Orden global estricto de locks (R12 / Anti-deadlock):
    solicitudes_kit (PK ASC) -> inventarios (PK ASC) -> kit_veterinario_lotes (PK ASC).
- Unificación de bloqueos: resolución previa de todos los candidatos del payload y bloqueo
  en una única consulta select_for_update().filter(pk__in=...).order_by('id_inventario').
- Revalidación pesimista post-lock contra la fila bloqueada en 'inventarios'.
- Kardex inmutable granular: una fila por lote físico individual (inventario_kardex.id_inventario NOT NULL).
- Lotes vencidos en kit: baja automática con tipo 'BAJA_VENCIMIENTO' en 'KIT_VETERINARIO' (no reingresan a bodega).
"""

from datetime import datetime, timezone as dt_tz
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from inventario.constants import (
    INVENTARIO_GESTOR_ROLES,
    ROL_VETERINARIO,
    TIPO_KARDEX_ASIGNACION_KIT,
    TIPO_KARDEX_DEVOLUCION_APROBADA,
    TIPO_KARDEX_RETIRO_GESTOR,
    TIPO_KARDEX_BAJA_VENCIMIENTO,
    UBICACION_KARDEX_GENERAL,
    UBICACION_KARDEX_KIT,
    SOLICITUD_TIPO_REPOSICION,
    SOLICITUD_TIPO_DEVOLUCION,
    SOLICITUD_ESTADO_PENDIENTE,
    SOLICITUD_ESTADO_APROBADA,
    SOLICITUD_ESTADO_PARCIAL,
)
from inventario.models import (
    Inventarios,
    VInventarioGeneralLotes,
    KitVeterinarioLotes,
    SolicitudesKit,
    InventarioKardex,
)
from medicamentos.models import Medicamentos
from users.models import Usuarios
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
)


def _es_entero_valido(val, minimo: int | None = None) -> bool:
    """
    Verifica que el valor sea un entero estricto (excluyendo bool)
    y que cumpla con el valor mínimo requerido si se especifica.
    """
    if isinstance(val, bool) or not isinstance(val, int):
        return False
    if minimo is not None and val < minimo:
        return False
    return True


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


def _validar_veterinario(id_veterinario: int) -> Usuarios:
    """Verifica que el usuario exista, esté activo y cuente con rol institucional de Veterinario."""
    if not _es_entero_valido(id_veterinario, minimo=1):
        raise CantidadInvalidaError("id_veterinario debe ser un entero mayor a cero.")
    vet = Usuarios.objects.filter(pk=id_veterinario).first()
    if not vet or not vet.activo or vet.id_rol_id != ROL_VETERINARIO:
        raise UsuarioNoVeterinarioError(
            f"El usuario #{id_veterinario} no existe, está inactivo o no posee el rol institucional de "
            f"Veterinario (rol requerido: {ROL_VETERINARIO})."
        )
    return vet


def _obtener_fecha_hoy():
    """
    Obtiene la fecha actual de forma segura tanto con USE_TZ=False como con USE_TZ=True.
    En este proyecto USE_TZ=False en settings.py, por lo que obtiene date() directo
    cuando timezone.now() devuelve un datetime naive.
    """
    ahora = timezone.now()
    return timezone.localdate(ahora) if timezone.is_aware(ahora) else ahora.date()


def _normalizar_fecha_compra(fecha_compra) -> datetime:
    """
    Normaliza fecha_compra (datetime aware, datetime naive, date o None) a datetime naive
    para comparaciones deterministas en Python sin riesgo de TypeError.
    """
    if fecha_compra is None:
        return datetime.min
    if isinstance(fecha_compra, datetime):
        if timezone.is_aware(fecha_compra):
            return fecha_compra.astimezone(dt_tz.utc).replace(tzinfo=None)
        return fecha_compra
    return datetime(fecha_compra.year, fecha_compra.month, fecha_compra.day)


def _sort_key_fifo_inverso(fecha_compra, id_inventario: int):
    """
    Clave para ordenar lotes en FIFO Inverso: fecha_compra DESC NULLS LAST, id_inventario DESC.
    Usada con sort(reverse=True) o max().
    - has_date=1 coloca fechas reales antes de NULLs (has_date=0), logrando NULLS LAST al invertir.
    - date_val ordena fechas más recientes primero.
    - id_inventario desempata con el ID mayor primero.
    """
    has_date = 1 if fecha_compra is not None else 0
    date_val = _normalizar_fecha_compra(fecha_compra)
    return (has_date, date_val, id_inventario)



@transaction.atomic
def asignar_stock_a_kit(
    id_gestor: int,
    id_veterinario: int,
    items: list[dict],
    motivo: str,
    id_solicitud: int | None = None,
) -> list[dict]:
    """
    Asigna unidades de medicamentos desde Bodega General CBA hacia el kit personal del veterinario.

    Parámetros:
    - id_gestor: ID del usuario con rol de gestor que realiza la asignación.
    - id_veterinario: ID del veterinario receptor del stock.
    - items: Lista de diccionarios, cada uno con:
        * 'id_medicamento' (int): Fármaco a asignar.
        * 'cantidad' (int > 0): Unidades a asignar.
        * 'fijar_cantidad_referencia' (int > 0, opcional): Fija explícitamente el nivel de referencia.
        * 'incrementar_cantidad_referencia' (bool, opcional): Suma la cantidad asignada a la referencia previa.
    - motivo: Razón o justificativo de la dotación.
    - id_solicitud: ID opcional de SolicitudesKit (tipo REPOSICION) que da origen a la asignación.

    Garantías de concurrencia y reglas de negocio:
    - Bloqueo unificado previo de todos los lotes candidatos en una sola consulta ordenada por PK ASC.
    - Revalidación del stock y vigencia directamente con la fila bloqueada de 'inventarios'.
    - Lote en bodega general que queda en 0 pasa a 'Agotado'.
    - Genera exactamente dos filas de kardex ('GENERAL' y 'KIT_VETERINARIO') por cada lote físico tocado.
    - Actualización explícita de actualizado_el = timezone.now() en kit_veterinario_lotes.
    """
    # 1. Validaciones iniciales de actores y parámetros
    _validar_gestor(id_gestor)
    _validar_veterinario(id_veterinario)

    if not items or not isinstance(items, list):
        raise CantidadInvalidaError("La lista de medicamentos a asignar no puede estar vacía.")

    if not motivo or not motivo.strip():
        raise CantidadInvalidaError("Debe indicar un motivo no vacío para la asignación de stock.")

    # Validar estructura y reglas de cada ítem
    med_ids_vistos = set()
    for item in items:
        med_id = item.get('id_medicamento')
        cantidad = item.get('cantidad')
        fijar_ref = item.get('fijar_cantidad_referencia')
        incr_ref = item.get('incrementar_cantidad_referencia', False)

        if not _es_entero_valido(med_id, minimo=1):
            raise CantidadInvalidaError("id_medicamento debe ser un entero mayor a cero.")

        if med_id in med_ids_vistos:
            raise CantidadInvalidaError(f"El medicamento #{med_id} está duplicado en la lista de asignación.")
        med_ids_vistos.add(med_id)

        if not _es_entero_valido(cantidad, minimo=1):
            raise CantidadInvalidaError(
                f"La cantidad a asignar para el medicamento #{med_id} debe ser un entero mayor a cero."
            )

        if fijar_ref is not None:
            if not _es_entero_valido(fijar_ref, minimo=1):
                raise CantidadInvalidaError(
                    f"fijar_cantidad_referencia para el medicamento #{med_id} debe ser un entero mayor a cero."
                )

        if not isinstance(incr_ref, bool):
            raise CantidadInvalidaError(
                f"incrementar_cantidad_referencia para el medicamento #{med_id} debe ser un valor booleano."
            )

        if fijar_ref is not None and incr_ref:
            raise CantidadInvalidaError(
                f"No se puede fijar e incrementar la cantidad de referencia simultáneamente en el medicamento #{med_id}."
            )

        # Regla 6: Medicamento activo se exige estrictamente SOLO en asignación
        med_obj = Medicamentos.objects.filter(pk=med_id).first()
        if not med_obj:
            raise MedicamentoInvalidoError(f"El medicamento #{med_id} no existe en el catálogo institucional.")
        if not med_obj.activo:
            raise MedicamentoInvalidoError(
                f"El medicamento #{med_id} ({med_obj.nombre}) se encuentra desactivado. "
                "No se permite asignar stock de fármacos descontinuados."
            )

    # 2. Orden Global de Locks 1: solicitudes_kit (si aplica, FOR NO KEY UPDATE)
    solicitud_obj = None
    if id_solicitud is not None:
        if not _es_entero_valido(id_solicitud, minimo=1):
            raise CantidadInvalidaError("id_solicitud debe ser un entero mayor a cero.")
        solicitud_obj = SolicitudesKit.objects.select_for_update(no_key=True).filter(pk=id_solicitud).first()
        if not solicitud_obj:
            raise SolicitudInvalidaError(f"La solicitud de kit #{id_solicitud} no existe.")
        if solicitud_obj.id_veterinario_id != id_veterinario:
            raise SolicitudInvalidaError(
                f"La solicitud #{id_solicitud} pertenece al veterinario #{solicitud_obj.id_veterinario_id}, "
                f"no al veterinario #{id_veterinario} especificado."
            )
        if solicitud_obj.tipo_solicitud != SOLICITUD_TIPO_REPOSICION:
            raise SolicitudInvalidaError(
                f"La solicitud #{id_solicitud} no es de tipo {SOLICITUD_TIPO_REPOSICION} "
                f"(tipo actual: {solicitud_obj.tipo_solicitud})."
            )
        if solicitud_obj.estado != SOLICITUD_ESTADO_PENDIENTE:
            raise SolicitudInvalidaError(
                f"La solicitud #{id_solicitud} no está en estado {SOLICITUD_ESTADO_PENDIENTE} "
                f"(estado actual: {solicitud_obj.estado})."
            )

    # 3. Proponer candidatos por medicamento usando la vista SQL (FIFO Directo)
    candidatos_por_med = {}
    todos_los_id_inventario = []

    for item in items:
        med_id = item['id_medicamento']
        candidatos_vista = list(
            VInventarioGeneralLotes.objects.filter(
                id_medicamento=med_id,
                disponible_para_asignar=True
            ).order_by(F('fecha_compra').asc(nulls_first=True), 'id_inventario')
        )
        if not candidatos_vista:
            med_obj = Medicamentos.objects.get(pk=med_id)
            raise MedicamentoSinLotesAsignablesError(
                f"No hay lotes disponibles ni vigentes en bodega general para el medicamento #{med_id} ({med_obj.nombre})."
            )

        candidatos_por_med[med_id] = candidatos_vista
        todos_los_id_inventario.extend([c.id_inventario for c in candidatos_vista])

    # 4. Resolver y consolidar inventarios de bodega y de kit para bloqueo unificado
    med_ids_con_fijar = [
        item['id_medicamento'] for item in items if item.get('fijar_cantidad_referencia') is not None
    ]
    if id_solicitud is not None:
        med_ids_con_fijar = sorted(set(med_ids_con_fijar + [item['id_medicamento'] for item in items]))

    # Para cualquier medicamento con fijar_cantidad_referencia o con solicitud: incluir todos los lotes que el vet ya tenga en su kit
    inv_ids_kit_fijar = list(
        KitVeterinarioLotes.objects.filter(
            id_veterinario=id_veterinario,
            id_medicamento__in=med_ids_con_fijar
        ).values_list('id_inventario_id', flat=True)
    )
    todos_los_id_inventario = sorted(set(todos_los_id_inventario + inv_ids_kit_fijar))

    # Orden Global de Locks 2: inventarios (PK ASC en una sola consulta unificada, FOR NO KEY UPDATE)
    inventarios_bloqueados = list(
        Inventarios.objects.select_for_update(no_key=True).filter(
            pk__in=todos_los_id_inventario
        ).order_by('id_inventario')
    )
    inventarios_map = {inv.id_inventario: inv for inv in inventarios_bloqueados}

    # Orden Global de Locks 3: kit_veterinario_lotes (PK ASC en una sola consulta)
    # Carga y bloquea con select_for_update TODOS los kit_veterinario_lotes del veterinario
    # para cualquier medicamento con fijar_cantidad_referencia o con solicitud (sin filtrar por cantidad ni candidatos),
    # además de los que correspondan a los candidatos de bodega a asignar:
    kit_lotes_bloqueados = list(
        KitVeterinarioLotes.objects.select_for_update().filter(
            id_veterinario=id_veterinario
        ).filter(
            Q(id_medicamento__in=med_ids_con_fijar) | Q(id_inventario__in=todos_los_id_inventario)
        ).order_by('id_kit_lote')
    )
    kit_lotes_map = {kl.id_inventario_id: kl for kl in kit_lotes_bloqueados}

    # Captura de referencia total previa bajo bloqueo para conservación de referencia en solicitudes (A3)
    ref_previa_por_med = {
        med_id: sum(kl.cantidad_referencia for kl in kit_lotes_bloqueados if kl.id_medicamento_id == med_id)
        for med_id in med_ids_con_fijar
    }

    faltantes_asig = [
        kl.id_inventario_id for kl in kit_lotes_bloqueados if kl.id_inventario_id not in inventarios_map
    ]
    if faltantes_asig:
        raise InventarioDomainError(
            f"Conflicto de concurrencia al asignar stock: se detectaron lotes de kit creados concurrentemente "
            f"({faltantes_asig}) que no contaban con bloqueo pesimista en inventarios. Por favor, reintente la operación.",
            codigo="CONFLICTO_CONCURRENCIA_REINTENTO",
            extra={'lotes_faltantes': faltantes_asig}
        )

    # 6. Revalidación pesimista con la fila bloqueada de Inventarios (Regla 2)
    hoy = _obtener_fecha_hoy()
    ahora = timezone.now()
    plan_asignaciones = {}

    for item in items:
        med_id = item['id_medicamento']
        cantidad_pedida = item['cantidad']
        restante = cantidad_pedida
        candidatos_vista = candidatos_por_med[med_id]
        asignaciones_med = []

        for cand in candidatos_vista:
            inv_row = inventarios_map.get(cand.id_inventario)
            if not inv_row:
                continue

            # Revalidar con los datos reales bloqueados de la tabla 'inventarios'
            if inv_row.cantidad_actual <= 0:
                continue
            if inv_row.estado not in ('Disponible', None):
                continue
            if inv_row.fecha_vencimiento is not None and inv_row.fecha_vencimiento < hoy:
                continue

            a_tomar = min(inv_row.cantidad_actual, restante)
            if a_tomar > 0:
                asignaciones_med.append({
                    'inv': inv_row,
                    'fecha_compra': cand.fecha_compra,
                    'a_tomar': a_tomar,
                })
                restante -= a_tomar

            if restante == 0:
                break

        if restante > 0:
            med_obj = Medicamentos.objects.get(pk=med_id)
            disponible_real = cantidad_pedida - restante
            raise StockInsuficienteBodegaError(
                f"Stock insuficiente en bodega general para el medicamento #{med_id} ({med_obj.nombre}). "
                f"Solicitado: {cantidad_pedida}, disponible real: {disponible_real} unidades."
            )

        plan_asignaciones[med_id] = asignaciones_med

    # 7. Ejecutar modificaciones y registrar kardex granular
    resultado = []

    for item in items:
        med_id = item['id_medicamento']
        fijar_ref = item.get('fijar_cantidad_referencia')
        incr_ref = item.get('incrementar_cantidad_referencia', False)
        asignaciones_med = plan_asignaciones[med_id]

        lotes_resultado = []
        lotes_tocados = []
        med_obj = Medicamentos.objects.get(pk=med_id)

        for idx, asig in enumerate(asignaciones_med):
            inv = asig['inv']
            a_tomar = asig['a_tomar']
            fecha_compra = asig['fecha_compra']

            # Descontar en Bodega General
            inv_saldo_anterior = inv.cantidad_actual
            inv.cantidad_actual -= a_tomar
            inv.estado = 'Agotado' if inv.cantidad_actual == 0 else 'Disponible'
            inv.save(update_fields=['cantidad_actual', 'estado'])

            # Dotar en Kit Veterinario
            kl = kit_lotes_map.get(inv.id_inventario)
            if kl is None:
                # Lote nuevo en el kit
                if incr_ref:
                    nueva_ref = a_tomar
                else:
                    # Regla por defecto: lote nuevo => referencia = cantidad asignada
                    nueva_ref = a_tomar

                kl = KitVeterinarioLotes.objects.create(
                    id_veterinario_id=id_veterinario,
                    id_inventario=inv,
                    id_medicamento_id=med_id,
                    cantidad_actual=a_tomar,
                    cantidad_referencia=nueva_ref,
                    fecha_vencimiento=inv.fecha_vencimiento,
                    fecha_compra_origen=fecha_compra,
                    actualizado_el=ahora,
                )
                kit_lotes_map[inv.id_inventario] = kl
                kit_lotes_bloqueados.append(kl)
                kit_saldo_anterior = 0
            else:
                # Lote existente en el kit
                kit_saldo_anterior = kl.cantidad_actual
                kl.cantidad_actual += a_tomar

                if incr_ref:
                    kl.cantidad_referencia += a_tomar
                else:
                    # Regla por defecto: se conserva referencia previa salvo que nuevo cantidad_actual la supere
                    kl.cantidad_referencia = max(kl.cantidad_referencia, kl.cantidad_actual)

                kl.actualizado_el = ahora
                kl.save()

            lotes_tocados.append(kl)

            # Registrar Kardex: Fila 1 - GENERAL (Descuento)
            InventarioKardex.objects.create(
                tipo_movimiento=TIPO_KARDEX_ASIGNACION_KIT,
                ubicacion=UBICACION_KARDEX_GENERAL,
                id_usuario_ejecuta_id=id_gestor,
                id_veterinario_id=id_veterinario,
                id_medicamento_id=med_id,
                id_inventario=inv,
                cantidad=a_tomar,
                saldo_anterior=inv_saldo_anterior,
                saldo_resultante=inv.cantidad_actual,
                motivo=motivo,
                id_solicitud_kit=solicitud_obj,
            )

            # Registrar Kardex: Fila 2 - KIT_VETERINARIO (Ingreso)
            InventarioKardex.objects.create(
                tipo_movimiento=TIPO_KARDEX_ASIGNACION_KIT,
                ubicacion=UBICACION_KARDEX_KIT,
                id_usuario_ejecuta_id=id_gestor,
                id_veterinario_id=id_veterinario,
                id_medicamento_id=med_id,
                id_inventario=inv,
                cantidad=a_tomar,
                saldo_anterior=kit_saldo_anterior,
                saldo_resultante=kl.cantidad_actual,
                motivo=motivo,
                id_solicitud_kit=solicitud_obj,
            )

            lotes_resultado.append({
                'id_kit_lote': kl.id_kit_lote,
                'id_inventario': inv.id_inventario,
                'cantidad_asignada': a_tomar,
                'cantidad_actual_kit': kl.cantidad_actual,
                'cantidad_referencia_kit': kl.cantidad_referencia,
                'fecha_vencimiento': kl.fecha_vencimiento,
                'fecha_compra_origen': kl.fecha_compra_origen,
                'stock_restante_bodega': inv.cantidad_actual,
            })

        # Si fijar_cantidad_referencia está activo o proviene de solicitud, ajustar referencias del medicamento (A2 y A3)
        if fijar_ref is not None or id_solicitud is not None:
            # Conjunto completo de lotes del medicamento en el kit (sin filtrar por cantidad)
            todos_lotes_med = [
                kl for kl in kit_lotes_bloqueados
                if kl.id_medicamento_id == med_id
            ]
            # Decisión 3.A: el piso de referencia aplica solo a lotes vigentes (fecha_vencimiento is None o >= hoy)
            suma_stock_vigente = sum(
                l.cantidad_actual for l in todos_lotes_med
                if l.fecha_vencimiento is None or l.fecha_vencimiento >= hoy
            )

            if fijar_ref is not None:
                N = fijar_ref
                if N < suma_stock_vigente:
                    raise CantidadInvalidaError(
                        f"fijar_cantidad_referencia ({N}) no puede ser menor a la suma de stock vigente "
                        f"del medicamento #{med_id} ({med_obj.nombre}) en el kit tras la asignación ({suma_stock_vigente}) "
                        f"para mantener el invariante cantidad_referencia >= cantidad_actual en lotes vigentes."
                    )
            else:
                # id_solicitud is not None: conservar referencia total previa
                N = max(ref_previa_por_med.get(med_id, 0), suma_stock_vigente)

            # Redistribución (Decisión 3.A): lotes vencidos quedan con cantidad_referencia = 0,
            # y lotes vigentes con cantidad_referencia = cantidad_actual.
            for l in todos_lotes_med:
                if l.fecha_vencimiento is not None and l.fecha_vencimiento < hoy:
                    l.cantidad_referencia = 0
                else:
                    l.cantidad_referencia = l.cantidad_actual

            # Remanente = N - suma_stock_vigente se suma íntegro al lote más reciente tocado por la asignación
            remanente = N - suma_stock_vigente
            lote_mas_reciente = max(
                lotes_tocados,
                key=lambda l: _sort_key_fifo_inverso(l.fecha_compra_origen, l.id_inventario_id)
            )
            lote_mas_reciente.cantidad_referencia += remanente

            for l in todos_lotes_med:
                l.actualizado_el = ahora
                l.save(update_fields=['cantidad_referencia', 'actualizado_el'])

            # Sincronizar en lotes_resultado
            ref_map = {l.id_kit_lote: l.cantidad_referencia for l in todos_lotes_med}
            for lr in lotes_resultado:
                if lr['id_kit_lote'] in ref_map:
                    lr['cantidad_referencia_kit'] = ref_map[lr['id_kit_lote']]


        resultado.append({
            'id_medicamento': med_id,
            'nombre_medicamento': med_obj.nombre,
            'cantidad_total_asignada': item['cantidad'],
            'lotes_asignados': lotes_resultado,
        })

    return resultado


@transaction.atomic
def retirar_stock_de_kit(
    id_gestor: int,
    id_veterinario: int,
    id_medicamento: int,
    cantidad: int,
    motivo: str,
) -> dict:
    """
    Retira unidades de un medicamento desde el kit del veterinario y las reintegra a Bodega General.

    Reglas de negocio (Regla 2 y Regla Única de Vencidos):
    - Bloqueo global estricto: inventarios (PK ASC) -> kit_veterinario_lotes (PK ASC),
      abarcando todos los lotes del veterinario para ese medicamento sin lectura previa sin lock.
    - Los lotes vencidos (fecha_vencimiento < hoy) se dan de baja PRIMERO y CUENTAN DENTRO
      de la cantidad N a retirar (1 fila BAJA_VENCIMIENTO en KIT_VETERINARIO por lote, no reingresan a bodega).
      Solo se dan de baja hasta cubrir N; no se destruyen vencidos que excedan N.
    - Si N supera el total de vencidos, el resto sale de lotes vigentes por FIFO Inverso
      (fecha_compra_origen DESC NULLS LAST, id_inventario DESC) con 2 filas de kardex por lote tocado
      (RETIRO_GESTOR en KIT_VETERINARIO y GENERAL).
    - Los lotes vigentes reintegrados a bodega general actualizan su estado a 'Disponible'.
    - Medicamento inactivo: SE PERMITE el retiro (un fármaco desactivado debe poder salir del kit).
    - La respuesta separa unidades_baja_vencimiento y unidades_reintegradas_bodega.
    """
    # 1. Validaciones de actores y parámetros
    _validar_gestor(id_gestor)
    _validar_veterinario(id_veterinario)

    if not _es_entero_valido(cantidad, minimo=1):
        raise CantidadInvalidaError("La cantidad a retirar debe ser un entero mayor a cero.")

    if not motivo or not motivo.strip():
        raise CantidadInvalidaError("Debe indicar un motivo no vacío para el retiro de stock.")

    if not _es_entero_valido(id_medicamento, minimo=1):
        raise CantidadInvalidaError("id_medicamento debe ser un entero mayor a cero.")

    # Regla 6: No se exige med.activo=True
    med_obj = Medicamentos.objects.filter(pk=id_medicamento).first()
    if not med_obj:
        raise MedicamentoInvalidoError(f"El medicamento #{id_medicamento} no existe en el catálogo institucional.")

    # 2. Orden Global de Locks 2: inventarios (PK ASC, FOR NO KEY UPDATE) para TODOS los lotes del vet para este med
    inv_ids_retiro = sorted(set(
        KitVeterinarioLotes.objects.filter(
            id_veterinario=id_veterinario,
            id_medicamento=id_medicamento
        ).values_list('id_inventario_id', flat=True)
    ))

    inventarios_bloqueados = list(
        Inventarios.objects.select_for_update(no_key=True).filter(
            pk__in=inv_ids_retiro
        ).order_by('id_inventario')
    )
    inventarios_map = {inv.id_inventario: inv for inv in inventarios_bloqueados}

    # 3. Orden Global de Locks 3: kit_veterinario_lotes (PK ASC)
    kit_lotes_bloqueados = list(
        KitVeterinarioLotes.objects.select_for_update().filter(
            id_veterinario=id_veterinario,
            id_medicamento=id_medicamento
        ).order_by('id_kit_lote')
    )

    if not kit_lotes_bloqueados:
        raise StockInsuficienteKitError(
            f"El veterinario #{id_veterinario} no cuenta con ningún lote registrado para el medicamento #{id_medicamento} "
            f"({med_obj.nombre}) en su kit."
        )

    # Si tras bloquear aparece un kit_veterinario_lotes con id_inventario que no está en inventarios_map
    faltantes = [
        kl.id_inventario_id for kl in kit_lotes_bloqueados if kl.id_inventario_id not in inventarios_map
    ]
    if faltantes:
        raise InventarioDomainError(
            f"Conflicto de concurrencia al retirar stock del medicamento #{id_medicamento} ({med_obj.nombre}): "
            f"se detectaron lotes en el kit creados concurrentemente ({faltantes}) que no contaban con bloqueo "
            f"pesimista en inventarios. Por favor, reintente la operación.",
            codigo="CONFLICTO_CONCURRENCIA_REINTENTO",
            extra={'id_medicamento': id_medicamento, 'lotes_faltantes': faltantes}
        )

    # 4. Revalidar stock total disponible (vencido + vigente) en el kit
    hoy = _obtener_fecha_hoy()
    ahora = timezone.now()

    lotes_con_stock = [kl for kl in kit_lotes_bloqueados if kl.cantidad_actual > 0]
    total_stock = sum(kl.cantidad_actual for kl in lotes_con_stock)
    if total_stock < cantidad:
        raise StockInsuficienteKitError(
            f"Stock total insuficiente en el kit del veterinario #{id_veterinario} para retirar {cantidad} unidades "
            f"del medicamento #{id_medicamento} ({med_obj.nombre}). Saldo total en kit: {total_stock}."
        )

    # Separar vencidos y vigentes con stock
    lotes_vencidos = [
        kl for kl in lotes_con_stock
        if kl.fecha_vencimiento is not None and kl.fecha_vencimiento < hoy
    ]
    lotes_vigentes = [
        kl for kl in lotes_con_stock
        if kl.fecha_vencimiento is None or kl.fecha_vencimiento >= hoy
    ]

    # Ordenar vencidos por fecha de vencimiento ASC, id_kit_lote ASC
    lotes_vencidos.sort(key=lambda kl: (kl.fecha_vencimiento, kl.id_kit_lote))
    # Ordenar vigentes por FIFO Inverso
    lotes_vigentes.sort(
        key=lambda kl: _sort_key_fifo_inverso(kl.fecha_compra_origen, kl.id_inventario_id),
        reverse=True
    )

    restante = cantidad
    unidades_baja_vencimiento = 0
    bajas_vencimiento = []

    # 5. Regla única: los lotes vencidos se dan de baja PRIMERO y CUENTAN DENTRO de N (hasta cubrir N)
    for kl in lotes_vencidos:
        if restante == 0:
            break
        a_dar_baja = min(kl.cantidad_actual, restante)
        if a_dar_baja <= 0:
            continue

        saldo_anterior = kl.cantidad_actual
        kl.cantidad_actual -= a_dar_baja
        kl.actualizado_el = ahora
        kl.save()

        InventarioKardex.objects.create(
            tipo_movimiento=TIPO_KARDEX_BAJA_VENCIMIENTO,
            ubicacion=UBICACION_KARDEX_KIT,
            id_usuario_ejecuta_id=id_gestor,
            id_veterinario_id=id_veterinario,
            id_medicamento_id=id_medicamento,
            id_inventario_id=kl.id_inventario_id,
            cantidad=a_dar_baja,
            saldo_anterior=saldo_anterior,
            saldo_resultante=kl.cantidad_actual,
            motivo=f"Baja por vencimiento ({kl.fecha_vencimiento}) al retirar de kit: {motivo}",
        )

        unidades_baja_vencimiento += a_dar_baja
        bajas_vencimiento.append({
            'id_kit_lote': kl.id_kit_lote,
            'id_inventario': kl.id_inventario_id,
            'cantidad_baja': a_dar_baja,
            'fecha_vencimiento': kl.fecha_vencimiento,
        })
        restante -= a_dar_baja

    # 6. Si N supera los vencidos, el resto sale de vigentes por FIFO inverso hacia bodega
    unidades_reintegradas_bodega = 0
    lotes_reintegrados = []

    for kl in lotes_vigentes:
        if restante == 0:
            break
        a_retirar = min(kl.cantidad_actual, restante)
        if a_retirar <= 0:
            continue

        kit_saldo_anterior = kl.cantidad_actual
        kl.cantidad_actual -= a_retirar
        kl.actualizado_el = ahora
        kl.save()

        inv = inventarios_map.get(kl.id_inventario_id)
        if not inv:
            raise InventarioDomainError(
                f"Conflicto de concurrencia al reintegrar lote #{kl.id_inventario_id} a bodega: "
                f"el lote no se encuentra bloqueado en memoria. Por favor, reintente la operación.",
                codigo="CONFLICTO_CONCURRENCIA_REINTENTO"
            )
        inv_saldo_anterior = inv.cantidad_actual
        inv.cantidad_actual += a_retirar
        inv.estado = 'Disponible'  # Decisión 5: siempre pasa a Disponible al recibir stock
        inv.save(update_fields=['cantidad_actual', 'estado'])

        # Kardex Fila 1: KIT_VETERINARIO
        InventarioKardex.objects.create(
            tipo_movimiento=TIPO_KARDEX_RETIRO_GESTOR,
            ubicacion=UBICACION_KARDEX_KIT,
            id_usuario_ejecuta_id=id_gestor,
            id_veterinario_id=id_veterinario,
            id_medicamento_id=id_medicamento,
            id_inventario_id=kl.id_inventario_id,
            cantidad=a_retirar,
            saldo_anterior=kit_saldo_anterior,
            saldo_resultante=kl.cantidad_actual,
            motivo=motivo,
        )

        # Kardex Fila 2: GENERAL
        InventarioKardex.objects.create(
            tipo_movimiento=TIPO_KARDEX_RETIRO_GESTOR,
            ubicacion=UBICACION_KARDEX_GENERAL,
            id_usuario_ejecuta_id=id_gestor,
            id_veterinario_id=id_veterinario,
            id_medicamento_id=id_medicamento,
            id_inventario_id=kl.id_inventario_id,
            cantidad=a_retirar,
            saldo_anterior=inv_saldo_anterior,
            saldo_resultante=inv.cantidad_actual,
            motivo=motivo,
        )

        unidades_reintegradas_bodega += a_retirar
        lotes_reintegrados.append({
            'id_kit_lote': kl.id_kit_lote,
            'id_inventario': kl.id_inventario_id,
            'cantidad_retirada': a_retirar,
            'saldo_restante_kit': kl.cantidad_actual,
            'saldo_resultante_bodega': inv.cantidad_actual,
        })
        restante -= a_retirar

    return {
        'id_medicamento': id_medicamento,
        'nombre_medicamento': med_obj.nombre,
        'id_veterinario': id_veterinario,
        'cantidad_total_retirada': cantidad,
        'unidades_baja_vencimiento': unidades_baja_vencimiento,
        'unidades_reintegradas_bodega': unidades_reintegradas_bodega,
        'lotes_reintegrados_bodega': lotes_reintegrados,
        'bajas_vencimiento': bajas_vencimiento,
    }


@transaction.atomic
def procesar_devolucion_aprobada(
    id_gestor: int,
    id_solicitud: int,
    aprobaciones: list[dict],
    observaciones_gestor: str | None = None,
) -> dict:
    """
    Procesa la aprobación (total o parcial) de una solicitud de devolución de stock desde el kit a bodega.

    Reglas de negocio (Regla 5):
    - Bloquea primero la fila de solicitudes_kit (select_for_update).
    - Exige tipo_solicitud='DEVOLUCION' y estado='PENDIENTE' (de lo contrario, SolicitudInvalidaError).
    - Valida que cada id_detalle_solicitud pertenezca a la solicitud y que cantidad_aprobada <= cantidad_solicitada.
    - Si todas las cantidades aprobadas son 0, rechaza la operación con CantidadInvalidaError (flujo de rechazo).
    - Estado final de la solicitud: 'PARCIAL' si al menos una cantidad_aprobada < cantidad_solicitada;
      'APROBADA' si todas las cantidades aprobadas coinciden con las solicitadas.
    - Guarda id_gestor, fecha_resolucion = timezone.now() y observaciones_gestor.
    - Granularidad de Kardex: una fila BAJA_VENCIMIENTO por cada lote vencido; dos filas
      (KIT_VETERINARIO y GENERAL) por cada lote vigente devuelto.
    - Reintegro a bodega general actualiza el lote a estado 'Disponible'.
    """
    # 1. Validaciones iniciales
    _validar_gestor(id_gestor)

    if not _es_entero_valido(id_solicitud, minimo=1):
        raise CantidadInvalidaError("id_solicitud debe ser un entero mayor a cero.")

    if not aprobaciones or not isinstance(aprobaciones, list):
        raise CantidadInvalidaError("Debe proporcionar la lista de aprobaciones para la devolución.")

    # Si todas las cantidades aprobadas son 0, se rechaza la llamada (corresponde a flujo de rechazo)
    if all(item.get('cantidad_aprobada', 0) == 0 for item in aprobaciones):
        raise CantidadInvalidaError(
            "No se puede procesar una devolución con todas las cantidades aprobadas en cero. "
            "Para rechazar una solicitud completa, utilice el flujo de rechazo."
        )

    # 2. Orden Global de Locks 1: solicitudes_kit (PK ASC, FOR NO KEY UPDATE)
    solicitud = SolicitudesKit.objects.select_for_update(no_key=True).filter(pk=id_solicitud).first()
    if not solicitud:
        raise SolicitudInvalidaError(f"La solicitud de kit #{id_solicitud} no existe.")

    if solicitud.tipo_solicitud != SOLICITUD_TIPO_DEVOLUCION:
        raise SolicitudInvalidaError(
            f"La solicitud #{id_solicitud} no es de tipo DEVOLUCION (tipo actual: {solicitud.tipo_solicitud})."
        )

    if solicitud.estado != SOLICITUD_ESTADO_PENDIENTE:
        raise SolicitudInvalidaError(
            f"La solicitud #{id_solicitud} no está en estado PENDIENTE (estado actual: {solicitud.estado})."
        )

    # 3. Bloquear y mapear detalles de la solicitud
    detalles_qs = list(solicitud.detalles.select_for_update().order_by('id_detalle_solicitud'))
    detalles_map = {d.id_detalle_solicitud: d for d in detalles_qs}

    aprobaciones_map = {}
    for item in aprobaciones:
        det_id = item.get('id_detalle_solicitud')
        cant_aprob = item.get('cantidad_aprobada')

        if not _es_entero_valido(det_id, minimo=1):
            raise CantidadInvalidaError("id_detalle_solicitud debe ser un entero mayor a cero.")

        if det_id not in detalles_map:
            raise SolicitudInvalidaError(
                f"El detalle #{det_id} no pertenece a la solicitud de devolución #{id_solicitud}."
            )

        if not _es_entero_valido(cant_aprob, minimo=0):
            raise CantidadInvalidaError(
                f"La cantidad aprobada en el detalle #{det_id} debe ser un entero mayor o igual a cero."
            )

        det_obj = detalles_map[det_id]
        if cant_aprob > det_obj.cantidad_solicitada:
            raise CantidadInvalidaError(
                f"La cantidad aprobada ({cant_aprob}) supera la cantidad solicitada ({det_obj.cantidad_solicitada}) "
                f"en el detalle #{det_id}."
            )

        aprobaciones_map[det_id] = cant_aprob

    # Si algún detalle de la solicitud se omitió en la lista de aprobaciones, asume 0
    for det_id in detalles_map:
        if det_id not in aprobaciones_map:
            aprobaciones_map[det_id] = 0

    # 4. Resolver todos los id_inventario candidatos involucrados
    todos_los_id_inventario = set()
    med_ids_sin_origen = set()
    for d in detalles_qs:
        cant_aprob = aprobaciones_map[d.id_detalle_solicitud]
        if cant_aprob == 0:
            continue

        if d.id_inventario_origen_id is not None:
            todos_los_id_inventario.add(d.id_inventario_origen_id)
        else:
            med_ids_sin_origen.add(d.id_medicamento_id)
            # Buscar todos los lotes del veterinario para este medicamento
            lotes_vet_ids = KitVeterinarioLotes.objects.filter(
                id_veterinario=solicitud.id_veterinario_id,
                id_medicamento=d.id_medicamento_id
            ).values_list('id_inventario_id', flat=True)
            todos_los_id_inventario.update(lotes_vet_ids)

    todos_los_id_inventario = sorted(todos_los_id_inventario)

    # 5. Orden Global de Locks 2: inventarios (PK ASC en una consulta unificada, FOR NO KEY UPDATE)
    inventarios_bloqueados = list(
        Inventarios.objects.select_for_update(no_key=True).filter(
            pk__in=todos_los_id_inventario
        ).order_by('id_inventario')
    )
    inventarios_map = {inv.id_inventario: inv for inv in inventarios_bloqueados}

    # 6. Orden Global de Locks 3: kit_veterinario_lotes (PK ASC en una consulta unificada)
    kit_lotes_bloqueados = list(
        KitVeterinarioLotes.objects.select_for_update().filter(
            id_veterinario=solicitud.id_veterinario_id
        ).filter(
            Q(id_inventario__in=todos_los_id_inventario) | Q(id_medicamento__in=med_ids_sin_origen)
        ).order_by('id_kit_lote')
    )
    kit_lotes_map = {kl.id_inventario_id: kl for kl in kit_lotes_bloqueados}

    faltantes_dev = [
        kl.id_inventario_id for kl in kit_lotes_bloqueados if kl.id_inventario_id not in inventarios_map
    ]
    if faltantes_dev:
        raise InventarioDomainError(
            f"Conflicto de concurrencia al procesar devolución de la solicitud #{id_solicitud}: "
            f"se detectaron lotes en el kit creados concurrentemente ({faltantes_dev}) que no contaban "
            f"con bloqueo pesimista en inventarios. Por favor, reintente la operación.",
            codigo="CONFLICTO_CONCURRENCIA_REINTENTO",
            extra={'id_solicitud': id_solicitud, 'lotes_faltantes': faltantes_dev}
        )

    # 7. Procesar cada detalle con aprobación > 0 aplicando la Regla Única de Vencidos (Punto 3)
    hoy = _obtener_fecha_hoy()
    ahora = timezone.now()
    detalles_procesados = []
    bajas_vencimiento = []

    for d in detalles_qs:
        cant_aprob = aprobaciones_map[d.id_detalle_solicitud]
        d.cantidad_aprobada = cant_aprob
        d.save(update_fields=['cantidad_aprobada'])

        if cant_aprob == 0:
            detalles_procesados.append({
                'id_detalle_solicitud': d.id_detalle_solicitud,
                'id_medicamento': d.id_medicamento_id,
                'cantidad_solicitada': d.cantidad_solicitada,
                'cantidad_aprobada': 0,
                'es_vencido': d.es_vencido,
                'unidades_baja_vencimiento': 0,
                'unidades_reintegradas_bodega': 0,
                'lotes_afectados': [],
            })
            continue

        det_unidades_baja = 0
        det_unidades_reint = 0
        lotes_afectados = []

        # Caso A: Detalle con lote específico de origen (id_inventario_origen)
        if d.id_inventario_origen_id is not None:
            kl = kit_lotes_map.get(d.id_inventario_origen_id)
            if not kl or kl.id_medicamento_id != d.id_medicamento_id or kl.cantidad_actual < cant_aprob:
                disponible = kl.cantidad_actual if (kl and kl.id_medicamento_id == d.id_medicamento_id) else 0
                raise StockInsuficienteKitError(
                    f"El lote #{d.id_inventario_origen_id} en el kit del veterinario tiene saldo insuficiente "
                    f"({disponible}) para devolver {cant_aprob} unidades solicitadas."
                )

            es_vencido = d.es_vencido or (kl.fecha_vencimiento is not None and kl.fecha_vencimiento < hoy)

            if es_vencido:
                # Baja por vencimiento (1 fila en KIT_VETERINARIO, no entra a bodega)
                saldo_anterior = kl.cantidad_actual
                kl.cantidad_actual -= cant_aprob
                kl.actualizado_el = ahora
                kl.save()

                InventarioKardex.objects.create(
                    tipo_movimiento=TIPO_KARDEX_BAJA_VENCIMIENTO,
                    ubicacion=UBICACION_KARDEX_KIT,
                    id_usuario_ejecuta_id=id_gestor,
                    id_veterinario_id=solicitud.id_veterinario_id,
                    id_medicamento_id=d.id_medicamento_id,
                    id_inventario_id=kl.id_inventario_id,
                    cantidad=cant_aprob,
                    saldo_anterior=saldo_anterior,
                    saldo_resultante=kl.cantidad_actual,
                    motivo=f"Baja por vencimiento en devolución de solicitud #{id_solicitud}: {solicitud.motivo_solicitud}",
                    id_solicitud_kit=solicitud,
                )

                det_unidades_baja = cant_aprob
                bajas_vencimiento.append({
                    'id_kit_lote': kl.id_kit_lote,
                    'id_inventario': kl.id_inventario_id,
                    'cantidad_baja': cant_aprob,
                    'fecha_vencimiento': kl.fecha_vencimiento,
                })
                lotes_afectados.append({
                    'id_kit_lote': kl.id_kit_lote,
                    'id_inventario': kl.id_inventario_id,
                    'cantidad': cant_aprob,
                    'tipo_movimiento': TIPO_KARDEX_BAJA_VENCIMIENTO,
                })
            else:
                # Lote vigente devuelto (2 filas: KIT_VETERINARIO y GENERAL)
                kit_saldo_anterior = kl.cantidad_actual
                kl.cantidad_actual -= cant_aprob
                kl.actualizado_el = ahora
                kl.save()

                inv = inventarios_map[kl.id_inventario_id]
                inv_saldo_anterior = inv.cantidad_actual
                inv.cantidad_actual += cant_aprob
                inv.estado = 'Disponible'
                inv.save(update_fields=['cantidad_actual', 'estado'])

                InventarioKardex.objects.create(
                    tipo_movimiento=TIPO_KARDEX_DEVOLUCION_APROBADA,
                    ubicacion=UBICACION_KARDEX_KIT,
                    id_usuario_ejecuta_id=id_gestor,
                    id_veterinario_id=solicitud.id_veterinario_id,
                    id_medicamento_id=d.id_medicamento_id,
                    id_inventario_id=kl.id_inventario_id,
                    cantidad=cant_aprob,
                    saldo_anterior=kit_saldo_anterior,
                    saldo_resultante=kl.cantidad_actual,
                    motivo=f"Devolución aprobada solicitud #{id_solicitud}: {solicitud.motivo_solicitud}",
                    id_solicitud_kit=solicitud,
                )

                InventarioKardex.objects.create(
                    tipo_movimiento=TIPO_KARDEX_DEVOLUCION_APROBADA,
                    ubicacion=UBICACION_KARDEX_GENERAL,
                    id_usuario_ejecuta_id=id_gestor,
                    id_veterinario_id=solicitud.id_veterinario_id,
                    id_medicamento_id=d.id_medicamento_id,
                    id_inventario_id=kl.id_inventario_id,
                    cantidad=cant_aprob,
                    saldo_anterior=inv_saldo_anterior,
                    saldo_resultante=inv.cantidad_actual,
                    motivo=f"Devolución aprobada solicitud #{id_solicitud}: {solicitud.motivo_solicitud}",
                    id_solicitud_kit=solicitud,
                )

                det_unidades_reint = cant_aprob
                lotes_afectados.append({
                    'id_kit_lote': kl.id_kit_lote,
                    'id_inventario': kl.id_inventario_id,
                    'cantidad': cant_aprob,
                    'tipo_movimiento': TIPO_KARDEX_DEVOLUCION_APROBADA,
                })

        # Caso B: Detalle sin lote específico con es_vencido=True (solo da de baja vencidos)
        elif d.es_vencido:
            cand_kl = [
                kl for kl in kit_lotes_bloqueados
                if kl.id_medicamento_id == d.id_medicamento_id and kl.cantidad_actual > 0
            ]
            lotes_vencidos = [
                kl for kl in cand_kl
                if kl.fecha_vencimiento is not None and kl.fecha_vencimiento < hoy
            ]
            total_vencido = sum(kl.cantidad_actual for kl in lotes_vencidos)
            if total_vencido < cant_aprob:
                raise StockInsuficienteKitError(
                    f"Stock vencido insuficiente en el kit para devolver {cant_aprob} unidades del "
                    f"medicamento #{d.id_medicamento_id}. Disponible vencido: {total_vencido}."
                )

            lotes_vencidos.sort(key=lambda kl: (kl.fecha_vencimiento, kl.id_kit_lote))
            restante = cant_aprob

            for kl in lotes_vencidos:
                if restante == 0:
                    break
                a_devolver = min(kl.cantidad_actual, restante)
                if a_devolver <= 0:
                    continue

                saldo_anterior = kl.cantidad_actual
                kl.cantidad_actual -= a_devolver
                kl.actualizado_el = ahora
                kl.save()

                InventarioKardex.objects.create(
                    tipo_movimiento=TIPO_KARDEX_BAJA_VENCIMIENTO,
                    ubicacion=UBICACION_KARDEX_KIT,
                    id_usuario_ejecuta_id=id_gestor,
                    id_veterinario_id=solicitud.id_veterinario_id,
                    id_medicamento_id=d.id_medicamento_id,
                    id_inventario_id=kl.id_inventario_id,
                    cantidad=a_devolver,
                    saldo_anterior=saldo_anterior,
                    saldo_resultante=kl.cantidad_actual,
                    motivo=f"Baja por vencimiento en devolución de solicitud #{id_solicitud}: {solicitud.motivo_solicitud}",
                    id_solicitud_kit=solicitud,
                )

                det_unidades_baja += a_devolver
                bajas_vencimiento.append({
                    'id_kit_lote': kl.id_kit_lote,
                    'id_inventario': kl.id_inventario_id,
                    'cantidad_baja': a_devolver,
                    'fecha_vencimiento': kl.fecha_vencimiento,
                })
                lotes_afectados.append({
                    'id_kit_lote': kl.id_kit_lote,
                    'id_inventario': kl.id_inventario_id,
                    'cantidad': a_devolver,
                    'tipo_movimiento': TIPO_KARDEX_BAJA_VENCIMIENTO,
                })
                restante -= a_devolver

        # Caso C: Detalle sin lote específico con es_vencido=False (vencidos primero hasta N, resto vigentes)
        else:
            cand_kl = [
                kl for kl in kit_lotes_bloqueados
                if kl.id_medicamento_id == d.id_medicamento_id and kl.cantidad_actual > 0
            ]
            total_disponible = sum(kl.cantidad_actual for kl in cand_kl)
            if total_disponible < cant_aprob:
                raise StockInsuficienteKitError(
                    f"Stock total insuficiente en el kit para devolver {cant_aprob} unidades del "
                    f"medicamento #{d.id_medicamento_id}. Disponible total en kit: {total_disponible}."
                )

            lotes_vencidos = [
                kl for kl in cand_kl
                if kl.fecha_vencimiento is not None and kl.fecha_vencimiento < hoy
            ]
            lotes_vigentes = [
                kl for kl in cand_kl
                if kl.fecha_vencimiento is None or kl.fecha_vencimiento >= hoy
            ]

            lotes_vencidos.sort(key=lambda kl: (kl.fecha_vencimiento, kl.id_kit_lote))
            lotes_vigentes.sort(
                key=lambda kl: _sort_key_fifo_inverso(kl.fecha_compra_origen, kl.id_inventario_id),
                reverse=True
            )

            restante = cant_aprob

            # 1. Dar de baja vencidos primero
            for kl in lotes_vencidos:
                if restante == 0:
                    break
                a_devolver = min(kl.cantidad_actual, restante)
                if a_devolver <= 0:
                    continue

                saldo_anterior = kl.cantidad_actual
                kl.cantidad_actual -= a_devolver
                kl.actualizado_el = ahora
                kl.save()

                InventarioKardex.objects.create(
                    tipo_movimiento=TIPO_KARDEX_BAJA_VENCIMIENTO,
                    ubicacion=UBICACION_KARDEX_KIT,
                    id_usuario_ejecuta_id=id_gestor,
                    id_veterinario_id=solicitud.id_veterinario_id,
                    id_medicamento_id=d.id_medicamento_id,
                    id_inventario_id=kl.id_inventario_id,
                    cantidad=a_devolver,
                    saldo_anterior=saldo_anterior,
                    saldo_resultante=kl.cantidad_actual,
                    motivo=f"Baja por vencimiento en devolución de solicitud #{id_solicitud}: {solicitud.motivo_solicitud}",
                    id_solicitud_kit=solicitud,
                )

                det_unidades_baja += a_devolver
                bajas_vencimiento.append({
                    'id_kit_lote': kl.id_kit_lote,
                    'id_inventario': kl.id_inventario_id,
                    'cantidad_baja': a_devolver,
                    'fecha_vencimiento': kl.fecha_vencimiento,
                })
                lotes_afectados.append({
                    'id_kit_lote': kl.id_kit_lote,
                    'id_inventario': kl.id_inventario_id,
                    'cantidad': a_devolver,
                    'tipo_movimiento': TIPO_KARDEX_BAJA_VENCIMIENTO,
                })
                restante -= a_devolver

            # 2. Reintegrar resto de vigentes por FIFO Inverso
            for kl in lotes_vigentes:
                if restante == 0:
                    break
                a_devolver = min(kl.cantidad_actual, restante)
                if a_devolver <= 0:
                    continue

                kit_saldo_anterior = kl.cantidad_actual
                kl.cantidad_actual -= a_devolver
                kl.actualizado_el = ahora
                kl.save()

                inv = inventarios_map[kl.id_inventario_id]
                inv_saldo_anterior = inv.cantidad_actual
                inv.cantidad_actual += a_devolver
                inv.estado = 'Disponible'
                inv.save(update_fields=['cantidad_actual', 'estado'])

                InventarioKardex.objects.create(
                    tipo_movimiento=TIPO_KARDEX_DEVOLUCION_APROBADA,
                    ubicacion=UBICACION_KARDEX_KIT,
                    id_usuario_ejecuta_id=id_gestor,
                    id_veterinario_id=solicitud.id_veterinario_id,
                    id_medicamento_id=d.id_medicamento_id,
                    id_inventario_id=kl.id_inventario_id,
                    cantidad=a_devolver,
                    saldo_anterior=kit_saldo_anterior,
                    saldo_resultante=kl.cantidad_actual,
                    motivo=f"Devolución aprobada solicitud #{id_solicitud}: {solicitud.motivo_solicitud}",
                    id_solicitud_kit=solicitud,
                )

                InventarioKardex.objects.create(
                    tipo_movimiento=TIPO_KARDEX_DEVOLUCION_APROBADA,
                    ubicacion=UBICACION_KARDEX_GENERAL,
                    id_usuario_ejecuta_id=id_gestor,
                    id_veterinario_id=solicitud.id_veterinario_id,
                    id_medicamento_id=d.id_medicamento_id,
                    id_inventario_id=kl.id_inventario_id,
                    cantidad=a_devolver,
                    saldo_anterior=inv_saldo_anterior,
                    saldo_resultante=inv.cantidad_actual,
                    motivo=f"Devolución aprobada solicitud #{id_solicitud}: {solicitud.motivo_solicitud}",
                    id_solicitud_kit=solicitud,
                )

                det_unidades_reint += a_devolver
                lotes_afectados.append({
                    'id_kit_lote': kl.id_kit_lote,
                    'id_inventario': kl.id_inventario_id,
                    'cantidad': a_devolver,
                    'tipo_movimiento': TIPO_KARDEX_DEVOLUCION_APROBADA,
                })
                restante -= a_devolver

        detalles_procesados.append({
            'id_detalle_solicitud': d.id_detalle_solicitud,
            'id_medicamento': d.id_medicamento_id,
            'cantidad_solicitada': d.cantidad_solicitada,
            'cantidad_aprobada': cant_aprob,
            'es_vencido': d.es_vencido,
            'unidades_baja_vencimiento': det_unidades_baja,
            'unidades_reintegradas_bodega': det_unidades_reint,
            'lotes_afectados': lotes_afectados,
        })

    # 8. Actualizar cabecera de la solicitud (Regla 5)
    if any(d.cantidad_aprobada < d.cantidad_solicitada for d in detalles_qs):
        solicitud.estado = SOLICITUD_ESTADO_PARCIAL
    else:
        solicitud.estado = SOLICITUD_ESTADO_APROBADA

    solicitud.id_gestor_id = id_gestor
    solicitud.fecha_resolucion = ahora
    if observaciones_gestor is not None:
        solicitud.observaciones_gestor = observaciones_gestor
    solicitud.save(update_fields=['estado', 'id_gestor', 'fecha_resolucion', 'observaciones_gestor'])

    total_bajas = sum(dp['unidades_baja_vencimiento'] for dp in detalles_procesados)
    total_reintegradas = sum(dp['unidades_reintegradas_bodega'] for dp in detalles_procesados)

    return {
        'id_solicitud': solicitud.id_solicitud,
        'tipo_solicitud': solicitud.tipo_solicitud,
        'estado': solicitud.estado,
        'id_veterinario': solicitud.id_veterinario_id,
        'id_gestor': solicitud.id_gestor_id,
        'fecha_resolucion': solicitud.fecha_resolucion,
        'observaciones_gestor': solicitud.observaciones_gestor,
        'unidades_baja_vencimiento': total_bajas,
        'unidades_reintegradas_bodega': total_reintegradas,
        'detalles_procesados': detalles_procesados,
        'bajas_vencimiento': bajas_vencimiento,
    }

