# serializers.py
from rest_framework import serializers
# Importamos el hash de contraseñas de Django. 
# Esto es CRÍTICO: nunca guardes contraseñas en texto plano.
from django.contrib.auth.hashers import make_password, check_password
from users.models import Usuarios, Rol

class RegisterCustomSerializer(serializers.ModelSerializer):
    """
    Serializador encargado de tomar los datos de Swagger, validarlos
    y crear un nuevo registro en la tabla 'usuarios'.
    """
    # PrimaryKeyRelatedField le dice a Swagger que id_rol es un número que debe apuntar a un Rol existente.
    # queryset=Rol.objects.all() asegura que si el usuario envía un id_rol=99 (y no existe), devuelva error antes de tocar la BD.
    id_rol = serializers.PrimaryKeyRelatedField(queryset=Rol.objects.all())

    class Meta:
        model = Usuarios
        # Declaramos TODOS los campos que tu script SQL exige que no sean nulos.
        fields = ('email', 'password', 'nombre', 'apellido', 'id_rol')
        # write_only impide que la contraseña vuelva a Swagger en la respuesta de éxito.
        extra_kwargs = {'password': {'write_only': True}}

    def create(self, validated_data):
        # make_password encripta la contraseña usando el algoritmo PBKDF2 de Django.
        # Si omites esto, tu sistema quedará vulnerable y el inicio de sesión no funcionará después.
        validated_data['password'] = make_password(validated_data['password'])
        
        # Insertamos el nuevo registro directamente en la tabla Usuarios de PostgreSQL.
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

class UsuariosSerializer(serializers.ModelSerializer):

    nombre_rol = serializers.CharField(
        source='id_rol.nombre_rol',
        read_only=True
    )

    class Meta:
        model = Usuarios
        fields = (
            'id_usuario',
            'email',
            'nombre',
            'apellido',
            'id_rol',
            'nombre_rol',
            'activo',
        )


class RolSerializer(serializers.ModelSerializer):

    class Meta:
        model = Rol
        fields = (
            'id_rol',
            'nombre_rol',
        )