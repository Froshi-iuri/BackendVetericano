from rest_framework import serializers
from django.db import transaction
from inventario.models import Proveedores, Compra, DetalleCompra, Salidas, DetalleSalida, Inventarios


class ProveedoresSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedores
        fields = '__all__'


class DetalleCompraSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleCompra
        fields = '__all__'
        extra_kwargs = {'id_compra': {'required': False}}


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

    @transaction.atomic
    def create(self, validated_data):
        # Extraemos los detalles desde el initial_data, ya que no son validados automáticamente porque read_only=True en el campo o están fuera de validated_data
        detalles_data = self.initial_data.get('detalles', [])
        
        compra = Compra.objects.create(**validated_data)
        
        for detalle_data in detalles_data:
            medicamento_id = detalle_data.get('id_medicamento')
            cantidad = detalle_data.get('cantidad', 0)
            precio = detalle_data.get('precio_unitario', 0)
            
            # Crear detalle de compra
            detalle = DetalleCompra.objects.create(
                id_compra=compra,
                id_medicamento_id=medicamento_id,
                cantidad=cantidad,
                precio_unitario=precio
            )
            
            # Crear lote en el inventario automáticamente
            Inventarios.objects.create(
                id_detalle_compra=detalle,
                cantidad_actual=cantidad,
                estado='Disponible'
            )
            
        return compra


class SalidasSerializer(serializers.ModelSerializer):
    class Meta:
        model = Salidas
        fields = '__all__'


class DetalleSalidaSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleSalida
        fields = '__all__'

    @transaction.atomic
    def create(self, validated_data):
        from django.db.models import Q
        
        # 1. Crear el registro de salida
        detalle_salida = super().create(validated_data)
        
        cantidad_a_descontar = detalle_salida.cantidad
        if not cantidad_a_descontar or cantidad_a_descontar <= 0:
            return detalle_salida
            
        # 2. Identificar qué medicamento es
        medicamento = detalle_salida.id_administracion.id_medicamento
        
        # 3. Buscar los lotes de inventario que correspondan a este medicamento y tengan stock
        # (Puede estar enlazado por compra o por otra salida anterior según la BD)
        lotes = Inventarios.objects.filter(
            Q(id_detalle_compra__id_medicamento=medicamento) | 
            Q(id_detalle_salida__id_administracion__id_medicamento=medicamento),
            cantidad_actual__gt=0
        ).order_by('id_inventario')  # Orden FIFO (los más viejos primero)
        
        # 4. Validar si hay stock suficiente
        total_stock = sum(lote.cantidad_actual for lote in lotes)
        if cantidad_a_descontar > total_stock:
            raise serializers.ValidationError({
                "error": f"Stock insuficiente. Intentaste sacar {cantidad_a_descontar}, pero solo hay {total_stock} disponibles."
            })
            
        # 5. Descontar en cascada (FIFO)
        for lote in lotes:
            if cantidad_a_descontar <= 0:
                break
                
            if lote.cantidad_actual >= cantidad_a_descontar:
                lote.cantidad_actual -= cantidad_a_descontar
                lote.save()
                cantidad_a_descontar = 0
            else:
                cantidad_a_descontar -= lote.cantidad_actual
                lote.cantidad_actual = 0
                lote.save()
                
        return detalle_salida


class InventariosSerializer(serializers.ModelSerializer):
    class Meta:
        model = Inventarios
        fields = '__all__'
