from django.urls import path, include
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
    path('login/', LoginView.as_view(), name='login'),
    path('register/', RegisterView.as_view(), name='register'),

    path('', include(router.urls)),
]