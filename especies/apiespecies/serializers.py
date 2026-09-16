from rest_framework import serializers
from especies.models import Especie, Raza, Animal


class EspecieSerializer(serializers.ModelSerializer):
    class Meta:
        model = Especie
        fields = '__all__'
        read_only_fields = ('id_especie',)


class RazaSerializer(serializers.ModelSerializer):
    nombre_especie = serializers.CharField(
        source='id_especie.nombre',
        read_only=True
    )

    class Meta:
        model = Raza
        fields = (
            'id_raza',
            'id_especie',
            'nombre_especie',
            'nombre',
            'descripcion',
            'activo',
        )
        read_only_fields = ('id_raza',)

class AnimalSerializer(serializers.ModelSerializer):
    # Accede al nombre de la especie viajando a través de la raza
    nombre_especie = serializers.CharField(
        source='id_raza.id_especie.nombre', 
        read_only=True
    )
    # Opcional: Si también quieres mostrar el nombre de la raza en el JSON
    nombre_raza = serializers.CharField(
        source='id_raza.nombre', 
        read_only=True
    )

    class Meta:
        model = Animal
        fields = (
            'id_animal',
            'id_raza',
            'nombre_raza',     # Incluido por si quieres ver la raza textualmente
            'nombre_especie',  # El campo que necesitas
            'nombre',
            'sexo',
            'fecha_ingreso',
            'caracteristicas',
            'observaciones',
            'foto_url',
            'activo',
            'color',
            'fecha_nacimiento',
            'peso',
            'esterilizado',
        )
        read_only_fields = ('id_animal',)
