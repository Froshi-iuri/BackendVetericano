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
    DashboardJuridicoView,
    NotificacionesJuridicoView,
    MarcarNotificacionLeidaView,
    HistorialAsignacionesPorPeticionView,
    FormularioActaSchemaView,
    DictadoVozActaView,
    DetectarDuplicadosPeticionView,
)

urlpatterns = [
    # Rutas existentes
    path('tipos/', ListarTiposPeticionView.as_view(), name='tipos-peticion'),
    path('listar/', ListarPeticionesView.as_view(), name='listar-peticiones'),
    path('iniciar/', IniciarPeticionView.as_view(), name='iniciar-peticion'),
    path('detectar-duplicados/', DetectarDuplicadosPeticionView.as_view(), name='detectar-duplicados-peticion'),
    path('perfil/', PerfilUsuarioView.as_view(), name='perfil-usuario'),

    # Dashboard consolidado del rol Jurídico (Requerimiento 1)
    path('dashboard-juridico/', DashboardJuridicoView.as_view(), name='dashboard-juridico'),

    # Notificaciones del módulo Jurídico (SCRUM-212 / Requerimiento 5)
    path('notificaciones/', NotificacionesJuridicoView.as_view(), name='notificaciones-juridico'),
    path('notificaciones/<int:id>/marcar-leida/', MarcarNotificacionLeidaView.as_view(), name='marcar-notificacion-leida'),
    path('notificaciones/marcar-leida/', MarcarNotificacionLeidaView.as_view(), name='marcar-todas-notificaciones-leidas'),

    # Rutas del acta de visita
    # IMPORTANTE: las rutas fijas van ANTES de <int:id_seguimiento>/ para
    # que 'funcionarios', 'crear', 'formulario-schema', 'dictado-voz' y 'listar' no se confundan con un ID.
    path('seguimiento/formulario-schema/', FormularioActaSchemaView.as_view(), name='formulario-acta-schema'),
    path('seguimiento/dictado-voz/', DictadoVozActaView.as_view(), name='dictado-voz-acta'),
    path('seguimiento/funcionarios/', FuncionariosVisitaView.as_view(), name='funcionarios-visita'),
    path('seguimiento/crear/', SeguimientoPeticionesVisitaCreateView.as_view(), name='crear-seguimiento'),
    path('seguimiento/listar/', ListarSeguimientoPeticionesVisitaView.as_view(), name='listar-seguimientos'),
    path('seguimiento/', ListarSeguimientoPeticionesVisitaView.as_view(), name='listar-seguimientos-raiz'),
    path('seguimiento/<int:id_seguimiento>/', SeguimientoPeticionesVisitaDetailView.as_view(), name='detalle-seguimiento'),

    # Detalle, historial y operaciones de la petición
    path('<int:id_peticion>/estado/', ActualizarEstadoPeticionView.as_view(), name='actualizar-estado-peticion'),
    path('<int:id_peticion>/asignar/', AsignarPeticionView.as_view(), name='asignar-peticion'),
    path('<int:id_peticion>/historial-asignaciones/', HistorialAsignacionesPorPeticionView.as_view(), name='historial-asignaciones-peticion'),
    path('<int:id_peticion>/seguimientos/', ListarSeguimientosPorPeticionView.as_view(), name='seguimientos-por-peticion'),
    path('<int:id_peticion>/', DetallePeticionView.as_view(), name='detalle-peticion'),
]



# Rutas adicionales del módulo de Jurídico (estados, notificaciones por usuario e historial completo)
from .views import (
    ListarEstadosPeticionView,
    HistorialPeticionView,
    NotificacionesPeticionesListView,
    MarcarNotificacionPeticionLeidaView,
    MarcarTodasNotificacionesLeidasView,
)

urlpatterns += [
    path('estados/', ListarEstadosPeticionView.as_view(), name='estados-peticion'),
    path('notificaciones/usuario/', NotificacionesPeticionesListView.as_view(), name='notificaciones-peticiones'),
    path('notificaciones/leer-todas/', MarcarTodasNotificacionesLeidasView.as_view(), name='leer-todas-notificaciones'),
    path('notificaciones/<int:id_notificacion>/leer/', MarcarNotificacionPeticionLeidaView.as_view(), name='leer-notificacion'),
    path('<int:id_peticion>/historial/', HistorialPeticionView.as_view(), name='historial-peticion'),
]
