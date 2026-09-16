from rest_framework import serializers
from examenes_clinicos.models import Examen, ProcedimientoRealizado
from users.models import Consulta


class ExamenSerializer(serializers.ModelSerializer):
    id_consulta = serializers.PrimaryKeyRelatedField(
        queryset=Consulta.objects.all(),
        required=False,
        allow_null=True
    )
    nombre_tipo = serializers.CharField(source='nombre_tipo_examen', required=False, allow_blank=True, allow_null=True)
    descripcion = serializers.CharField(source='descripcion_hallazgos', required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Examen
        fields = (
            'id_examen',
            'id_consulta',
            'nombre_tipo_examen',
            'nombre_tipo',
            'solicitado',
            'descripcion_hallazgos',
            'descripcion',
            'resultado',
            'ruta_archivo_resultado',
            'fecha_realizacion',
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
