from rest_framework import viewsets
from examenes_clinicos.models import ProcedimientosCatalogo, ProcedimientosRealizados
from .serializers import (
    ProcedimientosCatalogoSerializer,
    ProcedimientosRealizadosSerializer,
)


class ProcedimientosCatalogoViewSet(viewsets.ModelViewSet):
    queryset = ProcedimientosCatalogo.objects.all()
    serializer_class = ProcedimientosCatalogoSerializer


class ProcedimientosRealizadosViewSet(viewsets.ModelViewSet):
    queryset = ProcedimientosRealizados.objects.all()
    serializer_class = ProcedimientosRealizadosSerializer
