from typing import Any
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from users.models import Usuarios


class UsuariosTokenRefreshSerializer(TokenRefreshSerializer):
    """
    Serializer de refresco de token adaptado al modelo Usuarios (managed=False).
    En lugar de usar get_user_model() (que apunta al auth.User estándar de Django
    y falla al resolver 'id_usuario'), valida la existencia y estado activo
    directamente sobre el modelo Usuarios, siguiendo el mismo criterio que
    CustomJWTAuthentication.
    """

    def validate(self, attrs: dict[str, Any]) -> dict[str, str]:
        refresh = self.token_class(attrs["refresh"])

        user_id = refresh.payload.get(api_settings.USER_ID_CLAIM, None)
        if not user_id:
            raise AuthenticationFailed("El token no contiene el ID del usuario.")

        try:
            user = Usuarios.objects.get(id_usuario=user_id)
        except Usuarios.DoesNotExist:
            raise AuthenticationFailed("Usuario no encontrado.")

        if not user.activo:
            raise AuthenticationFailed("El usuario está desactivado.")

        # Asegurar claims de rol actualizados si el usuario los tiene
        if hasattr(user, "id_rol") and user.id_rol:
            refresh["id_rol"] = user.id_rol.id_rol
            refresh["nombre_rol"] = getattr(user.id_rol, "nombre_rol", "")

        data = {"access": str(refresh.access_token)}

        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass

            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            refresh.outstand()

            data["refresh"] = str(refresh)

        return data


class UsuariosTokenRefreshView(TokenRefreshView):
    """
    Vista de refresco de tokens JWT compatible con el modelo Usuarios.
    """
    serializer_class = UsuariosTokenRefreshSerializer
