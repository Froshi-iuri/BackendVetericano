from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from users.models import TipoPeticion, Peticiones
from .serializers import (
    PeticionSerializer,
    TipoPeticionSerializer,
    TiposPeticionSerializer,
    IniciarPeticionSerializer,
)


class PeticionListCreateView(generics.ListCreateAPIView):
    """
    GET: Lista las peticiones registradas. Permite filtrar por query params: ?id_tipo= y ?id_estado=
    POST: Crea una nueva petición.
    """
    queryset = Peticiones.objects.all().order_by('-id_peticion')
    serializer_class = PeticionSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = Peticiones.objects.all().order_by('-id_peticion')
        tipo = self.request.query_params.get('id_tipo')
        estado = self.request.query_params.get('id_estado')
        if tipo:
            queryset = queryset.filter(id_tipo=tipo)
        if estado:
            queryset = queryset.filter(id_estado=estado)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        peticion = serializer.save()

        headers = self.get_success_headers(serializer.data)
        return Response(
            {
                "mensaje": "Petición creada con éxito",
                "id_peticion": peticion.id_peticion,
                "id_tipo": peticion.id_tipo.id_tipo if peticion.id_tipo else None,
                "datos": serializer.data
            },
            status=status.HTTP_201_CREATED,
            headers=headers
        )


class PeticionDetailView(generics.RetrieveAPIView):
    """
    GET: Obtiene el detalle de una petición por su identificador (ID).
    """
    queryset = Peticiones.objects.all()
    serializer_class = PeticionSerializer
    permission_classes = [AllowAny]


class ListarTiposPeticionView(generics.ListAPIView):
    """
    GET: Lista los tipos de peticiones disponibles en el sistema.
    """
    queryset = TipoPeticion.objects.filter(activo=True)
    serializer_class = TipoPeticionSerializer
    permission_classes = [AllowAny]


# Alias para retrocompatibilidad
IniciarPeticionView = PeticionListCreateView