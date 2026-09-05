# views.py

from rest_framework import generics, status, viewsets
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from .serializers import RegisterCustomSerializer, LoginCustomSerializer

class RegisterView(generics.GenericAPIView):

    # Esto es lo que le dice a Swagger:
    # "Usa este serializador para dibujar los campos".
    serializer_class = RegisterCustomSerializer

    def post(self, request, *args, **kwargs):

        serializer = self.get_serializer(data=request.data)

        # Verifica duplicados de email, que los campos obligatorios existan,
        # y que id_rol sea válido.
        serializer.is_valid(raise_exception=True)

        # Guarda en la BD (invocando nuestro método create
        # con la contraseña encriptada).
        serializer.save()

        return Response({
            "mensaje": "Usuario creado exitosamente.",
            "datos": serializer.data
        }, status=status.HTTP_201_CREATED)


class LoginView(generics.GenericAPIView):

    # Configura los campos 'email' y 'password' en Swagger.
    serializer_class = LoginCustomSerializer

    def post(self, request, *args, **kwargs):

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data
        
        # Generamos el par de tokens (acceso y refresco) para este usuario
        refresh = RefreshToken.for_user(user)
        
        # Inyectamos datos extra al token para que el frontend no tenga que hacer peticiones extra
        # Esto guarda el rol directamente dentro del código encriptado del token
        refresh['id_rol'] = user.id_rol.id_rol
        refresh['nombre_rol'] = user.id_rol.nombre_rol

        return Response({
            "mensaje": f"Bienvenido, {user.nombre} {user.apellido}",
            "email": user.email,
            "id_rol": user.id_rol.id_rol,
            "tokens": {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            }
        }, status=status.HTTP_200_OK)


class UsuariosViewSet(viewsets.ModelViewSet):

    queryset = Usuarios.objects.all()
    serializer_class = UsuariosSerializer


class RolViewSet(viewsets.ReadOnlyModelViewSet):

    queryset = Rol.objects.all()
    serializer_class = RolSerializer