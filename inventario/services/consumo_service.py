"""
Servicio de Consumo Clínico de Insumos y Medicamentos del Kit por Veterinario.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.

Implementa el motor transaccional de descuento automático de fármacos en:
- Historia Clínica (CONSUMO_HISTORIA: exige al menos id_consulta o id_tratamiento)
- Acta de Visita / Seres Sintientes (CONSUMO_ACTA: exige al menos id_seguimiento_visita o id_visita_animal)
- Sincronización Offline Dedicada (DIRECTO: exige IDs de origen de historia o de acta)

Garantías arquitectónicas y reglas de negocio:
- Ejecución dentro de la transacción atómica del llamador (savepoints anidados).
- Modo Online (es_offline=False, R3): Todo o Nada. Cualquier falta de stock vigente
  lanza StockInsuficienteKitError con el mensaje canónico y revierte toda la transacción.
- Modo Offline (es_offline=True, R14): Tolerancia parcial mediante savepoints por renglón.
  Los consumos sin stock vigente registran RECHAZADO_CONFLICTO (codigo_http=409) sin anular
  los demás consumos ni el guardado clínico principal.
- Idempotencia total por client_uuid (R13): verificación pre-lock, re-lectura post-lock
  y captura de IntegrityError en savepoint ante peticiones concurrentes gemelas.
- FIFO Directo en Kit (R1 / Decisión 1): fecha_compra_origen ASC NULLS FIRST, id_inventario ASC,
  excluyendo estrictamente lotes vencidos (fecha_vencimiento < hoy; fecha_vencimiento == hoy es vigente).
- Orden Global de Locks (R12): bloqueo unificado exclusivo de kit_veterinario_lotes
  ordenado por id_kit_lote ASC en una sola consulta (sin tocar inventarios ni usar .distinct()).
"""

import uuid
from django.db import IntegrityError, transaction
from django.utils import timezone

from inventario.constants import (
    TIPO_KARDEX_CONSUMO_HISTORIA,
    TIPO_KARDEX_CONSUMO_ACTA,
    UBICACION_KARDEX_KIT,
    CONSUMO_ESTADO_APLICADO,
    CONSUMO_ESTADO_RECHAZADO_CONFLICTO,
    CONSUMO_ESTADO_REVERTIDO,
    CONSUMO_ORIGEN_HISTORIA,
    CONSUMO_ORIGEN_ACTA,
    CONSUMO_ORIGEN_DIRECTO,
    CONSUMO_ORIGENES_PERMITIDOS,
)
from inventario.models import (
    KitVeterinarioLotes,
    InventarioKardex,
    InventarioConsumosRegistro,
)
from medicamentos.models import Medicamentos
from inventario.services.exceptions import (
    StockInsuficienteKitError,
    MedicamentoInvalidoError,
    CantidadInvalidaError,
    ConflictoIdempotenciaConsumoError,
    ConsumoInvalidoError,
)
from inventario.services.kit_service import (
    _es_entero_valido,
    _validar_veterinario,
    _obtener_fecha_hoy,
    _normalizar_fecha_compra,
)


def _sort_key_fifo_directo(fecha_compra, id_inventario: int):
    """
    Clave para ordenar lotes del kit en FIFO Directo (Decisión 1):
    fecha_compra_origen ASC NULLS FIRST, id_inventario ASC.
    - has_date=0 coloca NULLs primero (NULLS FIRST) al ordenar en forma ascendente.
    - date_val ordena las fechas de compra más antiguas primero.
    - id_inventario desempata con el ID menor primero.
    """
    has_date = 0 if fecha_compra is None else 1
    date_val = _normalizar_fecha_compra(fecha_compra)
    return (has_date, date_val, id_inventario)


def _parsear_client_uuid(raw_uuid) -> uuid.UUID:
    """
    Valida y convierte client_uuid a una instancia uuid.UUID.
    Rechaza valores nulos, booleanos, enteros o cadenas con formato inválido.
    """
    if isinstance(raw_uuid, uuid.UUID):
        return raw_uuid
    if isinstance(raw_uuid, str) and raw_uuid.strip():
        try:
            return uuid.UUID(raw_uuid.strip())
        except (ValueError, AttributeError) as exc:
            raise ConsumoInvalidoError(
                f"El client_uuid '{raw_uuid}' no tiene un formato UUID válido."
            ) from exc
    raise ConsumoInvalidoError(
        "Cada consumo debe incluir un 'client_uuid' con formato UUID válido."
    )


