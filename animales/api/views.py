from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
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
    permission_classes = [IsAuthenticated]


class AnimalViewSet(viewsets.ModelViewSet):
    queryset = Animal.objects.all()
    serializer_class = AnimalSerializer
    permission_classes = [IsAuthenticated]


class EstadosAnimalViewSet(viewsets.ModelViewSet):
    queryset = EstadosAnimal.objects.all()
    serializer_class = EstadosAnimalSerializer
    permission_classes = [IsAuthenticated]


class AnimalResponsableViewSet(viewsets.ModelViewSet):
    queryset = AnimalResponsable.objects.all()
    serializer_class = AnimalResponsableSerializer
    permission_classes = [IsAuthenticated]
