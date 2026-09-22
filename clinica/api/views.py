from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from clinica.models import (
    HistoriaClinica,
    Consulta,
    HospitalizacionSeresSintientes,
    Tratamientos,
    Diagnostico,
    SeguimientosClinicos,
    DiagnosticoPatologia,
    SeguimientoHospitalario,
)
from .serializers import (
    HistoriaClinicaSerializer,
    ConsultaSerializer,
    HospitalizacionSeresSintientesSerializer,
    TratamientosSerializer,
    DiagnosticoSerializer,
    SeguimientosClinicosSerializer,
    DiagnosticoPatologiaSerializer,
    SeguimientoHospitalarioSerializer,
)


class HistoriaClinicaViewSet(viewsets.ModelViewSet):
    queryset = HistoriaClinica.objects.all()
    serializer_class = HistoriaClinicaSerializer
    permission_classes = [IsAuthenticated]


class ConsultaViewSet(viewsets.ModelViewSet):
    queryset = Consulta.objects.all()
    serializer_class = ConsultaSerializer
    permission_classes = [IsAuthenticated]


class HospitalizacionSeresSintientesViewSet(viewsets.ModelViewSet):
    queryset = HospitalizacionSeresSintientes.objects.all()
    serializer_class = HospitalizacionSeresSintientesSerializer
    permission_classes = [IsAuthenticated]


class TratamientosViewSet(viewsets.ModelViewSet):
    queryset = Tratamientos.objects.all()
    serializer_class = TratamientosSerializer
    permission_classes = [IsAuthenticated]


class DiagnosticoViewSet(viewsets.ModelViewSet):
    queryset = Diagnostico.objects.all()
    serializer_class = DiagnosticoSerializer
    permission_classes = [IsAuthenticated]


class SeguimientosClinicosViewSet(viewsets.ModelViewSet):
    queryset = SeguimientosClinicos.objects.all()
    serializer_class = SeguimientosClinicosSerializer
    permission_classes = [IsAuthenticated]


class DiagnosticoPatologiaViewSet(viewsets.ModelViewSet):
    queryset = DiagnosticoPatologia.objects.all()
    serializer_class = DiagnosticoPatologiaSerializer
    permission_classes = [IsAuthenticated]


class SeguimientoHospitalarioViewSet(viewsets.ModelViewSet):
    queryset = SeguimientoHospitalario.objects.all()
    serializer_class = SeguimientoHospitalarioSerializer
    permission_classes = [IsAuthenticated]