def _extraer_id_opcional(
    contenedor: dict,
    clave_larga: str,
    clave_corta: str,
    contexto: str,
) -> tuple[bool, int | None]:
    """
    Extrae un ID opcional desde un diccionario aceptando la clave larga (ej. 'id_consulta')
    o la clave corta (ej. 'consulta').
    Si la clave no está presente o su valor es None, se trata como no especificado: (False, None).
    Retorna (fue_especificado, valor_validado).
    """
    tiene_larga = clave_larga in contenedor
    tiene_corta = clave_corta in contenedor

    if not tiene_larga and not tiene_corta:
        return False, None

    val_larga = contenedor.get(clave_larga)
    val_corta = contenedor.get(clave_corta)

    if (
        tiene_larga
        and tiene_corta
        and val_larga is not None
        and val_corta is not None
        and val_larga != val_corta
    ):
        raise ConsumoInvalidoError(
            f"Conflicto en {contexto}: se enviaron '{clave_larga}' ({val_larga}) y "
            f"'{clave_corta}' ({val_corta}) con valores distintos."
        )

    valor = val_larga if (tiene_larga and val_larga is not None) else val_corta
    if valor is None:
        return False, None

    if not _es_entero_valido(valor, minimo=1):
        raise ConsumoInvalidoError(
            f"El identificador '{clave_larga}' en {contexto} debe ser un entero mayor a cero."
        )
    return True, valor


def _resolver_origen_item(
    item: dict,
    origen_tipo_default: str,
    base_ids: dict,
    idx: int,
) -> dict:
    """
    Resuelve y valida origen_tipo, los 4 IDs de origen clínico y el tipo_movimiento de Kardex
    para un renglón de consumo.
    Si un ID no viene en el ítem (o viene con valor None), hace fallback a base_ids (origen_ids).
    Exige trazabilidad de origen (R9) en todos los modos:
    - HISTORIA_CLINICA: al menos uno de id_consulta o id_tratamiento (sin IDs de acta).
    - ACTA_VISITA: al menos uno de id_seguimiento_visita o id_visita_animal (sin IDs de historia).
    - DIRECTO: al menos uno de historia o de acta (sin mezclar ambos).
    """
    contexto = f"consumo #{idx}"
    item_origen_tipo = item.get('origen_tipo')
    if item_origen_tipo is None:
        item_origen_tipo = origen_tipo_default
    if not isinstance(item_origen_tipo, str) or item_origen_tipo not in CONSUMO_ORIGENES_PERMITIDOS:
        raise ConsumoInvalidoError(
            f"origen_tipo '{item_origen_tipo}' inválido en {contexto}. "
            f"Valores permitidos: {CONSUMO_ORIGENES_PERMITIDOS}."
        )

    espec_cons, val_cons = _extraer_id_opcional(item, 'id_consulta', 'consulta', contexto)
    espec_trat, val_trat = _extraer_id_opcional(item, 'id_tratamiento', 'tratamiento', contexto)
    espec_seg, val_seg = _extraer_id_opcional(item, 'id_seguimiento_visita', 'seguimiento', contexto)
    espec_vis, val_vis = _extraer_id_opcional(item, 'id_visita_animal', 'visita', contexto)

    id_consulta = val_cons if espec_cons else base_ids.get('id_consulta')
    id_tratamiento = val_trat if espec_trat else base_ids.get('id_tratamiento')
    id_seguimiento_visita = val_seg if espec_seg else base_ids.get('id_seguimiento_visita')
    id_visita_animal = val_vis if espec_vis else base_ids.get('id_visita_animal')

    tiene_historia = (id_consulta is not None) or (id_tratamiento is not None)
    tiene_acta = (id_seguimiento_visita is not None) or (id_visita_animal is not None)

    if tiene_historia and tiene_acta:
        raise ConsumoInvalidoError(
            f"Error en {contexto}: no se pueden combinar identificadores de historia clínica "
            f"(id_consulta/id_tratamiento) con identificadores de acta de visita "
            f"(id_seguimiento_visita/id_visita_animal) en un mismo consumo."
        )

    if item_origen_tipo == CONSUMO_ORIGEN_HISTORIA:
        if tiene_acta:
            raise ConsumoInvalidoError(
                f"Error en {contexto}: origen_tipo '{CONSUMO_ORIGEN_HISTORIA}' no admite "
                f"id_seguimiento_visita ni id_visita_animal."
            )
        if not tiene_historia:
            raise ConsumoInvalidoError(
                f"Error en {contexto}: origen_tipo '{CONSUMO_ORIGEN_HISTORIA}' requiere al menos "
                f"uno de 'id_consulta' o 'id_tratamiento' para trazabilidad de origen (R9)."
            )
        tipo_kardex = TIPO_KARDEX_CONSUMO_HISTORIA

    elif item_origen_tipo == CONSUMO_ORIGEN_ACTA:
        if tiene_historia:
            raise ConsumoInvalidoError(
                f"Error en {contexto}: origen_tipo '{CONSUMO_ORIGEN_ACTA}' no admite "
                f"id_consulta ni id_tratamiento."
            )
        if not tiene_acta:
            raise ConsumoInvalidoError(
                f"Error en {contexto}: origen_tipo '{CONSUMO_ORIGEN_ACTA}' requiere al menos "
                f"uno de 'id_seguimiento_visita' o 'id_visita_animal' para trazabilidad de origen (R9)."
            )
        tipo_kardex = TIPO_KARDEX_CONSUMO_ACTA

    elif item_origen_tipo == CONSUMO_ORIGEN_DIRECTO:
        if tiene_historia:
            tipo_kardex = TIPO_KARDEX_CONSUMO_HISTORIA
        elif tiene_acta:
            tipo_kardex = TIPO_KARDEX_CONSUMO_ACTA
        else:
            raise ConsumoInvalidoError(
                f"Error en {contexto}: origen_tipo '{CONSUMO_ORIGEN_DIRECTO}' solo es válido si "
                f"incluye identificadores de origen (id_consulta/id_tratamiento o "
                f"id_seguimiento_visita/id_visita_animal)."
            )

    return {
        'origen_tipo': item_origen_tipo,
        'tipo_kardex': tipo_kardex,
        'id_consulta': id_consulta,
        'id_tratamiento': id_tratamiento,
        'id_seguimiento_visita': id_seguimiento_visita,
        'id_visita_animal': id_visita_animal,
    }


