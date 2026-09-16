from rest_framework import viewsets
from especies.models import Especie, Raza, Animal
from .serializers import EspecieSerializer, RazaSerializer, AnimalSerializer


class EspecieViewSet(viewsets.ModelViewSet):
    queryset = Especie.objects.all()
    serializer_class = EspecieSerializer


class RazaViewSet(viewsets.ModelViewSet):
    queryset = Raza.objects.all()
    serializer_class = RazaSerializer

class AnimalViewSet(viewsets.ModelViewSet):
    queryset = Animal.objects.all()
    serializer_class = AnimalSerializer