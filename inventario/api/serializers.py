from rest_framework import serializers
from django.db import transaction

from inventario.models import Proveedores, Compra, DetalleCompra, Salidas, DetalleSalida, Inventarios
from inventario.services.compras_service import (
    ESTADOS_LOTE_PERMITIDOS,
    agregar_detalle_compra,
    lote_tiene_historial_operativo,
    registrar_compra,
    validar_item_detalle_compra,
    verificar_compra_sin_dependencias,
    verificar_detalle_compra_sin_dependencias,
)
from inventario.services.exceptions import (
    ConflictoOperacionInventarioError,
    InventarioDomainError,
    PermisoGestorRequeridoError,
)
from inventario.services.kit_service import _es_entero_valido


MENSAJE_CREACION_DIRECTA_LOTE_NO_PERMITIDA = (
    "No se permite crear lotes directamente en /api/inventario/inventarios/. Las entradas a "
    "Bodega General deben registrarse mediante el flujo autorizado de compras "
    "(POST /api/inventario/compras/ o POST /api/inventario/detalles-compra/), que crea el lote "
    "junto con su movimiento ENTRADA_COMPRA en Kardex."
)


def _obtener_id_usuario_request(serializer_self) -> int:
    request = serializer_self.context.get('request')
    user = getattr(request, 'user', None) if request else None
    if not user or not getattr(user, 'is_authenticated', False):
        raise PermisoGestorRequeridoError("Se requiere un usuario gestor autenticado para realizar esta operación.")
    return user.pk


class ProveedoresSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedores
        fields = '__all__'


class DetalleCompraSerializer(serializers.ModelSerializer):
    fecha_vencimiento = serializers.DateField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = DetalleCompra
        fields = ['id_detalle_compra', 'id_compra', 'id_medicamento', 'cantidad', 'fecha_vencimiento']
        extra_kwargs = {
            'id_compra': {'required': True},
            'id_medicamento': {'required': True},
            'cantidad': {'required': True},
        }

    def validate(self, attrs):
        raw = self.initial_data if isinstance(getattr(self, 'initial_data', None), dict) else {}

        for campo_int in ('id_compra', 'id_medicamento', 'cantidad'):
            if campo_int in raw and isinstance(raw.get(campo_int), bool):
                raise serializers.ValidationError(
                    {campo_int: "No se permiten valores booleanos."},
                    code='DATO_BOOLEANO_NO_PERMITIDO',
                )

        if self.instance is None:
            medicamento = attrs.get('id_medicamento')
            cantidad = attrs.get('cantidad')
            if medicamento is None or not medicamento.activo:
                raise serializers.ValidationError(
                    {"id_medicamento": "El medicamento debe existir y encontrarse activo."},
                    code='MEDICAMENTO_INVALIDO',
                )
            if not _es_entero_valido(cantidad, minimo=1):
                raise serializers.ValidationError(
                    {"cantidad": "La cantidad debe ser un número entero mayor a cero."},
                    code='CANTIDAD_INVALIDA',
                )
        else:
            if 'cantidad' in attrs and not _es_entero_valido(attrs.get('cantidad'), minimo=1):
                raise serializers.ValidationError(
                    {"cantidad": "La cantidad debe ser un número entero mayor a cero."},
                    code='CANTIDAD_INVALIDA',
                )
            if 'id_medicamento' in attrs and (attrs.get('id_medicamento') is None or not attrs['id_medicamento'].activo):
                raise serializers.ValidationError(
                    {"id_medicamento": "El medicamento debe existir y encontrarse activo."},
                    code='MEDICAMENTO_INVALIDO',
                )

        return super().validate(attrs)

    def create(self, validated_data):
        usuario_id = _obtener_id_usuario_request(self)
        compra = validated_data['id_compra']
        medicamento = validated_data['id_medicamento']
        cantidad = validated_data['cantidad']
        fecha_vencimiento = validated_data.get('fecha_vencimiento')
        return agregar_detalle_compra(
            id_usuario=usuario_id,
            id_compra=compra.pk,
            id_medicamento=medicamento.pk,
            cantidad=cantidad,
            fecha_vencimiento=fecha_vencimiento,
        )

    @transaction.atomic
    def update(self, instance, validated_data):
        raw = self.initial_data if isinstance(getattr(self, 'initial_data', None), dict) else {}
        campos_inmutables = {'id_compra', 'id_medicamento', 'cantidad'}
        if any(campo in raw for campo in campos_inmutables) or any(campo in validated_data for campo in campos_inmutables):
            verificar_detalle_compra_sin_dependencias(instance.pk, "modificar")
        validated_data.pop('fecha_vencimiento', None)
        return super().update(instance, validated_data)


