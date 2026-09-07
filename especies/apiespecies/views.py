from rest_framework import viewsets
from especies.models import Especie, Raza
from .serializers import EspecieSerializer, RazaSerializer


class EspecieViewSet(viewsets.ModelViewSet):
    queryset = Especie.objects.all()
    serializer_class = EspecieSerializer


class RazaViewSet(viewsets.ModelViewSet):
    queryset = Raza.objects.all()
    serializer_class = RazaSerializer

