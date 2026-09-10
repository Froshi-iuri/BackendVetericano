from rest_framework import serializers
from ..models import EventoVoluntariado, PostulacionVoluntariado

class EventoVoluntariadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventoVoluntariado
        fields = '__all__'

class PostulacionVoluntariadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostulacionVoluntariado
        fields = '__all__'