from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProcedimientosCatalogoViewSet,
    ProcedimientosRealizadosViewSet,
)

router = DefaultRouter()
router.register(r'catalogo', ProcedimientosCatalogoViewSet, basename='catalogo')
router.register(r'realizados', ProcedimientosRealizadosViewSet, basename='realizados')

urlpatterns = [
    path('', include(router.urls)),
]
