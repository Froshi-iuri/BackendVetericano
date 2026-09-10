from django.urls import path
from .views import ListarTiposPeticionView, IniciarPeticionView, PerfilUsuarioView

urlpatterns = [
    path('tipos/', ListarTiposPeticionView.as_view(), name='tipos-peticion'),
    path('iniciar/', IniciarPeticionView.as_view(), name='iniciar-peticion'),
    path('perfil/', PerfilUsuarioView.as_view(), name='perfil-usuario'),
]