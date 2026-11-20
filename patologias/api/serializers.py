from rest_framework import serializers
from patologias.models import Patologia


class PatologiaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patologia
        fields = ('id_patologia', 'nombre', 'descripcion', 'tipo_de_enfermedad', 'activo')
        read_only_fields = ('id_patologia',)
