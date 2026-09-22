from rest_framework import serializers
from peticiones.models import TipoPeticion, Peticiones


class TiposPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoPeticion
        fields = ['id_tipo', 'nombre', 'descripcion']


class IniciarPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Peticiones
        fields = ['id_peticion', 'id_tipo']
        read_only_fields = ['id_peticion']
