from rest_framework import serializers
from django.db import transaction
from peticiones.models import TipoPeticion, Peticiones, Ubicaciones


class TiposPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoPeticion
        fields = ['id_tipo', 'nombre', 'descripcion']


class IniciarPeticionSerializer(serializers.ModelSerializer):
    # Campos de ubicación que vienen en el mismo POST
    direccion = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    latitud = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    longitud = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    foto = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Peticiones
        fields = [
            'id_peticion',
            'id_tipo',
            'descripcion',
            'prioridad',
            'foto',
            'direccion',
            'latitud',
            'longitud',
        ]
        read_only_fields = ['id_peticion']

    @transaction.atomic
    def create(self, validated_data):
        direccion = validated_data.pop('direccion', None)
        latitud = validated_data.pop('latitud', None)
        longitud = validated_data.pop('longitud', None)

        # 1. Si viene algún dato de mapa/dirección, creamos el registro en Ubicaciones
        ubicacion = None
        if direccion or latitud is not None or longitud is not None:
            ubicacion = Ubicaciones.objects.create(
                direccion=direccion,
                latitud=latitud,
                longitud=longitud
            )

        # 2. Creamos la petición enlazando la ubicación creada
        peticion = Peticiones.objects.create(
            id_ubicacion=ubicacion,
            **validated_data
        )
        return peticion
