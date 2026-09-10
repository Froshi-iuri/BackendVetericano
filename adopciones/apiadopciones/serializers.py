from rest_framework import serializers
from ..models import AdopcionAnimal

class AdopcionAnimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdopcionAnimal
        fields = '__all__'