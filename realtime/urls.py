from django.urls import path

from realtime import views

urlpatterns = [
    path('', views.NotificacionesListView.as_view(), name='notificaciones-lista'),
    path('contador/', views.NotificacionesContadorView.as_view(), name='notificaciones-contador'),
    path('leer-todas/', views.NotificacionesLeerTodasView.as_view(), name='notificaciones-leer-todas'),
    path('dispositivos/', views.DispositivoRegistrarView.as_view(), name='notificaciones-dispositivos'),
    path('dispositivos/desactivar/', views.DispositivoDesactivarView.as_view(),
         name='notificaciones-dispositivos-desactivar'),
    path('<str:clave>/leer/', views.NotificacionLeerView.as_view(), name='notificaciones-leer'),
]
