from rest_framework import viewsets
from medicamentos.models import (
    Medicamento,
    Medicamentos,
    AdministracionMedicamento,
    TratamientoMedicamentos,
)
from .serializers import (
    MedicamentoSerializer,
    MedicamentosSerializer,
    AdministracionMedicamentoSerializer,
    TratamientoMedicamentosSerializer,
)


class MedicamentoViewSet(viewsets.ModelViewSet):
    queryset = Medicamento.objects.all()
    serializer_class = MedicamentoSerializer


class MedicamentosViewSet(viewsets.ModelViewSet):
    queryset = Medicamentos.objects.all()
    serializer_class = MedicamentosSerializer


class AdministracionMedicamentoViewSet(viewsets.ModelViewSet):
    queryset = AdministracionMedicamento.objects.all()
    serializer_class = AdministracionMedicamentoSerializer


class TratamientoMedicamentosViewSet(viewsets.ModelViewSet):
    queryset = TratamientoMedicamentos.objects.all()
    serializer_class = TratamientoMedicamentosSerializer
