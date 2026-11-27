from rest_framework import serializers

from triaje_ia.models import TriajePeticion
from triaje_ia.services import normalizar_prioridad_sugerida


class TriajeResultadoInputSerializer(serializers.Serializer):
    clave_idempotencia = serializers.CharField(max_length=120, required=False, allow_blank=True)
    estado_analisis = serializers.ChoiceField(
        choices=TriajePeticion.ESTADOS_ANALISIS_CHOICES,
        required=False,
        default=TriajePeticion.ANALISIS_COMPLETADO,
    )
    prioridad_sugerida = serializers.CharField(
        max_length=30,
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    nivel_confianza = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0.0,
        max_value=1.0,
    )
    justificacion = serializers.CharField(required=False, allow_blank=True, default='')
    resumen_texto = serializers.CharField(required=False, allow_blank=True, default='')
    resumen_vision = serializers.CharField(required=False, allow_blank=True, default='')
    signos_detectados = serializers.ListField(
        child=serializers.CharField(max_length=150),
        required=False,
        default=list,
    )
    posibles_duplicados = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
    )
    veterinario_recomendado_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
    )
    justificacion_recomendacion = serializers.CharField(required=False, allow_blank=True, default='')
    carga_veterinarios_snapshot = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
    )
    modelo_texto_usado = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    modelo_vision_usado = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    n8n_execution_id = serializers.CharField(max_length=120, required=False, allow_blank=True, default='')
    resultado_raw = serializers.DictField(required=False, default=dict)

    def validate_prioridad_sugerida(self, value):
        if value is None or not str(value).strip():
            return None
        normalizada = normalizar_prioridad_sugerida(value)
        if not normalizada:
            raise serializers.ValidationError(
                'Prioridad sugerida inválida. Valores compatibles: URGENTE, ALTA, MEDIA, BAJA.'
            )
        return normalizada


class TriajeDuplicadosInputSerializer(serializers.Serializer):
    usar_dinov2 = serializers.BooleanField(required=False, default=True)
    registrar_alertas = serializers.BooleanField(required=False, default=True)


class TriajePeticionSerializer(serializers.ModelSerializer):
    id_peticion = serializers.IntegerField(source='peticion_id', read_only=True)
    numero_radicado = serializers.CharField(source='peticion.numero_radicado', read_only=True)
    prioridad_actual_peticion = serializers.CharField(source='peticion.prioridad', read_only=True)
    asignado_a_id_peticion = serializers.IntegerField(source='peticion.asignado_a_id', read_only=True)
    veterinario_recomendado_id = serializers.IntegerField(read_only=True, allow_null=True)
    veterinario_recomendado_nombre = serializers.SerializerMethodField()

    class Meta:
        model = TriajePeticion
        fields = [
            'id_triaje',
            'id_peticion',
            'numero_radicado',
            'clave_idempotencia',
            'estado_envio',
            'estado_analisis',
            'intentos_envio',
            'max_intentos',
            'proximo_reintento_en',
            'ultimo_codigo_http',
            'ultimo_error_envio',
            'enviado_en',
            'completado_en',
            'n8n_execution_id',
            'prioridad_original',
            'prioridad_actual_peticion',
            'prioridad_sugerida',
            'nivel_confianza',
            'justificacion',
            'resumen_texto',
            'resumen_vision',
            'signos_detectados',
            'posibles_duplicados',
            'total_duplicados_detectados',
            'asignado_a_id_peticion',
            'veterinario_recomendado_id',
            'veterinario_recomendado_nombre',
            'justificacion_recomendacion',
            'carga_veterinarios_snapshot',
            'modelo_texto_usado',
            'modelo_vision_usado',
            'fecha_creacion',
            'fecha_actualizacion',
        ]
        read_only_fields = fields

    def get_veterinario_recomendado_nombre(self, obj):
        vet = getattr(obj, 'veterinario_recomendado', None)
        if not vet:
            return None
        return f'{vet.nombre or ""} {vet.apellido or ""}'.strip() or vet.email
