from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from users.models import Usuarios


class CustomJWTAuthentication(JWTAuthentication):

    def get_user(self, validated_token):

        try:
            user_id = validated_token['user_id']
        except KeyError:
            raise AuthenticationFailed('El token no contiene el ID del usuario.')

        try:
            user = Usuarios.objects.get(id_usuario=user_id)
        except Usuarios.DoesNotExist:
            raise AuthenticationFailed('Usuario no encontrado.')

        if not user.activo:
            raise AuthenticationFailed('El usuario está desactivado.')

        return user