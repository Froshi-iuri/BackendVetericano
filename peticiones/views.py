from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from users.models import TiposPeticion, Peticiones, EstadosPeticion
from users.api.serializers import UsuariosSerializer
from .serializers import TiposPeticionSerializer, IniciarPeticionSerializer

class ListarTiposPeticionView(generics.ListAPIView):
    queryset = TiposPeticion.objects.filter(activo=True)
    serializer_class = TiposPeticionSerializer
    permission_classes = [IsAuthenticated]


class IniciarPeticionView(generics.CreateAPIView):
    serializer_class = IniciarPeticionSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        try:
            estado_inicial = EstadosPeticion.objects.get(nombre__iexact='Borrador')
        except EstadosPeticion.DoesNotExist:
            return Response(
                {"error": "El estado 'Borrador' no existe en la base de datos."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        peticion = serializer.save(
            ciudadano_id=request.user.id_usuario,
            id_estado=estado_inicial
        )

        return Response({
            "mensaje": "Petición iniciada con éxito",
            "id_peticion": peticion.id_peticion,
            "id_tipo": peticion.id_tipo.id_tipo
        }, status=status.HTTP_201_CREATED)


class PerfilUsuarioView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UsuariosSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request):
        serializer = UsuariosSerializer(
            request.user, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)