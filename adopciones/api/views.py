from rest_framework import viewsets
from animales.models import Animal
from .serializers import AdopcionAnimalSerializer


class AdopcionAnimalViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Animal.objects.filter(disponible_adopcion=True, activo=True)
    serializer_class = AdopcionAnimalSerializer
