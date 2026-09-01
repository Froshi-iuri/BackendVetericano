from rest_framework import serializers


class RegistroSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    nombre = serializers.CharField(max_length=150, required=False)
    apellido = serializers.CharField(max_length=150, required=False)
    telefono = serializers.CharField(max_length=20, required=False)  # <-- Campo nuevo
    rol_id = serializers.IntegerField(required=False, help_text="ID del rol (Ej: 1=Admin, 2=Cliente)")


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)