class CompraSerializer(serializers.ModelSerializer):
    proveedor_nombre = serializers.CharField(source='id_proveedor.nombre', read_only=True)
    proveedor_telefono = serializers.CharField(source='id_proveedor.telefono', read_only=True)
    total_productos = serializers.SerializerMethodField()
    total_unidades = serializers.SerializerMethodField()
    detalles = DetalleCompraSerializer(source='detallecompra_set', many=True, read_only=True)

    class Meta:
        model = Compra
        fields = ['id_compra', 'id_proveedor', 'proveedor_nombre', 'proveedor_telefono', 'fecha', 'total_productos', 'total_unidades', 'detalles']

    def get_total_productos(self, obj):
        return obj.detallecompra_set.count()

    def get_total_unidades(self, obj):
        return sum(detalle.cantidad for detalle in obj.detallecompra_set.all() if detalle.cantidad)

    def validate(self, attrs):
        raw = self.initial_data if isinstance(getattr(self, 'initial_data', None), dict) else {}

        if isinstance(raw.get('id_proveedor'), bool):
            raise serializers.ValidationError(
                {"id_proveedor": "Identificador de proveedor inválido."},
                code='PROVEEDOR_INVALIDO',
            )

        if self.instance is None:
            if 'detalles' not in raw:
                raise serializers.ValidationError(
                    {"detalles": "Debe enviar la lista de detalles de compra."},
                    code='DETALLES_COMPRA_REQUERIDOS',
                )
            detalles_data = raw.get('detalles')
            if not isinstance(detalles_data, list) or len(detalles_data) == 0:
                raise serializers.ValidationError(
                    {"detalles": "El campo 'detalles' debe ser una lista no vacía."},
                    code='DETALLES_COMPRA_REQUERIDOS',
                )
            try:
                for item in detalles_data:
                    validar_item_detalle_compra(item)
            except InventarioDomainError as exc:
                raise serializers.ValidationError({"detalles": exc.message}, code=exc.codigo) from exc
        else:
            campos_simulacion = [c for c in ('estado', 'motivo_anulacion') if c in raw]
            if campos_simulacion:
                raise serializers.ValidationError(
                    f"La tabla 'compra' no soporta anulación ni campos {campos_simulacion}.",
                    code='CAMPOS_COMPRA_NO_SOPORTADOS',
                )
            if 'detalles' in raw:
                raise ConflictoOperacionInventarioError(
                    "No se permite modificar los detalles de una compra registrada mediante PATCH/PUT /compras/{id}/.",
                    codigo='COMPRA_DETALLES_INMUTABLES',
                )

        return super().validate(attrs)

    def create(self, validated_data):
        usuario_id = _obtener_id_usuario_request(self)
        detalles_data = self.initial_data.get('detalles') if isinstance(self.initial_data, dict) else None
        proveedor = validated_data['id_proveedor']
        return registrar_compra(
            id_usuario=usuario_id,
            id_proveedor=proveedor.pk,
            detalles=detalles_data,
        )

    @transaction.atomic
    def update(self, instance, validated_data):
        compra_bloqueada = Compra.objects.select_for_update(no_key=True).get(pk=instance.pk)
        nuevo_proveedor = validated_data.get('id_proveedor')
        if nuevo_proveedor is not None and nuevo_proveedor.pk != compra_bloqueada.id_proveedor_id:
            verificar_compra_sin_dependencias(compra_bloqueada.pk, "cambiar el proveedor de")
        return super().update(compra_bloqueada, validated_data)


