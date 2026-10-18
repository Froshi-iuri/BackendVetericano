"""
Serializadores REST del Kit de Veterinario.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.

Contrato de API para el módulo de inventario de dos niveles:
- Entrada: Valida tipos estrictos, estructuras y parámetros requeridos.
- Salida: Serializa respuestas directas de los servicios de dominio y modelos.
"""

import json
from rest_framework import serializers

from inventario.models import (
    SolicitudesKit,
    SolicitudesKitDetalles,
    InventarioKardex,
    InventarioConsumosRegistro,
)
from inventario.constants import (
    SOLICITUD_TIPO_REPOSICION,
    SOLICITUD_TIPO_DEVOLUCION,
    CONSUMO_ORIGENES_PERMITIDOS,
    CONSUMO_ORIGEN_HISTORIA,
    CONSUMO_ORIGEN_ACTA,
)
from inventario.services.exceptions import (
    ConsumoInvalidoError,
    CantidadInvalidaError,
    MedicamentoInvalidoError,
    ConflictoIdempotenciaConsumoError,
)
from inventario.services.kit_service import (
    _es_entero_valido,
    _validar_veterinario,
)
from inventario.services.consumo_service import _parsear_client_uuid
from medicamentos.models import Medicamentos



# ============================================================================
# 1. SERIALIZADORES DE ENTRADA (VALIDACIÓN DE PAYLOADS)
# ============================================================================

