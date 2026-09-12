from rest_framework import serializers
from examenes_clinicos.models import Examen, ProcedimientoRealizado


class ExamenSerializer(serializers.ModelSerializer):
    class Meta:
        model = Examen
        fields = (
            'id_examen',
            'id_consulta',
            'resultado',
        )
        read_only_fields = ('id_examen',)


class ProcedimientoRealizadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcedimientoRealizado
        fields = (
            'id_procedimiento',
            'id_hospitalizacion',
            'descripcion',
        )
        read_only_fields = ('id_procedimiento',)