class SalidasSerializer(serializers.ModelSerializer):
    class Meta:
        model = Salidas
        fields = '__all__'


class DetalleSalidaSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleSalida
        fields = '__all__'

    def create(self, validated_data):
        raise ConflictoOperacionInventarioError(
            "El endpoint legacy de detalles-salida está bloqueado porque modificaba existencias sin trazabilidad en Kardex.",
            codigo="SALIDA_LEGACY_BLOQUEADA",
        )


class InventariosSerializer(serializers.ModelSerializer):
    class Meta:
        model = Inventarios
        fields = '__all__'

    def create(self, validated_data):
        raise ConflictoOperacionInventarioError(
            MENSAJE_CREACION_DIRECTA_LOTE_NO_PERMITIDA,
            codigo="CREACION_DIRECTA_LOTE_NO_PERMITIDA",
        )

    def validate(self, attrs):
        if self.instance is not None:
            raw = self.initial_data if isinstance(getattr(self, 'initial_data', None), dict) else None
            if raw is None:
                raise serializers.ValidationError(
                    "El cuerpo de actualización debe ser un objeto JSON válido.",
                    code='PAYLOAD_LOTE_INVALIDO',
                )

            claves_recibidas = set(raw.keys())
            claves_permitidas = {'estado', 'fecha_vencimiento'}
            claves_no_permitidas = claves_recibidas - claves_permitidas
            if claves_no_permitidas:
                raise serializers.ValidationError(
                    f"Campos no permitidos al editar lote de bodega: {sorted(list(claves_no_permitidas))}. "
                    f"Solo se permite actualizar 'estado' o 'fecha_vencimiento'.",
                    code='CAMPO_LOTE_NO_PERMITIDO',
                )
            if not claves_recibidas:
                raise serializers.ValidationError(
                    "Debe especificar al menos 'estado' o 'fecha_vencimiento'.",
                    code='PATCH_LOTE_VACIO',
                )

            if 'estado' in attrs:
                nuevo_estado = attrs.get('estado')
                if nuevo_estado not in ESTADOS_LOTE_PERMITIDOS:
                    raise serializers.ValidationError(
                        f"Estado de lote inválido '{nuevo_estado}'. Valores permitidos: {list(ESTADOS_LOTE_PERMITIDOS)}.",
                        code='ESTADO_LOTE_INVALIDO',
                    )

        return super().validate(attrs)

    @transaction.atomic
    def update(self, instance, validated_data):
        instancia_bloqueada = Inventarios.objects.select_for_update(no_key=True).get(pk=instance.pk)

        if (
            'fecha_vencimiento' in validated_data
            and validated_data['fecha_vencimiento'] != instancia_bloqueada.fecha_vencimiento
            and lote_tiene_historial_operativo(instancia_bloqueada)
        ):
            raise ConflictoOperacionInventarioError(
                "No se puede modificar la fecha de vencimiento de un lote que ya tiene historial en kits, solicitudes o Kardex.",
                codigo='LOTE_CON_HISTORIAL_VENCIMIENTO',
            )

        if 'estado' in validated_data:
            nuevo_estado = validated_data['estado']
            stock_actual = instancia_bloqueada.cantidad_actual or 0
            if nuevo_estado == 'Disponible' and stock_actual <= 0:
                raise ConflictoOperacionInventarioError(
                    "No se puede establecer estado 'Disponible' en un lote sin existencias (cantidad_actual <= 0).",
                    codigo='ESTADO_LOTE_INCONSISTENTE',
                )
            if nuevo_estado == 'Agotado' and stock_actual > 0:
                raise ConflictoOperacionInventarioError(
                    f"No se puede establecer estado 'Agotado' mientras el lote conserve existencias ({stock_actual}).",
                    codigo='ESTADO_LOTE_INCONSISTENTE',
                )

        return super().update(instancia_bloqueada, validated_data)

