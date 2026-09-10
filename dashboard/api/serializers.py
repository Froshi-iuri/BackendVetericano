from rest_framework import serializers
from users.models import Usuarios


class DashboardSerializer(serializers.ModelSerializer):

    nombre_rol = serializers.CharField(
        source='id_rol.nombre_rol',
        read_only=True
    )

    class Meta:
        model = Usuarios
        fields = (
            'id_usuario',
            'nombre',
            'apellido',
            'email',
            'nombre_rol',
        )