from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    HistoriaClinicaViewSet,
    ConsultaViewSet,
    HospitalizacionSeresSintientesViewSet,
    TratamientosViewSet,
    DiagnosticoViewSet,
    SeguimientosClinicosViewSet,
    DiagnosticoPatologiaViewSet,
    SeguimientoHospitalarioViewSet,
)

router = DefaultRouter()
router.register(r'historias', HistoriaClinicaViewSet, basename='historia-clinica')
router.register(r'consultas', ConsultaViewSet, basename='consulta')
router.register(r'hospitalizaciones', HospitalizacionSeresSintientesViewSet, basename='hospitalizacion')
router.register(r'tratamientos', TratamientosViewSet, basename='tratamiento')
router.register(r'diagnosticos', DiagnosticoViewSet, basename='diagnostico')
router.register(r'seguimientos', SeguimientosClinicosViewSet, basename='seguimiento-clinico')
router.register(r'diagnostico-patologias', DiagnosticoPatologiaViewSet, basename='diagnostico-patologia')
router.register(r'seguimientos-hospitalarios', SeguimientoHospitalarioViewSet, basename='seguimiento-hospitalario')

urlpatterns = [
    path('', include(router.urls)),
]
