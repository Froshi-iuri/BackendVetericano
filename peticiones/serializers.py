from rest_framework import serializers
from users.models import TiposPeticion, Peticiones

class TiposPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TiposPeticion
        fields = ['id_tipo', 'nombre', 'descripcion']

class IniciarPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Peticiones
        fields = ['id_peticion', 'id_tipo']
        read_only_fields = ['id_peticion']
        