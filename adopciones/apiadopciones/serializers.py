from rest_framework import serializers
from ..models import AdopcionAnimal, SolicitudAdopcion

class AdopcionAnimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdopcionAnimal
        fields = '__all__'

class SolicitudAdopcionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SolicitudAdopcion
        fields = '__all__'
        read_only_fields = ['fecha_solicitud']