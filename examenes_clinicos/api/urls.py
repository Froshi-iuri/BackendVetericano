from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ExamenViewSet,
    ProcedimientoRealizadoViewSet,
)

router = DefaultRouter()
router.register(r'examenes', ExamenViewSet, basename='examen')
router.register(r'procedimientos', ProcedimientoRealizadoViewSet, basename='procedimiento')

urlpatterns = [
    path('', include(router.urls)),
]
