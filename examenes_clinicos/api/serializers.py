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
    estado = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='activo')

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
            'estado',
        )
        read_only_fields = ('id_examen',)

    @staticmethod
    def _normalizar_estado(valor):
        if valor is None:
            return 'activo'
        texto = str(valor).strip().lower()
        if not texto or texto in ('true', '1', 'si', 'sí', 'activo'):
            return 'activo'
        if texto in ('false', '0', 'no', 'inactivo'):
            return 'inactivo'
        return texto

    def validate_estado(self, value):
        return self._normalizar_estado(value)

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        estado_valor = getattr(instance, 'estado', None)

        if not estado_valor:
            try:
                from django.db import connection
                with connection.cursor() as cursor:
                    cursor.execute("SELECT estado FROM examen WHERE id_examen = %s", [instance.id_examen])
                    row = cursor.fetchone()
                    if row and row[0]:
                        estado_valor = row[0]
            except Exception:
                pass

        representation['estado'] = str(estado_valor).lower().strip() if estado_valor else 'activo'
        return representation

    def update(self, instance, validated_data):
        if 'estado' in validated_data:
            validated_data['estado'] = self._normalizar_estado(validated_data['estado'])
        return super().update(instance, validated_data)

    def create(self, validated_data):
        validated_data['estado'] = self._normalizar_estado(validated_data.get('estado'))
        return super().create(validated_data)


class ProcedimientoRealizadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcedimientoRealizado
        fields = (
            'id_procedimiento',
            'id_hospitalizacion',
            'descripcion',
        )
        read_only_fields = ('id_procedimiento',)