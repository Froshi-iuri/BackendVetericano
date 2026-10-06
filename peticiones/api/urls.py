from django.urls import path
from .views import (
    ListarTiposPeticionView, IniciarPeticionView, PerfilUsuarioView,
    FuncionariosVisitaView,
    SeguimientoPeticionesVisitaCreateView,
    SeguimientoPeticionesVisitaDetailView,
    ListarSeguimientoPeticionesVisitaView,
    ListarSeguimientosPorPeticionView,
    AsignarPeticionView,
    ListarPeticionesView,
    DetallePeticionView,
    ActualizarEstadoPeticionView,
)

urlpatterns = [
    # Rutas existentes
    path('tipos/', ListarTiposPeticionView.as_view(), name='tipos-peticion'),
    path('listar/', ListarPeticionesView.as_view(), name='listar-peticiones'),
    path('iniciar/', IniciarPeticionView.as_view(), name='iniciar-peticion'),
    path('perfil/', PerfilUsuarioView.as_view(), name='perfil-usuario'),

    # Rutas nuevas del acta de visita
    # IMPORTANTE: las rutas fijas van ANTES de <int:id_seguimiento>/ para
    # que 'funcionarios', 'crear' y 'listar' no se confundan con un ID.
    path('seguimiento/funcionarios/', FuncionariosVisitaView.as_view(), name='funcionarios-visita'),
    path('seguimiento/crear/', SeguimientoPeticionesVisitaCreateView.as_view(), name='crear-seguimiento'),
    path('seguimiento/listar/', ListarSeguimientoPeticionesVisitaView.as_view(), name='listar-seguimientos'),
    path('seguimiento/', ListarSeguimientoPeticionesVisitaView.as_view(), name='listar-seguimientos-raiz'),
    path('seguimiento/<int:id_seguimiento>/', SeguimientoPeticionesVisitaDetailView.as_view(), name='detalle-seguimiento'),

    # Detalle y operaciones de la petición
    path('<int:id_peticion>/estado/', ActualizarEstadoPeticionView.as_view(), name='actualizar-estado-peticion'),
    path('<int:id_peticion>/asignar/', AsignarPeticionView.as_view(), name='asignar-peticion'),
    path('<int:id_peticion>/seguimientos/', ListarSeguimientosPorPeticionView.as_view(), name='seguimientos-por-peticion'),
    path('<int:id_peticion>/', DetallePeticionView.as_view(), name='detalle-peticion'),
]