def _construir_motivo_kardex(item_norm: dict) -> str:
    """Construye la glosa de auditoría para inventario_kardex."""
    partes_ref = []
    if item_norm['id_consulta'] is not None:
        partes_ref.append(f"consulta #{item_norm['id_consulta']}")
    if item_norm['id_tratamiento'] is not None:
        partes_ref.append(f"tratamiento #{item_norm['id_tratamiento']}")
    if item_norm['id_seguimiento_visita'] is not None:
        partes_ref.append(f"seguimiento #{item_norm['id_seguimiento_visita']}")
    if item_norm['id_visita_animal'] is not None:
        partes_ref.append(f"visita_animal #{item_norm['id_visita_animal']}")

    ref_str = f" ({', '.join(partes_ref)})" if partes_ref else ""
    if item_norm['tipo_kardex'] == TIPO_KARDEX_CONSUMO_HISTORIA:
        base = "Consumo en historia clínica"
    else:
        base = "Consumo en acta de visita"
    return f"{base}{ref_str} [uuid: {item_norm['client_uuid']}]"


def _validar_y_reconstruir_idempotente(
    registro: InventarioConsumosRegistro,
    item_norm: dict,
    id_veterinario: int,
    med_obj: Medicamentos,
    es_offline: bool,
) -> tuple[str, dict, dict | None]:
    """
    Valida que un registro preexistente en inventario_consumos_registro coincida en
    veterinario, medicamento y unidades con el ítem recibido, y reconstruye su respuesta
    sin alterar el stock.
    """
    uuid_str = str(registro.client_uuid)
    med_id = item_norm['id_medicamento']
    unidades = item_norm['unidades']

    if (
        registro.id_veterinario_id != id_veterinario
        or registro.id_medicamento_id != med_id
        or registro.cantidad_solicitada != unidades
    ):
        raise ConflictoIdempotenciaConsumoError(
            f"El client_uuid '{uuid_str}' ya fue registrado con parámetros distintos "
            f"(registrado: vet=#{registro.id_veterinario_id}, med=#{registro.id_medicamento_id}, "
            f"unidades={registro.cantidad_solicitada}; recibido: vet=#{id_veterinario}, "
            f"med=#{med_id}, unidades={unidades}).",
            extra={
                'client_uuid': uuid_str,
                'id_veterinario_registrado': registro.id_veterinario_id,
                'id_medicamento_registrado': registro.id_medicamento_id,
                'unidades_registradas': registro.cantidad_solicitada,
            },
        )

    if registro.estado == CONSUMO_ESTADO_REVERTIDO:
        raise ConflictoIdempotenciaConsumoError(
            f"El consumo con client_uuid '{uuid_str}' se encuentra en estado REVERTIDO y no puede reutilizarse.",
            extra={'client_uuid': uuid_str, 'estado': registro.estado},
        )

    if registro.estado == CONSUMO_ESTADO_RECHAZADO_CONFLICTO:
        if not es_offline:
            raise ConflictoIdempotenciaConsumoError(
                f"El consumo con client_uuid '{uuid_str}' fue registrado previamente como "
                f"RECHAZADO_CONFLICTO y no puede procesarse en modo online.",
                extra={'client_uuid': uuid_str, 'estado': registro.estado},
            )
        disp_rechazo = (
            registro.stock_disponible_al_rechazo
            if registro.stock_disponible_al_rechazo is not None
            else 0
        )
        motivo_rechazo = registro.motivo_rechazo or (
            f"Stock insuficiente en el kit de {med_obj.nombre}: "
            f"disponible {disp_rechazo}, solicitado {unidades}"
        )
        conflicto_dict = {
            'id_consumo': registro.id_consumo,
            'client_uuid': uuid_str,
            'id_medicamento': med_id,
            'nombre_medicamento': med_obj.nombre,
            'unidades': unidades,
            'cantidad_solicitada': unidades,
            'stock_disponible_actual': disp_rechazo,
            'estado': CONSUMO_ESTADO_RECHAZADO_CONFLICTO,
            'codigo_http': registro.codigo_http,
            'motivo_rechazo': motivo_rechazo,
            'idempotente': True,
        }
        alerta_dict = {
            'client_uuid': uuid_str,
            'id_medicamento': med_id,
            'nombre_medicamento': med_obj.nombre,
            'solicitado': unidades,
            'disponible': disp_rechazo,
            'motivo_rechazo': motivo_rechazo,
        }
        return ('CONFLICTO', conflicto_dict, alerta_dict)

    # Estado APLICADO: reconstruir desglose de lotes desde inventario_kardex
    movimientos_kardex = list(
        InventarioKardex.objects.filter(
            client_uuid=registro.client_uuid
        ).order_by('id_kardex')
    )
    lotes_afectados = [
        {
            'id_inventario': mov.id_inventario_id,
            'cantidad_descontada': mov.cantidad,
            'saldo_anterior_kit': mov.saldo_anterior,
            'saldo_resultante_kit': mov.saldo_resultante,
            'id_kardex': mov.id_kardex,
        }
        for mov in movimientos_kardex
    ]
    aplicado_dict = {
        'id_consumo': registro.id_consumo,
        'client_uuid': uuid_str,
        'id_medicamento': med_id,
        'nombre_medicamento': med_obj.nombre,
        'unidades_solicitadas': registro.cantidad_solicitada,
        'unidades_descontadas': registro.cantidad_descontada,
        'estado': CONSUMO_ESTADO_APLICADO,
        'codigo_http': registro.codigo_http,
        'idempotente': True,
        'lotes_afectados': lotes_afectados,
    }
    return ('APLICADO', aplicado_dict, None)


