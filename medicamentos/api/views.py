from rest_framework import viewsets, filters
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
    """
    Catálogo de medicamentos (tabla `medicamentos`).

    Filtros opcionales (si no se envían, la respuesta es la misma de siempre):
      - ?search=texto      -> busca en nombre y principio_activo
      - ?activo=true|false -> filtra por estado
      - ?tipo=valor        -> filtra por tipo (sin distinguir mayúsculas)
      - ?ordering=nombre   -> ordena (prefijo '-' para descendente)
    """
    queryset = Medicamentos.objects.all()
    serializer_class = MedicamentosSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre', 'principio_activo']
    ordering_fields = ['id_medicamento', 'nombre', 'tipo', 'activo']

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        activo = params.get('activo')
        if activo is not None:
            valor = activo.strip().lower()
            if valor in ('true', '1'):
                queryset = queryset.filter(activo=True)
            elif valor in ('false', '0'):
                queryset = queryset.filter(activo=False)

        tipo = params.get('tipo')
        if tipo:
            queryset = queryset.filter(tipo__iexact=tipo.strip())

        return queryset


class AdministracionMedicamentoViewSet(viewsets.ModelViewSet):
    queryset = AdministracionMedicamento.objects.all()
    serializer_class = AdministracionMedicamentoSerializer


class TratamientoMedicamentosViewSet(viewsets.ModelViewSet):
    queryset = TratamientoMedicamentos.objects.all()
    serializer_class = TratamientoMedicamentosSerializer
