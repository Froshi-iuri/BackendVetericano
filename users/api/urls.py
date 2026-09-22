from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    LoginView,
    RegisterView,
    UsuariosViewSet,
    RolViewSet
)

from users.views import solicitar_recuperacion, confirmar_recuperacion

router = DefaultRouter()
# Esto genera automáticamente las rutas GET, POST, PATCH y DELETE para /usuarios/
router.register(r'usuarios', UsuariosViewSet, basename='usuarios')
router.register(r'roles', RolViewSet, basename='roles')

urlpatterns = [
    path('login/', LoginView.as_view(), name='login'),
    path('register/', RegisterView.as_view(), name='register'), # El que usa Angular

    path('', include(router.urls)),
    
    path('recuperar-password/', solicitar_recuperacion, name='solicitar_recuperacion'),
    path('confirmar-password/', confirmar_recuperacion, name='confirmar_recuperacion'),
]