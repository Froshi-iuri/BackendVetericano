from rest_framework import viewsets
from ..models import EventoVoluntariado, PostulacionVoluntariado
from .serializers import EventoVoluntariadoSerializer, PostulacionVoluntariadoSerializer

class EventoVoluntariadoViewSet(viewsets.ModelViewSet):
    queryset = EventoVoluntariado.objects.all()
    serializer_class = EventoVoluntariadoSerializer

class PostulacionVoluntariadoViewSet(viewsets.ModelViewSet):
    queryset = PostulacionVoluntariado.objects.all()
    serializer_class = PostulacionVoluntariadoSerializer