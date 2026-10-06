from rest_framework import viewsets
from animales.models import Animal
from .serializers import AdopcionAnimalSerializer


class AdopcionAnimalViewSet(viewsets.ModelViewSet):
    # Ya no limitamos a disponible_adopcion=True en el queryset principal,
    # porque si el frontend cambia a estado "No Disponible" y actualiza,
    # el backend podría dar 404 al intentar obtenerlo de nuevo si lo filtramos aquí.
    # Así que listamos todos los activos, o filtramos solo para la vista de lista.
    queryset = Animal.objects.filter(activo=True)
    serializer_class = AdopcionAnimalSerializer

    def get_queryset(self):
        # Si es una petición para listar, podemos devolver solo los disponibles, 
        # o todos si el frontend admin quiere verlos todos.
        # Por ahora dejamos activo=True. El frontend parece pedir todos.
        return super().get_queryset()
