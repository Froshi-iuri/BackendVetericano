from django.urls import path
from .views import (
    ListarTiposPeticionView, IniciarPeticionView, PerfilUsuarioView,
    FuncionariosVisitaView,
    SeguimientoPeticionesVisitaCreateView,
    SeguimientoPeticionesVisitaDetailView,
    AsignarPeticionView,
    ListarPeticionesView,
)

urlpatterns = [
    # Rutas existentes — NO MODIFICADAS
    path('tipos/', ListarTiposPeticionView.as_view(), name='tipos-peticion'),
    path('listar/', ListarPeticionesView.as_view(), name='listar-peticiones'),
    path('iniciar/', IniciarPeticionView.as_view(), name='iniciar-peticion'),
    path('perfil/', PerfilUsuarioView.as_view(), name='perfil-usuario'),

    # Rutas nuevas del acta de visita
    path('seguimiento/funcionarios/', FuncionariosVisitaView.as_view(), name='funcionarios-visita'),
    path('seguimiento/crear/', SeguimientoPeticionesVisitaCreateView.as_view(), name='crear-seguimiento'),
    path('seguimiento/<int:id_seguimiento>/', SeguimientoPeticionesVisitaDetailView.as_view(), name='detalle-seguimiento'),
    
    # Asignación
    path('<int:id_peticion>/asignar/', AsignarPeticionView.as_view(), name='asignar-peticion'),
]