@transaction.atomic
def descontar_consumo_kit(
    id_veterinario: int,
    consumos: list[dict],
    origen_tipo: str,
    origen_ids: dict | None = None,
    es_offline: bool = False,
) -> dict:
    """
    Descuenta unidades de medicamentos del kit personal del veterinario aplicando FIFO Directo,
    idempotencia por client_uuid y política de respuestas online (Todo o Nada) vs offline (Savepoints).

    Parámetros:
    - id_veterinario (int): ID del veterinario titular del kit (debe ser Rol 3 activo).
    - consumos (list[dict]): Lista de consumos, cada uno con:
        * 'id_medicamento' (int > 0)
        * 'unidades' (int > 0, sin bool)
        * 'client_uuid' (UUID v4 str o uuid.UUID)
        * Opcionales por ítem: 'origen_tipo', 'id_consulta', 'id_tratamiento',
          'id_seguimiento_visita', 'id_visita_animal' (o claves cortas 'consulta',
          'tratamiento', 'seguimiento', 'visita').
    - origen_tipo (str): 'HISTORIA_CLINICA' (exige id_consulta o id_tratamiento),
      'ACTA_VISITA' (exige id_seguimiento_visita o id_visita_animal) o 'DIRECTO'
      (exige al menos un ID de historia o de acta sin mezclarlos).
    - origen_ids (dict | None): IDs clínicos del contexto padre (consulta, tratamiento, seguimiento, visita).
    - es_offline (bool): Parámetro explícito enviado por el llamador (nunca inferido).

    Retorna:
    - dict con claves 'aplicados', 'conflictos' y 'alertas_farmacia' ({'conflictos_stock': [...]}).
    """
    # 1. Validaciones de actor y parámetros generales
    _validar_veterinario(id_veterinario)

    if not isinstance(es_offline, bool):
        raise ConsumoInvalidoError("El parámetro 'es_offline' debe ser un valor booleano explícito.")

    if not consumos or not isinstance(consumos, list):
        raise ConsumoInvalidoError("La lista 'consumos' no puede estar vacía.")

    if not isinstance(origen_tipo, str) or origen_tipo not in CONSUMO_ORIGENES_PERMITIDOS:
        raise ConsumoInvalidoError(
            f"El parámetro 'origen_tipo' ('{origen_tipo}') no es válido. "
            f"Valores permitidos: {CONSUMO_ORIGENES_PERMITIDOS}."
        )

    if origen_ids is not None and not isinstance(origen_ids, dict):
        raise ConsumoInvalidoError("El parámetro 'origen_ids' debe ser un diccionario o None.")

    origen_ids_dict = origen_ids or {}
    espec_b_cons, base_cons = _extraer_id_opcional(origen_ids_dict, 'id_consulta', 'consulta', 'origen_ids')
    espec_b_trat, base_trat = _extraer_id_opcional(origen_ids_dict, 'id_tratamiento', 'tratamiento', 'origen_ids')
    espec_b_seg, base_seg = _extraer_id_opcional(origen_ids_dict, 'id_seguimiento_visita', 'seguimiento', 'origen_ids')
    espec_b_vis, base_vis = _extraer_id_opcional(origen_ids_dict, 'id_visita_animal', 'visita', 'origen_ids')
    base_ids = {
        'id_consulta': base_cons if espec_b_cons else None,
        'id_tratamiento': base_trat if espec_b_trat else None,
        'id_seguimiento_visita': base_seg if espec_b_seg else None,
        'id_visita_animal': base_vis if espec_b_vis else None,
    }

    # 2. Validar y normalizar cada renglón del payload
    items_normalizados = []
    uuids_vistos = set()
    med_ids_vistos = set()

    for idx, item in enumerate(consumos, start=1):
        if not isinstance(item, dict):
            raise ConsumoInvalidoError(f"El elemento #{idx} de 'consumos' debe ser un diccionario.")

        uuid_obj = _parsear_client_uuid(item.get('client_uuid'))
        if uuid_obj in uuids_vistos:
            raise ConsumoInvalidoError(
                f"El client_uuid '{uuid_obj}' está duplicado dentro del mismo payload de consumo."
            )
        uuids_vistos.add(uuid_obj)

        med_id = item.get('id_medicamento')
        if not _es_entero_valido(med_id, minimo=1):
            raise CantidadInvalidaError(
                f"En el consumo #{idx}, 'id_medicamento' debe ser un entero mayor a cero."
            )

        if 'unidades' not in item:
            raise CantidadInvalidaError(
                f"En el consumo #{idx} (medicamento #{med_id}), falta la clave obligatoria 'unidades'."
            )
        unidades = item.get('unidades')
        if not _es_entero_valido(unidades, minimo=1):
            raise CantidadInvalidaError(
                f"En el consumo #{idx} (medicamento #{med_id}), 'unidades' debe ser un entero mayor a cero."
            )

        datos_origen = _resolver_origen_item(item, origen_tipo, base_ids, idx)
        med_ids_vistos.add(med_id)

        items_normalizados.append({
            'client_uuid': uuid_obj,
            'id_medicamento': med_id,
            'unidades': unidades,
            **datos_origen,
        })

    # 3. Validar existencia en catálogo de medicamentos (NO se exige activo=True en consumo; Decisión 8)
    meds_qs = Medicamentos.objects.filter(pk__in=sorted(med_ids_vistos))
    meds_map = {m.id_medicamento: m for m in meds_qs}
    for med_id in sorted(med_ids_vistos):
        if med_id not in meds_map:
            raise MedicamentoInvalidoError(
                f"El medicamento #{med_id} no existe en el catálogo institucional."
            )

    # 4. Primera verificación de idempotencia (pre-lock)
    todos_uuids = [it['client_uuid'] for it in items_normalizados]
    existentes_map = {
        r.client_uuid: r
        for r in InventarioConsumosRegistro.objects.filter(client_uuid__in=todos_uuids)
    }

    for it in items_normalizados:
        reg_prev = existentes_map.get(it['client_uuid'])
        if reg_prev is not None:
            _validar_y_reconstruir_idempotente(
                reg_prev,
                it,
                id_veterinario,
                meds_map[it['id_medicamento']],
                es_offline,
            )

    # 5. Bloqueo pesimista unificado de kit_veterinario_lotes para los consumos nuevos
    # Orden Global de Locks (R12): solo se toca kit_veterinario_lotes por id_kit_lote ASC en una sola consulta.
    items_nuevos = [it for it in items_normalizados if it['client_uuid'] not in existentes_map]
    kit_lotes_bloqueados = []

    if items_nuevos:
        med_ids_a_bloquear = sorted({it['id_medicamento'] for it in items_nuevos})
        kit_lotes_bloqueados = list(
            KitVeterinarioLotes.objects.select_for_update().filter(
                id_veterinario=id_veterinario,
                id_medicamento__in=med_ids_a_bloquear,
            ).order_by('id_kit_lote')
        )

        # Re-lectura post-lock de idempotencia por si una transacción concurrente insertó mientras esperábamos el lock
        uuids_nuevos = [it['client_uuid'] for it in items_nuevos]
        existentes_post = {
            r.client_uuid: r
            for r in InventarioConsumosRegistro.objects.filter(client_uuid__in=uuids_nuevos)
        }
        for it in items_nuevos:
            reg_post = existentes_post.get(it['client_uuid'])
            if reg_post is not None:
                _validar_y_reconstruir_idempotente(
                    reg_post,
                    it,
                    id_veterinario,
                    meds_map[it['id_medicamento']],
                    es_offline,
                )
                existentes_map[it['client_uuid']] = reg_post

    # 6. Organizar lotes vigentes bajo lock en colas FIFO Directo por medicamento
    # Regla de vigencia: fecha_vencimiento is None o fecha_vencimiento >= hoy (== hoy es vigente; < hoy es vencido).
    hoy = _obtener_fecha_hoy()
    ahora = timezone.now()

    lotes_vigentes_por_med: dict[int, list[KitVeterinarioLotes]] = {}
    for kl in kit_lotes_bloqueados:
        if kl.cantidad_actual <= 0:
            continue
        if kl.fecha_vencimiento is not None and kl.fecha_vencimiento < hoy:
            continue
        lotes_vigentes_por_med.setdefault(kl.id_medicamento_id, []).append(kl)

    for med_id, lista_kl in lotes_vigentes_por_med.items():
        lista_kl.sort(
            key=lambda kl: _sort_key_fifo_directo(kl.fecha_compra_origen, kl.id_inventario_id)
        )

    # 7. En modo Online (es_offline=False, R3): pre-validar stock acumulado de TODO el payload
    if not es_offline:
        comprometido_por_med: dict[int, int] = {}
        for it in items_normalizados:
            if it['client_uuid'] in existentes_map:
                continue
            med_id = it['id_medicamento']
            unidades = it['unidades']
            med_obj = meds_map[med_id]

            total_vigente_med = sum(
                kl.cantidad_actual for kl in lotes_vigentes_por_med.get(med_id, [])
            )
            ya_comprometido = comprometido_por_med.get(med_id, 0)
            disponible_restante = max(0, total_vigente_med - ya_comprometido)

            if unidades > disponible_restante:
                raise StockInsuficienteKitError(
                    f"Stock insuficiente en el kit de {med_obj.nombre}: "
                    f"disponible {disponible_restante}, solicitado {unidades}",
                    extra={
                        'id_medicamento': med_id,
                        'nombre_medicamento': med_obj.nombre,
                        'disponible': disponible_restante,
                        'solicitado': unidades,
                        'client_uuid': str(it['client_uuid']),
                    },
                )
            comprometido_por_med[med_id] = ya_comprometido + unidades

    # 8. Procesar cada renglón en orden (con savepoint individual para modo offline y carreras de UUID)
    aplicados = []
    conflictos = []
    conflictos_stock_alerta = []

    for it in items_normalizados:
        uuid_obj = it['client_uuid']
        uuid_str = str(uuid_obj)
        med_id = it['id_medicamento']
        unidades = it['unidades']
        med_obj = meds_map[med_id]

        # 8.a. Si ya existía en inventario_consumos_registro (idempotencia)
        if uuid_obj in existentes_map:
            tipo_res, payload_res, alerta_res = _validar_y_reconstruir_idempotente(
                existentes_map[uuid_obj],
                it,
                id_veterinario,
                med_obj,
                es_offline,
            )
            if tipo_res == 'APLICADO':
                aplicados.append(payload_res)
            else:
                conflictos.append(payload_res)
                if alerta_res is not None:
                    conflictos_stock_alerta.append(alerta_res)
            continue

        # 8.b. Evaluar saldo vigente acumulado actual en el kit para este medicamento
        cola_lotes = lotes_vigentes_por_med.get(med_id, [])
        disponible_actual = sum(kl.cantidad_actual for kl in cola_lotes)

        # 8.c. Falta de stock vigente
        if unidades > disponible_actual:
            motivo_rechazo = (
                f"Stock insuficiente en el kit de {med_obj.nombre}: "
                f"disponible {disponible_actual}, solicitado {unidades}"
            )
            if not es_offline:
                raise StockInsuficienteKitError(
                    motivo_rechazo,
                    extra={
                        'id_medicamento': med_id,
                        'nombre_medicamento': med_obj.nombre,
                        'disponible': disponible_actual,
                        'solicitado': unidades,
                        'client_uuid': uuid_str,
                    },
                )

            # Modo Offline: registrar RECHAZADO_CONFLICTO (codigo_http=409) en su propio savepoint
            snapshot_memoria_conf = [(kl, kl.cantidad_actual, kl.actualizado_el) for kl in cola_lotes]
            try:
                with transaction.atomic():
                    reg_conflicto = InventarioConsumosRegistro.objects.create(
                        client_uuid=uuid_obj,
                        id_veterinario_id=id_veterinario,
                        id_medicamento_id=med_id,
                        cantidad_solicitada=unidades,
                        cantidad_descontada=0,
                        estado=CONSUMO_ESTADO_RECHAZADO_CONFLICTO,
                        codigo_http=409,
                        origen_tipo=it['origen_tipo'],
                        id_consulta_id=it['id_consulta'],
                        id_tratamiento_id=it['id_tratamiento'],
                        id_seguimiento_visita_id=it['id_seguimiento_visita'],
                        id_visita_animal_id=it['id_visita_animal'],
                        motivo_rechazo=motivo_rechazo,
                        stock_disponible_al_rechazo=disponible_actual,
                    )
            except IntegrityError as exc:
                for kl_inst, cant_prev, act_prev in snapshot_memoria_conf:
                    kl_inst.cantidad_actual = cant_prev
                    kl_inst.actualizado_el = act_prev

                reg_ganador = InventarioConsumosRegistro.objects.filter(client_uuid=uuid_obj).first()
                if reg_ganador is None:
                    raise ConsumoInvalidoError(
                        "No se pudo registrar el consumo: el identificador de origen no existe o los datos son inválidos",
                        extra={'error_original': str(exc), 'client_uuid': uuid_str},
                    ) from exc
                tipo_res, payload_res, alerta_res = _validar_y_reconstruir_idempotente(
                    reg_ganador, it, id_veterinario, med_obj, es_offline
                )
                if tipo_res == 'APLICADO':
                    aplicados.append(payload_res)
                else:
                    conflictos.append(payload_res)
                    if alerta_res is not None:
                        conflictos_stock_alerta.append(alerta_res)
                continue

            conflictos.append({
                'id_consumo': reg_conflicto.id_consumo,
                'client_uuid': uuid_str,
                'id_medicamento': med_id,
                'nombre_medicamento': med_obj.nombre,
                'unidades': unidades,
                'cantidad_solicitada': unidades,
                'stock_disponible_actual': disponible_actual,
                'estado': CONSUMO_ESTADO_RECHAZADO_CONFLICTO,
                'codigo_http': 409,
                'motivo_rechazo': motivo_rechazo,
                'idempotente': False,
            })
            conflictos_stock_alerta.append({
                'client_uuid': uuid_str,
                'id_medicamento': med_id,
                'nombre_medicamento': med_obj.nombre,
                'solicitado': unidades,
                'disponible': disponible_actual,
                'motivo_rechazo': motivo_rechazo,
            })
            continue

        # 8.d. Stock suficiente: descontar por FIFO Directo dentro de un savepoint
        snapshot_memoria = [(kl, kl.cantidad_actual, kl.actualizado_el) for kl in cola_lotes]
        motivo_kardex = _construir_motivo_kardex(it)
        lotes_afectados = []

        try:
            with transaction.atomic():
                restante = unidades
                for kl in cola_lotes:
                    if restante == 0:
                        break
                    if kl.cantidad_actual <= 0:
                        continue

                    a_tomar = min(kl.cantidad_actual, restante)
                    if a_tomar <= 0:
                        continue

                    saldo_anterior_kit = kl.cantidad_actual
                    kl.cantidad_actual -= a_tomar
                    kl.actualizado_el = ahora
                    kl.save(update_fields=['cantidad_actual', 'actualizado_el'])

                    kardex_row = InventarioKardex.objects.create(
                        tipo_movimiento=it['tipo_kardex'],
                        ubicacion=UBICACION_KARDEX_KIT,
                        id_usuario_ejecuta_id=id_veterinario,
                        id_veterinario_id=id_veterinario,
                        id_medicamento_id=med_id,
                        id_inventario_id=kl.id_inventario_id,
                        cantidad=a_tomar,
                        saldo_anterior=saldo_anterior_kit,
                        saldo_resultante=kl.cantidad_actual,
                        motivo=motivo_kardex,
                        id_consulta_id=it['id_consulta'],
                        id_tratamiento_id=it['id_tratamiento'],
                        id_seguimiento_visita_id=it['id_seguimiento_visita'],
                        id_visita_animal_id=it['id_visita_animal'],
                        client_uuid=uuid_obj,
                    )

                    lotes_afectados.append({
                        'id_kit_lote': kl.id_kit_lote,
                        'id_inventario': kl.id_inventario_id,
                        'cantidad_descontada': a_tomar,
                        'saldo_anterior_kit': saldo_anterior_kit,
                        'saldo_resultante_kit': kl.cantidad_actual,
                        'id_kardex': kardex_row.id_kardex,
                    })
                    restante -= a_tomar

                reg_aplicado = InventarioConsumosRegistro.objects.create(
                    client_uuid=uuid_obj,
                    id_veterinario_id=id_veterinario,
                    id_medicamento_id=med_id,
                    cantidad_solicitada=unidades,
                    cantidad_descontada=unidades,
                    estado=CONSUMO_ESTADO_APLICADO,
                    codigo_http=200,
                    origen_tipo=it['origen_tipo'],
                    id_consulta_id=it['id_consulta'],
                    id_tratamiento_id=it['id_tratamiento'],
                    id_seguimiento_visita_id=it['id_seguimiento_visita'],
                    id_visita_animal_id=it['id_visita_animal'],
                    motivo_rechazo=None,
                    stock_disponible_al_rechazo=None,
                )
        except IntegrityError as exc:
            # Restaurar estado en memoria de las instancias bloqueadas tras rollback del savepoint
            for kl_inst, cant_prev, act_prev in snapshot_memoria:
                kl_inst.cantidad_actual = cant_prev
                kl_inst.actualizado_el = act_prev

            reg_ganador = InventarioConsumosRegistro.objects.filter(client_uuid=uuid_obj).first()
            if reg_ganador is None:
                raise ConsumoInvalidoError(
                    "No se pudo registrar el consumo: el identificador de origen no existe o los datos son inválidos",
                    extra={'error_original': str(exc), 'client_uuid': uuid_str},
                ) from exc
            tipo_res, payload_res, alerta_res = _validar_y_reconstruir_idempotente(
                reg_ganador, it, id_veterinario, med_obj, es_offline
            )
            if tipo_res == 'APLICADO':
                aplicados.append(payload_res)
            else:
                conflictos.append(payload_res)
                if alerta_res is not None:
                    conflictos_stock_alerta.append(alerta_res)
            continue

        aplicados.append({
            'id_consumo': reg_aplicado.id_consumo,
            'client_uuid': uuid_str,
            'id_medicamento': med_id,
            'nombre_medicamento': med_obj.nombre,
            'unidades_solicitadas': unidades,
            'unidades_descontadas': unidades,
            'estado': CONSUMO_ESTADO_APLICADO,
            'codigo_http': 200,
            'idempotente': False,
            'lotes_afectados': lotes_afectados,
        })

    return {
        'aplicados': aplicados,
        'conflictos': conflictos,
        'alertas_farmacia': {
            'conflictos_stock': conflictos_stock_alerta,
        },
    }
