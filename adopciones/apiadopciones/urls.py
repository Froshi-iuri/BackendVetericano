from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AdopcionAnimalViewSet, SolicitudAdopcionViewSet

router = DefaultRouter()
router.register(r'adopciones', AdopcionAnimalViewSet, basename='adopcion')
router.register(r'solicitudes-adopcion', SolicitudAdopcionViewSet, basename='solicitud-adopcion')

urlpatterns = [
    path('', include(router.urls)),
]