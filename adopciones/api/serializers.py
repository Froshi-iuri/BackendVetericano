from rest_framework import serializers
from animales.models import Animal


class AdopcionAnimalSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source='id_animal', read_only=True)
    raza = serializers.SerializerMethodField()
    descripcion = serializers.SerializerMethodField()
    imagen = serializers.CharField(source='foto_url', read_only=True)
    disponible = serializers.BooleanField(source='disponible_adopcion')
    fecha_creacion = serializers.SerializerMethodField()

    class Meta:
        model = Animal
        fields = ['id', 'nombre', 'raza', 'descripcion', 'imagen', 'disponible', 'fecha_creacion']

    def get_raza(self, obj):
        if obj.id_raza:
            return obj.id_raza.nombre
        return "Mestizo"

    def get_descripcion(self, obj):
        return obj.observaciones or obj.caracteristicas or "Sin descripción"

    def get_fecha_creacion(self, obj):
        return obj.fecha_ingreso.isoformat() if obj.fecha_ingreso else None
