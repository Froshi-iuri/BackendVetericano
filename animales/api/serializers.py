from rest_framework import serializers
from animales.models import Responsables, Animal, EstadosAnimal, AnimalResponsable


class ResponsablesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Responsables
        fields = '__all__'


class AnimalSerializer(serializers.ModelSerializer):
    raza_nombre = serializers.CharField(source='id_raza.nombre', read_only=True)

    class Meta:
        model = Animal
        fields = '__all__'


class EstadosAnimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = EstadosAnimal
        fields = '__all__'


class AnimalResponsableSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnimalResponsable
        fields = '__all__'
