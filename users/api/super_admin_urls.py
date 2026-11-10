from django.urls import path
from .views import (
    SuperAdminCrearAdministradorView,
    SuperAdminListarAdministradoresView,
    SuperAdminDetalleAdministradorView,
    SuperAdminReportePeticionesView,
    SuperAdminReporteAtendidasView,
    SuperAdminReporteEstadisticasView,
)

urlpatterns = [
    # Gestión de Administradores (CRUD)
    path(
        'administradores/crear/',
        SuperAdminCrearAdministradorView.as_view(),
        name='super-admin-administradores-crear',
    ),
    path(
        'administradores/',
        SuperAdminListarAdministradoresView.as_view(),
        name='super-admin-administradores-listar',
    ),
    path(
        'administradores/<int:id>/',
        SuperAdminDetalleAdministradorView.as_view(),
        name='super-admin-administradores-detalle',
    ),

    # Reportes del Super Administrador
    path(
        'reportes/peticiones/',
        SuperAdminReportePeticionesView.as_view(),
        name='super-admin-reportes-peticiones',
    ),
    path(
        'reportes/atendidas/',
        SuperAdminReporteAtendidasView.as_view(),
        name='super-admin-reportes-atendidas',
    ),
    path(
        'reportes/estadisticas/',
        SuperAdminReporteEstadisticasView.as_view(),
        name='super-admin-reportes-estadisticas',
    ),
]
