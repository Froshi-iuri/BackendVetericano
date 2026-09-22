from rest_framework import serializers
from voluntariado.models import (
    EventoVoluntariado,
    PostulacionVoluntariado,
    TiposEventos,
    Eventos,
    VoluntarioEvento,
)


class EventoVoluntariadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventoVoluntariado
        fields = '__all__'


class PostulacionVoluntariadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostulacionVoluntariado
        fields = '__all__'


class TiposEventosSerializer(serializers.ModelSerializer):
    class Meta:
        model = TiposEventos
        fields = '__all__'


class EventosSerializer(serializers.ModelSerializer):
    class Meta:
        model = Eventos
        fields = '__all__'


class VoluntarioEventoSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoluntarioEvento
        fields = '__all__'
