from rest_framework import viewsets
from examenes_clinicos.models import Examen, ProcedimientoRealizado
from .serializers import (
    ExamenSerializer,
    ProcedimientoRealizadoSerializer,
)


class ExamenViewSet(viewsets.ModelViewSet):
    queryset = Examen.objects.all()
    serializer_class = ExamenSerializer


class ProcedimientoRealizadoViewSet(viewsets.ModelViewSet):
    queryset = ProcedimientoRealizado.objects.all()
    serializer_class = ProcedimientoRealizadoSerializer
