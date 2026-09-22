from django.contrib import admin
from .models import Proveedores, Compra, Salidas, DetalleCompra, DetalleSalida, Inventarios


@admin.register(Proveedores)
class ProveedoresAdmin(admin.ModelAdmin):
    list_display = ('id_proveedor', 'nombre', 'telefono', 'email', 'activo')
    search_fields = ('nombre', 'email')
    list_filter = ('activo',)


@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    list_display = ('id_compra', 'id_proveedor', 'fecha')


@admin.register(Salidas)
class SalidasAdmin(admin.ModelAdmin):
    list_display = ('id_salida', 'fecha', 'motivo')


@admin.register(DetalleCompra)
class DetalleCompraAdmin(admin.ModelAdmin):
    list_display = ('id_detalle_compra', 'id_compra', 'id_medicamento', 'cantidad', 'precio_unitario')


@admin.register(DetalleSalida)
class DetalleSalidaAdmin(admin.ModelAdmin):
    list_display = ('id_detalle_salida', 'id_usuario', 'id_administracion', 'cantidad')


@admin.register(Inventarios)
class InventariosAdmin(admin.ModelAdmin):
    list_display = ('id_inventario', 'cantidad_actual', 'fecha_vencimiento', 'estado')
