from rest_framework import serializers
from inventario.models import Proveedores, Compra, DetalleCompra, Salidas, DetalleSalida, Inventarios


class ProveedoresSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedores
        fields = '__all__'


class CompraSerializer(serializers.ModelSerializer):
    class Meta:
        model = Compra
        fields = '__all__'


class DetalleCompraSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleCompra
        fields = '__all__'


class SalidasSerializer(serializers.ModelSerializer):
    class Meta:
        model = Salidas
        fields = '__all__'


class DetalleSalidaSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleSalida
        fields = '__all__'


class InventariosSerializer(serializers.ModelSerializer):
    class Meta:
        model = Inventarios
        fields = '__all__'
