from django.contrib.auth import authenticate
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authtoken.models import Token
from drf_yasg.utils import swagger_auto_schema

from users.models import Usuario, Rol
from .serializers import RegistroSerializer, LoginSerializer


class RegistroView(APIView):

    @swagger_auto_schema(request_body=RegistroSerializer)
    def post(self, request):
        serializer = RegistroSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        username = serializer.validated_data.get("username")
        email = serializer.validated_data.get("email")
        password = serializer.validated_data.get("password")
        nombre = serializer.validated_data.get("nombre", "")
        apellido = serializer.validated_data.get("apellido", "")
        telefono = serializer.validated_data.get("telefono", None)  # <-- Obtenemos el teléfono
        rol_id = serializer.validated_data.get("rol_id")

        if Usuario.objects.filter(username=username).exists():
            return Response({"mensaje": "El nombre de usuario ya existe"}, status=status.HTTP_400_BAD_REQUEST)

        if Usuario.objects.filter(email=email).exists():
            return Response({"mensaje": "El correo ya está registrado"}, status=status.HTTP_400_BAD_REQUEST)

        rol_obj = None
        if rol_id:
            rol_obj = Rol.objects.filter(id=rol_id).first()
        else:
            rol_obj, _ = Rol.objects.get_or_create(nombre_rol="Cliente")

        # Guardamos el usuario con el teléfono
        usuario = Usuario.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=nombre,
            last_name=apellido,
            telefono=telefono,  # <-- Se asigna aquí
            rol=rol_obj
        )

        token = Token.objects.create(user=usuario)

        return Response({
            "mensaje": "Usuario registrado correctamente",
            "usuario": {
                "id": usuario.id,
                "username": usuario.username,
                "email": usuario.email,
                "telefono": usuario.telefono,  # <-- Se devuelve en el JSON
                "rol": usuario.rol.nombre_rol if usuario.rol else None
            },
            "token": token.key
        }, status=status.HTTP_201_CREATED)


class LoginView(APIView):

    @swagger_auto_schema(request_body=LoginSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        username = serializer.validated_data.get("username")
        password = serializer.validated_data.get("password")

        usuario = authenticate(username=username, password=password)

        if usuario:
            if not usuario.activo:
                return Response({"mensaje": "Este usuario está desactivado"}, status=status.HTTP_403_FORBIDDEN)

            token, _ = Token.objects.get_or_create(user=usuario)
            return Response({
                "mensaje": "Login correcto",
                "usuario": {
                    "id": usuario.id,
                    "username": usuario.username,
                    "email": usuario.email,
                    "telefono": usuario.telefono,  # <-- Se devuelve en el JSON
                    "rol": usuario.rol.nombre_rol if usuario.rol else None
                },
                "token": token.key
            }, status=status.HTTP_200_OK)

        return Response({"mensaje": "Usuario o contraseña incorrectos"}, status=status.HTTP_401_UNAUTHORIZED)