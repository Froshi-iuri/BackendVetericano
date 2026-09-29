from django.db.models import Q
from rest_framework import generics, status, viewsets
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from .serializers import RegisterCustomSerializer, LoginCustomSerializer, UsuariosSerializer, RolSerializer
from .permissions import EsAdministradorOReadOnly
from users.models import Usuarios, Rol



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
            "id_usuario": user.id_usuario,
            "nombre": user.nombre,
            "apellido": user.apellido,
            "id_rol": user.id_rol.id_rol,
            "tokens": {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            }
        }, status=status.HTTP_200_OK)


class UsuariosViewSet(viewsets.ModelViewSet):
    queryset = Usuarios.objects.all()
    serializer_class = UsuariosSerializer
    permission_classes = [EsAdministradorOReadOnly]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        # 1. Anti Auto-Eliminación
        if request.user and request.user.id_usuario == instance.id_usuario:
            return Response(
                {"detail": "Por seguridad del sistema, no puedes eliminar tu propia cuenta de Administrador."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Quórum Mínimo: No eliminar al último administrador activo
        rol = getattr(instance, 'id_rol', None)
        nombre_rol = getattr(rol, 'nombre_rol', '').strip().lower() if rol else ''
        id_rol = getattr(rol, 'id_rol', None) if rol else None
        es_admin = (nombre_rol in ['administrador', 'admin'] or id_rol == 1)

        if es_admin and instance.activo:
            otros_admins = Usuarios.objects.filter(
                activo=True
            ).filter(
                Q(id_rol__nombre_rol__iexact='administrador') |
                Q(id_rol__nombre_rol__iexact='admin') |
                Q(id_rol=1)
            ).exclude(id_usuario=instance.id_usuario).count()

            if otros_admins == 0:
                return Response(
                    {"detail": "Operación denegada: Este usuario es el único Administrador activo del sistema. No es posible eliminarlo sin antes designar a otro Administrador."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        return super().destroy(request, *args, **kwargs)



class RolViewSet(viewsets.ModelViewSet):
    queryset = Rol.objects.all()
    serializer_class = RolSerializer
    permission_classes = [EsAdministradorOReadOnly]
