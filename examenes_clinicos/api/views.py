from rest_framework import viewsets
from examenes_clinicos.models import Examen, ProcedimientoRealizado
from .serializers import ExamenSerializer, ProcedimientoRealizadoSerializer

class ExamenViewSet(viewsets.ModelViewSet):
    queryset = Examen.objects.all().order_by('-id_examen')
    serializer_class = ExamenSerializer

    def perform_create(self, serializer):
        print("--- CREANDO EXAMEN ---")
        print("Datos validados recibidos:", serializer.validated_data)
        instance = serializer.save()
        print("Estado guardado en BD:", instance.estado)

    def perform_update(self, serializer):
        print("--- ACTUALIZANDO EXAMEN ---")
        print("Datos validados a actualizar:", serializer.validated_data)
        instance = serializer.save()
        print("Estado actualizado en BD:", instance.estado)


class ProcedimientoRealizadoViewSet(viewsets.ModelViewSet):
    queryset = ProcedimientoRealizado.objects.all()
    serializer_class = ProcedimientoRealizadoSerializer