from rest_framework import viewsets
from users.api.permissions import PermisoAdopciones
from animales.models import Animal
from .serializers import AdopcionAnimalSerializer


class AdopcionAnimalViewSet(viewsets.ModelViewSet):
    queryset = Animal.objects.filter(activo=True)
    serializer_class = AdopcionAnimalSerializer
    permission_classes = [PermisoAdopciones]

    def get_queryset(self):
        # Si es una petición para listar, podemos devolver solo los disponibles, 
        # o todos si el frontend admin quiere verlos todos.
        # Por ahora dejamos activo=True. El frontend parece pedir todos.
        return super().get_queryset()
