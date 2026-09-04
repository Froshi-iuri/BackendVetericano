# serializers.py
from rest_framework import serializers
# Importamos el hash de contraseñas de Django. 
# Esto es CRÍTICO: nunca guardes contraseñas en texto plano.
from django.contrib.auth.hashers import make_password, check_password
from users.models import Usuarios, Rol

class RegisterCustomSerializer(serializers.ModelSerializer):
    # Ya no declaramos id_rol aquí arriba. 
    # Dejamos que DRF use exclusivamente lo definido en Meta.fields.

    class Meta:
        model = Usuarios
        fields = ('email', 'password', 'nombre', 'apellido')
        extra_kwargs = {'password': {'write_only': True}}

    def create(self, validated_data):
        validated_data['password'] = make_password(validated_data['password'])
        
        # Obtenemos o creamos el rol por defecto
        rol_peticionario, _ = Rol.objects.get_or_create(nombre_rol='Peticionario')
        
        # Inyectamos el rol directamente en los datos validados antes de guardar
        validated_data['id_rol'] = rol_peticionario
        
        return super().create(validated_data)
class LoginCustomSerializer(serializers.Serializer):
    """
    Serializador que genera el formulario de Login en Swagger y
    verifica que el correo y la contraseña coincidan en PostgreSQL.
    """
    # Exigimos email y contraseña. Swagger usará estos campos para dibujar el formulario.
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        # 1. Buscamos al usuario por correo en nuestra tabla personalizada.
        # Si usas User.objects.get(), fallará si el correo no existe, por eso usamos filter y .first().
        user = Usuarios.objects.filter(email=data['email']).first()

        # 2. Validaciones encadenadas:
        # - ¿El usuario existe?
        # - check_password compara la contraseña plana de Swagger con el hash guardado en PostgreSQL.
        # - ¿El usuario tiene activo=True?
        if user and check_password(data['password'], user.password):
            if not user.activo:
                 raise serializers.ValidationError("Esta cuenta ha sido desactivada.")
            return user
        
        # Si algo de arriba falla, rechazamos el login.
        raise serializers.ValidationError("Credenciales incorrectas.")