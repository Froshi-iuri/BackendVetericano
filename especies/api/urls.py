from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EspecieViewSet, RazaViewSet

router = DefaultRouter()
router.register(r'especies', EspecieViewSet, basename='especie')
router.register(r'razas', RazaViewSet, basename='raza')

urlpatterns = [
    path('', include(router.urls)),
]
