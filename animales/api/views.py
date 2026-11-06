from rest_framework import viewsets
from users.api.permissions import PermisoAnimales
from animales.models import Responsables, Animal, EstadosAnimal, AnimalResponsable
from .serializers import (
    ResponsablesSerializer,
    AnimalSerializer,
    EstadosAnimalSerializer,
    AnimalResponsableSerializer,
)


class ResponsablesViewSet(viewsets.ModelViewSet):
    queryset = Responsables.objects.all()
    serializer_class = ResponsablesSerializer
    permission_classes = [PermisoAnimales]


class AnimalViewSet(viewsets.ModelViewSet):
    queryset = Animal.objects.all()
    serializer_class = AnimalSerializer
    permission_classes = [PermisoAnimales]


class EstadosAnimalViewSet(viewsets.ModelViewSet):
    queryset = EstadosAnimal.objects.all()
    serializer_class = EstadosAnimalSerializer
    permission_classes = [PermisoAnimales]


class AnimalResponsableViewSet(viewsets.ModelViewSet):
    queryset = AnimalResponsable.objects.all()
    serializer_class = AnimalResponsableSerializer
    permission_classes = [PermisoAnimales]
