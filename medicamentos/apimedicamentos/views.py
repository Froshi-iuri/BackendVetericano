from django.shortcuts import render

# AnaC

from rest_framework import viewsets
from ..models import Medicamento
from .serializers import MedicamentoSerializer

class MedicamentoViewSet(viewsets.ModelViewSet):
    queryset = Medicamento.objects.all()
    serializer_class = MedicamentoSerializer


