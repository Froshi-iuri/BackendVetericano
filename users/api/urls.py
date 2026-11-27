from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ConfirmarCodigoRegistroView,
    GoogleLoginView,
    LoginView,
    RegisterView,
    SolicitarCodigoRegistroView,
    UsuariosViewSet,
    RolViewSet,
    SedeViewSet,
)

from users.views import solicitar_recuperacion, confirmar_recuperacion

router = DefaultRouter()
# Esto genera automáticamente las rutas GET, POST, PATCH y DELETE para /usuarios/
router.register(r'usuarios', UsuariosViewSet, basename='usuarios')
router.register(r'roles', RolViewSet, basename='roles')
router.register(r'sedes', SedeViewSet, basename='usuarios-sedes')

urlpatterns = [
    path('login/', LoginView.as_view(), name='login'),
    path('google-login/', GoogleLoginView.as_view(), name='google-login'),
    path('register/', RegisterView.as_view(), name='register'), # El que usa Angular
    path('register/solicitar-codigo/', SolicitarCodigoRegistroView.as_view(), name='register-solicitar-codigo'),
    path('register/confirmar-codigo/', ConfirmarCodigoRegistroView.as_view(), name='register-confirmar-codigo'),

    path('', include(router.urls)),
    
    path('recuperar-password/', solicitar_recuperacion, name='solicitar_recuperacion'),
    path('confirmar-password/', confirmar_recuperacion, name='confirmar_recuperacion'),
]