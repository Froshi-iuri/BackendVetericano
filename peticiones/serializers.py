from rest_framework import serializers
from users.models import TiposPeticion, Peticiones

class TiposPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TiposPeticion
        fields = ['id_tipo', 'nombre', 'descripcion']

# Se corrigió "Inicar" por "Iniciar"
class IniciarPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Peticiones
        # Nota adicional: verifica si 'id_pericion' no tiene también un typo por 'id_peticion'
        fields = ['id_pericion', 'nombre', 'descripcion']