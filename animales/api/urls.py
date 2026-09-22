from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ResponsablesViewSet,
    AnimalViewSet,
    EstadosAnimalViewSet,
    AnimalResponsableViewSet,
)

router = DefaultRouter()
router.register(r'responsables', ResponsablesViewSet, basename='responsable')
router.register(r'animales', AnimalViewSet, basename='animal')
router.register(r'estados', EstadosAnimalViewSet, basename='estado-animal')
router.register(r'animal-responsables', AnimalResponsableViewSet, basename='animal-responsable')

urlpatterns = [
    path('', include(router.urls)),
]
