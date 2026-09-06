from rest_framework import viewsets
from especies.models import Especie
from especies.serializers import EspecieSerializer


class EspecieViewSet(viewsets.ModelViewSet):
    queryset = Especie.objects.all()
    serializer_class = EspecieSerializer