class ItemAsignarSerializer(serializers.Serializer):
    """Renglón de medicamento para asignación de bodega a kit."""
    id_medicamento = serializers.IntegerField(min_value=1)
    cantidad = serializers.IntegerField(min_value=1)
    fijar_cantidad_referencia = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    incrementar_cantidad_referencia = serializers.BooleanField(required=False, default=False)

    def validate_id_medicamento(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("id_medicamento debe ser un entero.")
        return value

    def validate_cantidad(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("cantidad debe ser un entero.")
        return value

    def validate_fijar_cantidad_referencia(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("fijar_cantidad_referencia debe ser un entero.")
        return value


class AsignarStockKitSerializer(serializers.Serializer):
    """Payload para POST /api/inventario/kit/asignar/."""
    id_veterinario = serializers.IntegerField(min_value=1)
    motivo = serializers.CharField(max_length=500, allow_blank=False, trim_whitespace=True)
    items = ItemAsignarSerializer(many=True, allow_empty=False)

    def validate_id_veterinario(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("id_veterinario debe ser un entero.")
        return value


class RetiroDirectoKitSerializer(serializers.Serializer):
    """Payload para POST /api/inventario/kit/retiro-directo/."""
    id_veterinario = serializers.IntegerField(min_value=1)
    id_medicamento = serializers.IntegerField(min_value=1)
    cantidad = serializers.IntegerField(min_value=1)
    motivo = serializers.CharField(max_length=500, allow_blank=False, trim_whitespace=True)

    def validate_id_veterinario(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("id_veterinario debe ser un entero.")
        return value

    def validate_id_medicamento(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("id_medicamento debe ser un entero.")
        return value

    def validate_cantidad(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("cantidad debe ser un entero.")
        return value


class ItemSolicitudInputSerializer(serializers.Serializer):
    """Renglón de medicamento en creación de solicitud."""
    id_medicamento = serializers.IntegerField(min_value=1)
    cantidad = serializers.IntegerField(min_value=1)

    def validate_id_medicamento(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("id_medicamento debe ser un entero.")
        return value

    def validate_cantidad(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("cantidad debe ser un entero.")
        return value


class CrearSolicitudKitSerializer(serializers.Serializer):
    """Payload para POST /api/inventario/kit/solicitudes/."""
    tipo_solicitud = serializers.ChoiceField(choices=[SOLICITUD_TIPO_REPOSICION, SOLICITUD_TIPO_DEVOLUCION])
    motivo = serializers.CharField(max_length=500, allow_blank=False, trim_whitespace=True)
    es_reposicion_total = serializers.BooleanField(required=False, default=False)
    items = ItemSolicitudInputSerializer(many=True, required=False, allow_null=True)

    def validate(self, attrs):
        tipo = attrs.get('tipo_solicitud')
        es_total = attrs.get('es_reposicion_total', False)
        items = attrs.get('items')

        # En DEVOLUCION rechazar es_vencido e id_inventario_origen si vienen en el payload crudo
        raw_items = self.initial_data.get('items', [])
        if tipo == SOLICITUD_TIPO_DEVOLUCION and isinstance(raw_items, list):
            for idx, raw_it in enumerate(raw_items):
                if isinstance(raw_it, dict):
                    if 'es_vencido' in raw_it or 'id_inventario_origen' in raw_it:
                        raise serializers.ValidationError(
                            f"En solicitudes de devolución no se permite especificar 'es_vencido' ni 'id_inventario_origen' (renglón {idx})."
                        )

        if tipo == SOLICITUD_TIPO_DEVOLUCION:
            if es_total:
                raise serializers.ValidationError("es_reposicion_total solo es aplicable a solicitudes de REPOSICION.")
            if not items:
                raise serializers.ValidationError("items es obligatorio y no puede estar vacío en solicitudes de DEVOLUCION.")

        if tipo == SOLICITUD_TIPO_REPOSICION:
            if not es_total and not items:
                raise serializers.ValidationError("items es obligatorio cuando es_reposicion_total es False.")
            if es_total and items:
                raise serializers.ValidationError("No se deben enviar items específicos cuando es_reposicion_total es True.")

        return attrs


class ItemAprobacionInputSerializer(serializers.Serializer):
    """Renglón de aprobación parcial para una solicitud."""
    id_detalle_solicitud = serializers.IntegerField(min_value=1)
    cantidad_aprobada = serializers.IntegerField(min_value=0)

    def validate_id_detalle_solicitud(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("id_detalle_solicitud debe ser un entero.")
        return value

    def validate_cantidad_aprobada(self, value):
        if isinstance(value, bool):
            raise serializers.ValidationError("cantidad_aprobada debe ser un entero.")
        return value


class AprobarSolicitudKitSerializer(serializers.Serializer):
    """Payload para POST /api/inventario/kit/solicitudes/{id}/aprobar/."""
    observaciones_gestor = serializers.CharField(max_length=500, required=False, allow_null=True, allow_blank=True)
    aprobaciones = ItemAprobacionInputSerializer(many=True, required=False, allow_null=True)


class RechazarSolicitudKitSerializer(serializers.Serializer):
    """Payload para POST /api/inventario/kit/solicitudes/{id}/rechazar/."""
    observaciones_gestor = serializers.CharField(max_length=500, allow_blank=False, trim_whitespace=True)


class ConsumoOfflineItemSerializer(serializers.Serializer):
    """Renglón individual de consumo enviado en sincronización offline."""
    client_uuid = serializers.UUIDField()
    id_medicamento = serializers.IntegerField(min_value=1)
    cantidad = serializers.IntegerField(min_value=1, required=False)
    unidades = serializers.IntegerField(min_value=1, required=False)
    origen_tipo = serializers.ChoiceField(choices=CONSUMO_ORIGENES_PERMITIDOS, required=False, default='DIRECTO')
    id_consulta = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    id_tratamiento = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    id_seguimiento_visita = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    id_visita_animal = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    # Alias cortos admitidos
    consulta = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    tratamiento = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    seguimiento = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    visita = serializers.IntegerField(min_value=1, required=False, allow_null=True)

    def validate(self, attrs):
        unidades = attrs.get('unidades')
        cantidad = attrs.get('cantidad')

        if unidades is None and cantidad is None:
            raise serializers.ValidationError("Debe especificar 'cantidad' o 'unidades'.")
        if unidades is not None and isinstance(unidades, bool):
            raise serializers.ValidationError("unidades debe ser un entero.")
        if cantidad is not None and isinstance(cantidad, bool):
            raise serializers.ValidationError("cantidad debe ser un entero.")

        # Estandarizar a 'unidades' para el servicio de consumo
        attrs['unidades'] = unidades if unidades is not None else cantidad
        attrs.pop('cantidad', None)
        return attrs


class SincronizarOfflineSerializer(serializers.Serializer):
    """Payload para POST /api/inventario/kit/sincronizar-offline/."""
    consumos = ConsumoOfflineItemSerializer(many=True, allow_empty=False)


# ============================================================================
# 2. SERIALIZADORES DE SALIDA (MODELOS Y VISTAS DE CONSULTA)
# ============================================================================

class DetalleSolicitudKitOutputSerializer(serializers.ModelSerializer):
    """Serialización de renglones de solicitudes de kit."""
    nombre_medicamento = serializers.CharField(source='id_medicamento.nombre', read_only=True)

    class Meta:
        model = SolicitudesKitDetalles
        fields = [
            'id_detalle_solicitud',
            'id_medicamento',
            'nombre_medicamento',
            'id_inventario_origen',
            'cantidad_solicitada',
            'cantidad_aprobada',
            'es_vencido',
        ]


class SolicitudKitOutputSerializer(serializers.ModelSerializer):
    """Serialización completa de cabecera y renglones de solicitudes de kit."""
    detalles = DetalleSolicitudKitOutputSerializer(many=True, read_only=True)
    nombre_veterinario = serializers.SerializerMethodField()
    nombre_gestor = serializers.SerializerMethodField()

    class Meta:
        model = SolicitudesKit
        fields = [
            'id_solicitud',
            'tipo_solicitud',
            'estado',
            'id_veterinario',
            'nombre_veterinario',
            'id_gestor',
            'nombre_gestor',
            'fecha_solicitud',
            'fecha_resolucion',
            'motivo_solicitud',
            'observaciones_gestor',
            'es_reposicion_total',
            'detalles',
        ]

    def get_nombre_veterinario(self, obj):
        if obj.id_veterinario:
            return f"{obj.id_veterinario.nombre} {obj.id_veterinario.apellido}".strip()
        return None

    def get_nombre_gestor(self, obj):
        if obj.id_gestor:
            return f"{obj.id_gestor.nombre} {obj.id_gestor.apellido}".strip()
        return None


class KardexOutputSerializer(serializers.ModelSerializer):
    """Serialización de registros de movimientos inmutables de kardex."""
    nombre_medicamento = serializers.CharField(source='id_medicamento.nombre', read_only=True)
    nombre_usuario_ejecuta = serializers.SerializerMethodField()

    class Meta:
        model = InventarioKardex
        fields = [
            'id_kardex',
            'tipo_movimiento',
            'ubicacion',
            'id_veterinario',
            'nombre_medicamento',
            'id_medicamento',
            'id_inventario',
            'cantidad',
            'saldo_anterior',
            'saldo_resultante',
            'motivo',
            'id_usuario_ejecuta',
            'nombre_usuario_ejecuta',
            'id_consulta',
            'id_tratamiento',
            'id_seguimiento_visita',
            'id_visita_animal',
            'id_solicitud_kit',
            'id_kardex_revertido',
            'client_uuid',
            'fecha_hora',
        ]

    def get_nombre_usuario_ejecuta(self, obj):
        if obj.id_usuario_ejecuta:
            return f"{obj.id_usuario_ejecuta.nombre} {obj.id_usuario_ejecuta.apellido}".strip()
        return None


class ConflictoOutputSerializer(serializers.ModelSerializer):
    """Serialización de registros de consumos rechazados por conflicto de stock."""
    nombre_veterinario = serializers.SerializerMethodField()
    nombre_medicamento = serializers.CharField(source='id_medicamento.nombre', read_only=True)
    nombre_resuelto_por = serializers.SerializerMethodField()

    class Meta:
        model = InventarioConsumosRegistro
        fields = [
            'id_consumo',
            'client_uuid',
            'id_veterinario',
            'nombre_veterinario',
            'id_medicamento',
            'nombre_medicamento',
            'cantidad_solicitada',
            'stock_disponible_al_rechazo',
            'motivo_rechazo',
            'origen_tipo',
            'id_consulta',
            'id_tratamiento',
            'id_seguimiento_visita',
            'id_visita_animal',
            'fecha_consumo',
            'fecha_resolucion_conflicto',
            'resuelto_por',
            'nombre_resuelto_por',
        ]

    def get_nombre_veterinario(self, obj):
        if obj.id_veterinario:
            return f"{obj.id_veterinario.nombre} {obj.id_veterinario.apellido}".strip()
        return None

    def get_nombre_resuelto_por(self, obj):
        if obj.resuelto_por:
            return f"{obj.resuelto_por.nombre} {obj.resuelto_por.apellido}".strip()
        return None


# ============================================================================
# 3. HELPERS COMPARTIDOS PARA HOOKS TRANSVERSALES (CLÍNICA Y ACTA DE VISITA)
# ============================================================================

class CampoPassthroughJSONField(serializers.JSONField):
    """
    Subclase de JSONField que preserva la documentación OpenAPI/Swagger sin que DRF
    intercepte strings con json.loads() antes de validar_payload_consumo_kit().
    """
    def to_internal_value(self, data):
        return data


def validar_payload_consumo_kit(
    raw_meds,
    raw_offline=False,
    id_veterinario: int | None = None,
    permitir_multipart: bool = False,
) -> tuple[list[dict], bool]:
    """
    Validador fail-fast delgado para el bloque opcional 'medicamentos_usados' y
    'sincronizacion_offline' en endpoints clínicos (Historia Clínica y Acta de Visita).
    Delega la validación de tipos, UUIDs y rol del veterinario en los helpers del
    dominio de inventario antes de que el endpoint realice efectos de lado en BD.
    """
    if raw_meds is None or raw_meds == "" or raw_meds == []:
        return [], False

    if isinstance(raw_meds, str) and not raw_meds.strip():
        return [], False

    if permitir_multipart and isinstance(raw_meds, str):
        try:
            raw_meds = json.loads(raw_meds)
        except (ValueError, TypeError) as exc:
            raise ConsumoInvalidoError(
                "El campo 'medicamentos_usados' no contiene un JSON válido."
            ) from exc
        if raw_meds is None or raw_meds == "" or raw_meds == []:
            return [], False

    if not isinstance(raw_meds, list):
        raise ConsumoInvalidoError("El campo 'medicamentos_usados' debe ser una lista.")

    if len(raw_meds) == 0:
        return [], False

    if raw_offline is None:
        es_offline = False
    elif isinstance(raw_offline, bool):
        es_offline = raw_offline
    elif permitir_multipart and isinstance(raw_offline, str) and raw_offline.strip().lower() in ('true', 'false'):
        es_offline = raw_offline.strip().lower() == 'true'
    else:
        raise ConsumoInvalidoError(
            "El campo 'sincronizacion_offline' debe ser un valor booleano explícito (true/false)."
        )

    _validar_veterinario(id_veterinario)

    consumos_normalizados = []
    uuids_vistos = set()
    med_ids_vistos = set()

    for idx, item in enumerate(raw_meds, start=1):
        if not isinstance(item, dict):
            raise ConsumoInvalidoError(
                f"El elemento #{idx} de 'medicamentos_usados' debe ser un diccionario."
            )

        uuid_obj = _parsear_client_uuid(item.get('client_uuid'))
        if uuid_obj in uuids_vistos:
            raise ConsumoInvalidoError(
                f"El client_uuid '{uuid_obj}' está duplicado dentro del mismo payload de consumo."
            )
        uuids_vistos.add(uuid_obj)

        med_id = item.get('id_medicamento')
        if not _es_entero_valido(med_id, minimo=1):
            raise CantidadInvalidaError(
                f"En 'medicamentos_usados' #{idx}, 'id_medicamento' debe ser un entero mayor a cero."
            )

        if 'unidades' not in item:
            raise CantidadInvalidaError(
                f"En 'medicamentos_usados' #{idx} (medicamento #{med_id}), falta la clave obligatoria 'unidades'."
            )

        raw_unidades = item.get('unidades')
        if not _es_entero_valido(raw_unidades, minimo=1):
            raise CantidadInvalidaError(
                f"En 'medicamentos_usados' #{idx} (medicamento #{med_id}), 'unidades' debe ser un entero mayor a cero."
            )

        med_ids_vistos.add(med_id)
        consumos_normalizados.append({
            'client_uuid': str(uuid_obj),
            'id_medicamento': med_id,
            'unidades': raw_unidades,
        })

    existentes = set(
        Medicamentos.objects.filter(pk__in=sorted(med_ids_vistos))
        .values_list('id_medicamento', flat=True)
    )
    for med_id in sorted(med_ids_vistos):
        if med_id not in existentes:
            raise MedicamentoInvalidoError(
                f"El medicamento #{med_id} no existe en el catálogo institucional."
            )

    return consumos_normalizados, es_offline


def verificar_idempotencia_consumo_clinico(
    consumos: list[dict],
    id_veterinario: int,
    origen_tipo: str,
    id_origen_esperado: int | None = None,
    post_descuento: bool = False,
) -> int | None:
    """
    Verifica la consistencia e idempotencia de los 'client_uuid' en InventarioConsumosRegistro
    tanto en el pre-check (antes de crear registros clínicos) como en el post-check
    (después de descontar_consumo_kit, para cerrar la carrera de dos reintentos simultáneos).

    - Si no existen registros previos (en pre-check), retorna None.
    - Si existen registros previos y todos coinciden en veterinario, medicamento, unidades,
      origen_tipo y pertenecen al mismo registro de origen (y a id_origen_esperado si se indicó),
      retorna dicho ID de origen.
    - En cualquier discrepancia o mezcla parcial de UUIDs, lanza ConflictoIdempotenciaConsumoError.
    """
    if not consumos:
        return None

    if origen_tipo == CONSUMO_ORIGEN_HISTORIA:
        campo_origen = 'id_consulta'
    elif origen_tipo == CONSUMO_ORIGEN_ACTA:
        campo_origen = 'id_seguimiento_visita'
    else:
        raise ConsumoInvalidoError(
            f"origen_tipo '{origen_tipo}' no soportado en verificación de idempotencia clínica."
        )

    mapa_consumos = {_parsear_client_uuid(c['client_uuid']): c for c in consumos}
    uuids_lista = list(mapa_consumos.keys())

    registros = list(
        InventarioConsumosRegistro.objects.filter(client_uuid__in=uuids_lista)
    )

    if not registros:
        if post_descuento:
            raise ConflictoIdempotenciaConsumoError(
                "No se encontraron los registros de consumo tras procesar el descuento del kit."
            )
        return None

    if (post_descuento or id_origen_esperado is None) and len(registros) != len(uuids_lista):
        raise ConflictoIdempotenciaConsumoError(
            "Conflicto de idempotencia: solo una parte de los client_uuid del lote existe previamente en el registro de consumos."
        )

    origenes_encontrados = set()
    for reg in registros:
        item = mapa_consumos.get(reg.client_uuid)
        if (
            item is None
            or reg.id_veterinario_id != id_veterinario
            or reg.id_medicamento_id != item['id_medicamento']
            or reg.cantidad_solicitada != item['unidades']
        ):
            raise ConflictoIdempotenciaConsumoError(
                f"El client_uuid '{reg.client_uuid}' ya fue registrado con parámetros distintos "
                f"(veterinario, medicamento o unidades no coinciden)."
            )

        id_origen_reg = getattr(reg, f"{campo_origen}_id")
        if reg.origen_tipo != origen_tipo or id_origen_reg is None:
            raise ConflictoIdempotenciaConsumoError(
                f"El client_uuid '{reg.client_uuid}' ya fue registrado con un origen distinto "
                f"(origen_tipo='{reg.origen_tipo}')."
            )

        if id_origen_esperado is not None and id_origen_reg != id_origen_esperado:
            raise ConflictoIdempotenciaConsumoError(
                f"El client_uuid '{reg.client_uuid}' ya está asociado a otro registro de origen "
                f"({campo_origen}_id={id_origen_reg}, esperado={id_origen_esperado})."
            )

        origenes_encontrados.add(id_origen_reg)

    if len(origenes_encontrados) > 1:
        raise ConflictoIdempotenciaConsumoError(
            "Conflicto de idempotencia: los client_uuid enviados están asociados a múltiples registros de origen distintos."
        )

    return next(iter(origenes_encontrados))


def extraer_alertas_farmacia(resultado_kit: dict | None) -> dict | None:
    """
    Retorna el bloque 'alertas_farmacia' tal cual lo entrega descontar_consumo_kit
    únicamente cuando 'conflictos_stock' no está vacío; de lo contrario retorna None
    para omitir la clave de la respuesta HTTP.
    """
    if isinstance(resultado_kit, dict):
        alertas = resultado_kit.get('alertas_farmacia')
        if isinstance(alertas, dict) and alertas.get('conflictos_stock'):
            return alertas
    return None

