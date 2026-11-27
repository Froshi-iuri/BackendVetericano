from django.urls import path

from triaje_ia.views import (
    TriajeDecisionPeticionView,
    TriajeDetallePeticionView,
    TriajeInternoCargaVeterinariosView,
    TriajeInternoContextoPeticionView,
    TriajeInternoDuplicadosView,
    TriajeInternoGuardarResultadoView,
    TriajeInternoReintentarPendientesView,
    TriajeReintentarPeticionView,
)

urlpatterns = [
    # Endpoints internos exclusivos para el orquestador n8n (token de servicio)
    path(
        'interno/peticiones/<int:id_peticion>/',
        TriajeInternoContextoPeticionView.as_view(),
        name='triaje-interno-contexto-peticion',
    ),
    path(
        'interno/peticiones/<int:id_peticion>/duplicados/',
        TriajeInternoDuplicadosView.as_view(),
        name='triaje-interno-duplicados',
    ),
    path(
        'interno/peticiones/<int:id_peticion>/resultado/',
        TriajeInternoGuardarResultadoView.as_view(),
        name='triaje-interno-guardar-resultado',
    ),
    path(
        'interno/veterinarios/carga/',
        TriajeInternoCargaVeterinariosView.as_view(),
        name='triaje-interno-carga-veterinarios',
    ),
    path(
        'interno/reintentar-pendientes/',
        TriajeInternoReintentarPendientesView.as_view(),
        name='triaje-interno-reintentar-pendientes',
    ),
    # Endpoints de consulta/operación para roles Jurídico y Administrador (JWT)
    path(
        'peticiones/<int:id_peticion>/',
        TriajeDetallePeticionView.as_view(),
        name='triaje-detalle-peticion',
    ),
    path(
        'peticiones/<int:id_peticion>/reintentar/',
        TriajeReintentarPeticionView.as_view(),
        name='triaje-reintentar-peticion',
    ),
    path(
        'peticiones/<int:id_peticion>/decision/',
        TriajeDecisionPeticionView.as_view(),
        name='triaje-decision-peticion',
    ),
]
