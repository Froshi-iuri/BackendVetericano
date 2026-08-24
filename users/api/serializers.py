from rest_framework import serializers
from django.contrib.auth.models import User

class RegisterSerializer(serializers.ModelSerializer):
    # EL PORQUÉ: Ocultamos la contraseña en las respuestas HTTP de salida para no exponer datos sensibles.
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'username', 'first_name', 'password']

    # EL PORQUÉ: Verificamos en la base de datos si el correo ya existe antes de intentar guardar para evitar cuentas duplicadas.
    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Este correo electrónico ya está registrado.")
        return value

    # EL PORQUÉ: Usamos 'create_user' en lugar del 'create' por defecto para que Django aplique el algoritmo de hashing seguro a la clave.
    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            password=validated_data['password']
        )
        return user