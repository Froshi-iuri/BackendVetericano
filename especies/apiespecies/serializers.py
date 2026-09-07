from rest_framework import serializers
from especies.models import Especie, Raza


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
