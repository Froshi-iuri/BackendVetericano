from rest_framework import viewsets
from especies.models import Especie, Raza
from users.api.permissions import EsAdminOReadOnlyParaStaff
from vetericano.paginacion import PaginacionOpcional
from .serializers import EspecieSerializer, RazaSerializer


class EspecieViewSet(viewsets.ModelViewSet):
    queryset = Especie.objects.all()
    serializer_class = EspecieSerializer
    permission_classes = [EsAdminOReadOnlyParaStaff]
    pagination_class = PaginacionOpcional


class RazaViewSet(viewsets.ModelViewSet):
    queryset = Raza.objects.all()
    serializer_class = RazaSerializer
    permission_classes = [EsAdminOReadOnlyParaStaff]
