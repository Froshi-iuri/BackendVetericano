from rest_framework import viewsets
from django.db import connection
from examenes_clinicos.models import Examen, ProcedimientoRealizado
from .serializers import (
    ExamenSerializer,
    ProcedimientoRealizadoSerializer,
)


class ExamenViewSet(viewsets.ModelViewSet):
    queryset = Examen.objects.all()
    serializer_class = ExamenSerializer

    def perform_create(self, serializer):
        try:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE examen ALTER COLUMN id_consulta DROP NOT NULL;")
        except Exception:
            pass
        serializer.save()


class ProcedimientoRealizadoViewSet(viewsets.ModelViewSet):
    queryset = ProcedimientoRealizado.objects.all()
    serializer_class = ProcedimientoRealizadoSerializer
