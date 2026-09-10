from rest_framework import viewsets
from ..models import AdopcionAnimal
from .serializers import AdopcionAnimalSerializer

class AdopcionAnimalViewSet(viewsets.ModelViewSet):
    queryset = AdopcionAnimal.objects.all()
    serializer_class = AdopcionAnimalSerializer