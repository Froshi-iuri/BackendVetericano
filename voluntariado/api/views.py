from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from voluntariado.models import (
    EventoVoluntariado,
    PostulacionVoluntariado,
    TiposEventos,
    Eventos,
    VoluntarioEvento,
)
from .serializers import (
    EventoVoluntariadoSerializer,
    PostulacionVoluntariadoSerializer,
    PostuladoDetalleSerializer,
    TiposEventosSerializer,
    EventosSerializer,
    VoluntarioEventoSerializer,
)


class EventoVoluntariadoViewSet(viewsets.ModelViewSet):
    queryset = EventoVoluntariado.objects.all().order_by('-fecha')
    serializer_class = EventoVoluntariadoSerializer
    permission_classes = [AllowAny]

    @action(detail=True, methods=['get'], url_path='postulados', permission_classes=[AllowAny])
    def postulados(self, request, pk=None):
        """
        Retorna la lista de voluntarios postulados a esta jornada específica.
        Formato optimizado para el modal de Angular admin.
        """
        evento = self.get_object()
        postulaciones = evento.postulaciones.select_related('usuario').all().order_by('-fecha_postulacion')
        serializer = PostuladoDetalleSerializer(postulaciones, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='postularse', permission_classes=[permissions.IsAuthenticated])
    def postularse(self, request, pk=None):
        """
        Permite a un usuario autenticado desde la app móvil postularse con 1 solo clic.
        Toma automáticamente los datos de su cuenta registrada.
        """
        evento = self.get_object()
        usuario = request.user

        # Verificar si ya está postulado por usuario o por identificación
        ya_postulado = PostulacionVoluntariado.objects.filter(evento=evento, usuario=usuario).exists()
        if not ya_postulado and usuario.identificacion:
            ya_postulado = PostulacionVoluntariado.objects.filter(evento=evento, identificacion=usuario.identificacion).exists()

        if ya_postulado:
            return Response(
                {"mensaje": "Ya te encuentras postulado a esta jornada."},
                status=status.HTTP_400_BAD_REQUEST
            )

        telefono = request.data.get('telefono') or getattr(usuario, 'telefono', '') or ''
        nombre_completo = f"{usuario.nombre} {usuario.apellido}".strip()

        postulacion = PostulacionVoluntariado.objects.create(
            evento=evento,
            usuario=usuario,
            nombre_completo=nombre_completo,
            identificacion=usuario.identificacion or "",
            correo=usuario.email,
            telefono=telefono,
            edad=0,
            estado='PENDIENTE'
        )

        return Response(
            {
                "mensaje": "¡Te has postulado exitosamente a la jornada!",
                "id_postulacion": postulacion.id,
                "evento": evento.id,
                "estado": postulacion.estado
            },
            status=status.HTTP_201_CREATED
        )


class PostulacionVoluntariadoViewSet(viewsets.ModelViewSet):
    queryset = PostulacionVoluntariado.objects.all().order_by('-fecha_postulacion')
    serializer_class = PostulacionVoluntariadoSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = super().get_queryset()
        evento_id = self.request.query_params.get('evento')
        if evento_id:
            queryset = queryset.filter(evento_id=evento_id)
        return queryset


class TiposEventosViewSet(viewsets.ModelViewSet):
    queryset = TiposEventos.objects.all()
    serializer_class = TiposEventosSerializer


class EventosViewSet(viewsets.ModelViewSet):
    queryset = Eventos.objects.all()
    serializer_class = EventosSerializer


class VoluntarioEventoViewSet(viewsets.ModelViewSet):
    queryset = VoluntarioEvento.objects.all()
    serializer_class = VoluntarioEventoSerializer
