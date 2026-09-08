from rest_framework import serializers
from examenes_clinicos.models import ProcedimientosCatalogo, ProcedimientosRealizados


class ProcedimientosCatalogoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcedimientosCatalogo
        fields = (
            'id_procedimiento_catalogo',
            'nombre_tipo',
            'tipo',
            'descripcion',
            'observaciones', 
            'estado')
        read_only_fields = ('id_procedimiento_catalogo',)


class ProcedimientosRealizadosSerializer(serializers.ModelSerializer):
    nombre_procedimiento = serializers.CharField(
        source='id_procedimiento_catalogo.nombre_tipo',
        read_only=True
    )

    class Meta:
        model = ProcedimientosRealizados
        fields = (
            'id_procedimiento_realizado',
            'id_consulta',
            'id_procedimiento_catalogo',
            'nombre_procedimiento',
            'resultado_anexo_url',
        )
        read_only_fields = ('id_procedimiento_realizado',)
