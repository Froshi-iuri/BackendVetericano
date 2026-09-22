from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    MedicamentoViewSet,
    MedicamentosViewSet,
    AdministracionMedicamentoViewSet,
    TratamientoMedicamentosViewSet,
)

router = DefaultRouter()
router.register(r'medicamentos', MedicamentoViewSet, basename='medicamento')
router.register(r'medicamentos-catalogo', MedicamentosViewSet, basename='medicamentos-catalogo')
router.register(r'administraciones', AdministracionMedicamentoViewSet, basename='administracion-medicamento')
router.register(r'tratamiento-medicamentos', TratamientoMedicamentosViewSet, basename='tratamiento-medicamento')

urlpatterns = [
    path('', include(router.urls)),
]
