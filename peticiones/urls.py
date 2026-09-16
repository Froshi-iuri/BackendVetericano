from django.urls import path
from .views import (
    PeticionListCreateView,
    PeticionDetailView,
    ListarTiposPeticionView,
    IniciarPeticionView,
)

urlpatterns = [
    # Endpoint principal para listar (GET) y crear (POST) peticiones
    path('', PeticionListCreateView.as_view(), name='peticiones-list-create'),

    # Endpoint para ver detalle de una petición por ID (GET)
    path('<int:pk>/', PeticionDetailView.as_view(), name='peticion-detalle'),

    # Endpoints adicionales / retrocompatibilidad
    path('tipos/', ListarTiposPeticionView.as_view(), name='tipos-peticion'),
    path('iniciar/', IniciarPeticionView.as_view(), name='iniciar-peticion'),
]