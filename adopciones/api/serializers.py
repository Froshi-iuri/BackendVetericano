from rest_framework import serializers
from animales.models import Animal
from especies.models import Raza
from datetime import date

class AdopcionAnimalSerializer(serializers.ModelSerializer):
    # Definimos los campos exactamente como los envía y espera el frontend
    id = serializers.IntegerField(source='id_animal', read_only=True)
    raza = serializers.CharField(required=False, allow_blank=True)
    descripcion = serializers.CharField(required=False, allow_blank=True)
    imagen = serializers.CharField(source='foto_url', required=False, allow_blank=True)
    estado = serializers.CharField(required=False, allow_blank=True)
    fecha = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Animal
        fields = ['id', 'nombre', 'raza', 'descripcion', 'imagen', 'estado', 'fecha']

    def to_representation(self, instance):
        """Al enviar al frontend, formateamos exactamente al formato que esperan"""
        fecha_str = ""
        if instance.fecha_ingreso:
            # Formato similar a "Oct 6, 2026" o YYYY-MM-DD
            fecha_str = instance.fecha_ingreso.strftime("%Y-%m-%d")

        return {
            'id': instance.id_animal,
            'nombre': instance.nombre,
            'raza': instance.id_raza.nombre if instance.id_raza else "Desconocida",
            'descripcion': instance.observaciones or instance.caracteristicas or "",
            'imagen': instance.foto_url or "",
            'estado': "Disponible" if instance.disponible_adopcion else "No Disponible",
            'fecha': fecha_str
        }

    def create(self, validated_data):
        raza_nombre = validated_data.pop('raza', 'Mestizo')
        descripcion = validated_data.pop('descripcion', '')
        estado = validated_data.pop('estado', 'Disponible')
        # Ignoramos la fecha del frontend por ser texto localizado complejo y ponemos la actual
        validated_data.pop('fecha', None)

        # Buscar raza o usar la primera que exista como fallback (ya que id_raza no es null)
        raza_obj = Raza.objects.filter(nombre__icontains=raza_nombre).first()
        if not raza_obj:
            raza_obj = Raza.objects.first()

        disponible = (estado.lower() == 'disponible')

        animal = Animal.objects.create(
            id_raza=raza_obj,
            caracteristicas=descripcion,
            disponible_adopcion=disponible,
            fecha_ingreso=date.today(),
            **validated_data
        )
        return animal

    def update(self, instance, validated_data):
        raza_nombre = validated_data.pop('raza', None)
        if raza_nombre:
            raza_obj = Raza.objects.filter(nombre__icontains=raza_nombre).first()
            if raza_obj:
                instance.id_raza = raza_obj

        if 'descripcion' in validated_data:
            instance.caracteristicas = validated_data.pop('descripcion')

        if 'estado' in validated_data:
            estado = validated_data.pop('estado')
            instance.disponible_adopcion = (estado.lower() == 'disponible')

        validated_data.pop('fecha', None) # Ignoramos fecha

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance
