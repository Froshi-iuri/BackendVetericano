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
from .kit_views import (
    KitMiKitView,
    KitVeterinarioView,
    KitAsignarStockView,
    KitRetiroDirectoView,
    SolicitudesKitListCreateView,
    SolicitudKitDetailView,
    SolicitudKitAprobarView,
    SolicitudKitRechazarView,
    SolicitudKitCancelarView,
    KitSincronizarOfflineView,
    KitSnapshotMovilView,
    KitAlertasView,
    KitKardexView,
    KitConflictosView,
)

router = DefaultRouter()
router.register(r'proveedores', ProveedoresViewSet, basename='proveedor')
router.register(r'compras', CompraViewSet, basename='compra')
router.register(r'detalles-compra', DetalleCompraViewSet, basename='detalle-compra')
router.register(r'salidas', SalidasViewSet, basename='salida')
router.register(r'detalles-salida', DetalleSalidaViewSet, basename='detalle-salida')
router.register(r'inventarios', InventariosViewSet, basename='inventario')

kit_urlpatterns = [
    path('mi-kit/', KitMiKitView.as_view(), name='kit-mi-kit'),
    path('veterinario/<int:id>/', KitVeterinarioView.as_view(), name='kit-veterinario-detalle'),
    path('snapshot-movil/', KitSnapshotMovilView.as_view(), name='kit-snapshot-movil'),
    path('asignar/', KitAsignarStockView.as_view(), name='kit-asignar'),
    path('retiro-directo/', KitRetiroDirectoView.as_view(), name='kit-retiro-directo'),
    path('solicitudes/', SolicitudesKitListCreateView.as_view(), name='kit-solicitudes-list-create'),
    path('solicitudes/<int:pk>/', SolicitudKitDetailView.as_view(), name='kit-solicitudes-detail'),
    path('solicitudes/<int:pk>/aprobar/', SolicitudKitAprobarView.as_view(), name='kit-solicitudes-aprobar'),
    path('solicitudes/<int:pk>/rechazar/', SolicitudKitRechazarView.as_view(), name='kit-solicitudes-rechazar'),
    path('solicitudes/<int:pk>/cancelar/', SolicitudKitCancelarView.as_view(), name='kit-solicitudes-cancelar'),
    path('sincronizar-offline/', KitSincronizarOfflineView.as_view(), name='kit-sincronizar-offline'),
    path('alertas/', KitAlertasView.as_view(), name='kit-alertas'),
    path('kardex/', KitKardexView.as_view(), name='kit-kardex'),
    path('conflictos/', KitConflictosView.as_view(), name='kit-conflictos'),
]

urlpatterns = [
    path('', include(router.urls)),
    path('kit/', include(kit_urlpatterns)),
]
