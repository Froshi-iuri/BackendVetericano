from rest_framework import viewsets
from voluntariado.models import (
    EventoVoluntariado,
    PostulacionVoluntariado,
    TiposEventos,
    Eventos,
    VoluntarioEvento,
)
from .serializers import (
    EventoVoluntariadoSerializer,
    PostulacionVoluntariadoSerializer,
    TiposEventosSerializer,
    EventosSerializer,
    VoluntarioEventoSerializer,
)


class EventoVoluntariadoViewSet(viewsets.ModelViewSet):
    queryset = EventoVoluntariado.objects.all()
    serializer_class = EventoVoluntariadoSerializer


class PostulacionVoluntariadoViewSet(viewsets.ModelViewSet):
    queryset = PostulacionVoluntariado.objects.all()
    serializer_class = PostulacionVoluntariadoSerializer


class TiposEventosViewSet(viewsets.ModelViewSet):
    queryset = TiposEventos.objects.all()
    serializer_class = TiposEventosSerializer


class EventosViewSet(viewsets.ModelViewSet):
    queryset = Eventos.objects.all()
    serializer_class = EventosSerializer


class VoluntarioEventoViewSet(viewsets.ModelViewSet):
    queryset = VoluntarioEvento.objects.all()
    serializer_class = VoluntarioEventoSerializer
