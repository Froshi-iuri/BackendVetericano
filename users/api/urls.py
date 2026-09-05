from django.urls import path, include
from .views import LoginView, RegisterView
# AnaC
from users.views import solicitar_recuperacion, confirmar_recuperacion
from rest_framework.routers import DefaultRouter

from .views import (
    LoginView,
    RegisterView,
    UsuariosViewSet,
    RolViewSet
)


router = DefaultRouter()

router.register(r'usuarios', UsuariosViewSet, basename='usuarios')
router.register(r'roles', RolViewSet, basename='roles')

urlpatterns = [
    path('registro/', RegistroView.as_view(), name='registro'),
    path('login/', LoginView.as_view(), name='login'),
    path('register/', RegisterView.as_view(), name='register'),

    path('', include(router.urls)),
    # AnaC
    path('recuperar-password/', solicitar_recuperacion, name='solicitar_recuperacion'),
    path('confirmar-password/', confirmar_recuperacion, name='confirmar_recuperacion'),
]