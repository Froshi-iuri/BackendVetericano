from django.db.models import F
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
    queryset = Responsables.objects.all().order_by('nombre', 'id_responsable')
    serializer_class = ResponsablesSerializer
    permission_classes = [PermisoAnimales]


class AnimalViewSet(viewsets.ModelViewSet):
    queryset = Animal.objects.all().order_by('nombre', 'id_animal')
    serializer_class = AnimalSerializer
    permission_classes = [PermisoAnimales]


class EstadosAnimalViewSet(viewsets.ModelViewSet):
    queryset = EstadosAnimal.objects.all().order_by('-fecha_registro', '-id_estado_animal')
    serializer_class = EstadosAnimalSerializer
    permission_classes = [PermisoAnimales]


class AnimalResponsableViewSet(viewsets.ModelViewSet):
    queryset = AnimalResponsable.objects.all().order_by(
        F('fecha_inicio').desc(nulls_last=True), '-id_animal_responsable',
    )
    serializer_class = AnimalResponsableSerializer
    permission_classes = [PermisoAnimales]
