from rest_framework import serializers
from ..models import Patologia 
from patologias.models import Patologia 

class PatologiaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patologia
        fields = '__all__'
        read_only_fields = ('id_patologia',)