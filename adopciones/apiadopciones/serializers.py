from rest_framework import serializers
from ..models import SolicitudAdopcion

class SolicitudAdopcionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SolicitudAdopcion
        fields = '__all__'
        read_only_fields = ['fecha_solicitud']