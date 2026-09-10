from rest_framework import serializers
from users.models import TiposPeticion, Peticiones

class TiposPeticionSerializre(serlializers.ModelSerializer):
    class Meta:
        model = TiposPeticion
        fields = ['id_tipo', 'nombre', 'descripcion']

class InicarPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Peticiones
        fields = ['id_pericion', 'nombre', 'descripcion']


