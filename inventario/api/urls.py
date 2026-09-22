from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProveedoresViewSet,
    CompraViewSet,
    DetalleCompraViewSet,
    SalidasViewSet,
    DetalleSalidaViewSet,
    InventariosViewSet,
)

router = DefaultRouter()
router.register(r'proveedores', ProveedoresViewSet, basename='proveedor')
router.register(r'compras', CompraViewSet, basename='compra')
router.register(r'detalles-compra', DetalleCompraViewSet, basename='detalle-compra')
router.register(r'salidas', SalidasViewSet, basename='salida')
router.register(r'detalles-salida', DetalleSalidaViewSet, basename='detalle-salida')
router.register(r'inventarios', InventariosViewSet, basename='inventario')

urlpatterns = [
    path('', include(router.urls)),
]
