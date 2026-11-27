from django.conf import settings
from django.db import connection, connections
from rest_framework import serializers

from peticiones.models import PeticionPosibleDuplicado
from peticiones.services.duplicados_service import extraer_fotografias_peticion
from peticiones.utils import detectar_tipo_archivo
from triaje_ia.models import TriajePeticion
from triaje_ia.services import normalizar_prioridad_sugerida


def tabla_triaje_disponible() -> bool:
    """
    Verifica de forma segura si la tabla `triaje_peticion` existe en la conexión actual
    (p. ej. entornos o plantillas de prueba previos a aplicar migraciones de `triaje_ia`).
    """
    try:
        conn_real = connections[connection.alias]
    except Exception:
        conn_real = connection
    if getattr(conn_real, '_triaje_tabla_disponible', False):
        return True
    try:
        existe = TriajePeticion._meta.db_table in conn_real.introspection.table_names()
    except Exception:
        return False
    if existe:
        setattr(conn_real, '_triaje_tabla_disponible', True)
    return existe


def _nombre_usuario_triaje(user_obj):
    if not user_obj:
        return None
    return f'{user_obj.nombre or ""} {user_obj.apellido or ""}'.strip() or user_obj.email


def calcular_puede_reintentar_triaje(obj) -> bool:
    if obj.estado_analisis in (
        TriajePeticion.ANALISIS_FALLIDO,
        TriajePeticion.ANALISIS_PARCIAL,
        TriajePeticion.ANALISIS_PENDIENTE,
        TriajePeticion.ANALISIS_EN_PROCESO,
    ):
        return True
    return obj.estado_envio in (
        TriajePeticion.ENVIO_FALLO_TEMPORAL,
        TriajePeticion.ENVIO_FALLO_DEFINITIVO,
        TriajePeticion.ENVIO_OMITIDO,
    )


def _buscar_carga_en_snapshot_triaje(obj):
    vet_id = getattr(obj, 'veterinario_recomendado_id', None)
    if not vet_id:
        return None
    snap = getattr(obj, 'carga_veterinarios_snapshot', None) or []
    if not isinstance(snap, list):
        return None
    for item in snap:
        if isinstance(item, dict) and item.get('id_veterinario') == vet_id:
            return item
    return None


def extraer_veterinario_recomendado_sede(obj):
    vet = getattr(obj, 'veterinario_recomendado', None)
    if vet and getattr(vet, 'id_sede', None):
        return getattr(vet.id_sede, 'nombre', None)
    snap = _buscar_carga_en_snapshot_triaje(obj)
    return snap.get('sede_nombre') if snap else None


def extraer_veterinario_recomendado_carga(obj):
    snap = _buscar_carga_en_snapshot_triaje(obj)
    if not snap:
        return None
    return {
        'id_veterinario': snap.get('id_veterinario'),
        'id_sede': snap.get('id_sede'),
        'sede_nombre': snap.get('sede_nombre'),
        'total_asignados': snap.get('total_asignados', 0),
        'pendientes': snap.get('pendientes', 0),
        'en_proceso': snap.get('en_proceso', 0),
        'finalizadas': snap.get('finalizadas', 0),
        'carga_activa': snap.get('carga_activa', 0),
        'urgentes_activos': snap.get('urgentes_activos', 0),
        'saturado': bool(snap.get('saturado', False)),
        'porcentaje_completado': snap.get('porcentaje_completado', 0),
    }


def construir_resumen_triaje_tarjeta(obj):
    """
    Construye el resumen ligero de triaje IA para las tarjetas del listado
    sin consultas adicionales ni exposición de datos internos completos.
    """
    if obj is None:
        return None
    return {
        'id_triaje': obj.id_triaje,
        'automatizacion_activa': bool(getattr(settings, 'TRIAJE_N8N_ACTIVO', False)),
        'puede_reintentar': calcular_puede_reintentar_triaje(obj),
        'estado_envio': obj.estado_envio,
        'estado_analisis': obj.estado_analisis,
        'prioridad_sugerida': obj.prioridad_sugerida,
        'prioridad_aplicada': obj.prioridad_aplicada,
        'decision_prioridad': obj.decision_prioridad,
        'veterinario_recomendado_id': obj.veterinario_recomendado_id,
        'veterinario_recomendado_nombre': _nombre_usuario_triaje(
            getattr(obj, 'veterinario_recomendado', None)
        ),
        'veterinario_recomendado_sede': extraer_veterinario_recomendado_sede(obj),
        'veterinario_recomendado_carga': extraer_veterinario_recomendado_carga(obj),
        'decision_veterinario': obj.decision_veterinario,
        'veterinario_asignado_decision_id': obj.veterinario_asignado_decision_id,
        'contradiccion_foto_descripcion': bool(obj.contradiccion_foto_descripcion),
        'total_duplicados_detectados': int(obj.total_duplicados_detectados or 0),
        'completado_en': obj.completado_en.isoformat() if obj.completado_en else None,
        'fecha_actualizacion': obj.fecha_actualizacion.isoformat() if obj.fecha_actualizacion else None,
    }


class TriajeResultadoInputSerializer(serializers.Serializer):
    clave_idempotencia = serializers.CharField(max_length=120, required=False, allow_blank=True)
    despacho_token = serializers.CharField(max_length=64, required=False, allow_blank=True)
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
    justificacion = serializers.CharField(required=False, allow_blank=True, default='', max_length=3000)
    resumen_texto = serializers.CharField(required=False, allow_blank=True, default='', max_length=2000)
    resumen_vision = serializers.CharField(required=False, allow_blank=True, default='', max_length=2000)
    signos_detectados = serializers.ListField(
        child=serializers.CharField(max_length=80),
        required=False,
        default=list,
        max_length=15,
    )
    signos_reportados = serializers.ListField(
        child=serializers.CharField(max_length=120),
        required=False,
        default=list,
        max_length=20,
    )
    signos_fotografia = serializers.ListField(
        child=serializers.CharField(max_length=120),
        required=False,
        default=list,
        max_length=20,
    )
    contradiccion_foto_descripcion = serializers.BooleanField(required=False, default=False)
    advertencias_contradiccion = serializers.ListField(
        child=serializers.CharField(max_length=400),
        required=False,
        default=list,
        max_length=10,
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
    justificacion_recomendacion = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)
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


class TriajeDecisionInputSerializer(serializers.Serializer):
    tipo_recomendacion = serializers.ChoiceField(choices=['PRIORIDAD', 'VETERINARIO'])
    decision = serializers.ChoiceField(choices=['ACEPTADA', 'RECHAZADA'])
    observacion = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)
    motivo = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)


class TriajePeticionSerializer(serializers.ModelSerializer):
    id_peticion = serializers.IntegerField(source='peticion_id', read_only=True)
    numero_radicado = serializers.CharField(source='peticion.numero_radicado', read_only=True)
    prioridad_actual_peticion = serializers.CharField(source='peticion.prioridad', read_only=True)
    estado_peticion = serializers.CharField(source='peticion.id_estado.nombre', read_only=True, default=None)
    asignado_a_id_peticion = serializers.IntegerField(source='peticion.asignado_a_id', read_only=True)
    veterinario_recomendado_id = serializers.IntegerField(read_only=True, allow_null=True)
    veterinario_recomendado_nombre = serializers.SerializerMethodField()
    veterinario_recomendado_sede_id = serializers.IntegerField(
        source='veterinario_recomendado.id_sede_id',
        read_only=True,
        allow_null=True,
    )
    veterinario_recomendado_sede = serializers.SerializerMethodField()
    veterinario_recomendado_carga = serializers.SerializerMethodField()
    posibles_duplicados_detalle = serializers.SerializerMethodField()
    automatizacion_activa = serializers.SerializerMethodField()
    tiene_fotografias = serializers.SerializerMethodField()
    puede_reintentar = serializers.SerializerMethodField()
    decision_prioridad_por_id = serializers.IntegerField(read_only=True, allow_null=True)
    decision_prioridad_por_nombre = serializers.SerializerMethodField()
    decision_veterinario_por_id = serializers.IntegerField(read_only=True, allow_null=True)
    decision_veterinario_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = TriajePeticion
        fields = [
            'id_triaje',
            'id_peticion',
            'numero_radicado',
            'clave_idempotencia',
            'automatizacion_activa',
            'tiene_fotografias',
            'puede_reintentar',
            'estado_envio',
            'estado_analisis',
            'intentos_envio',
            'max_intentos',
            'lease_expira_en',
            'ultimo_intento_en',
            'proximo_reintento_en',
            'ultimo_codigo_http',
            'ultimo_error_envio',
            'enviado_en',
            'completado_en',
            'n8n_execution_id',
            'version_resultado',
            'prioridad_original',
            'prioridad_actual_peticion',
            'estado_peticion',
            'prioridad_sugerida',
            'nivel_confianza',
            'justificacion',
            'resumen_texto',
            'resumen_vision',
            'signos_detectados',
            'signos_reportados',
            'signos_fotografia',
            'contradiccion_foto_descripcion',
            'advertencias_contradiccion',
            'posibles_duplicados',
            'posibles_duplicados_detalle',
            'total_duplicados_detectados',
            'asignado_a_id_peticion',
            'veterinario_recomendado_id',
            'veterinario_recomendado_nombre',
            'veterinario_recomendado_sede_id',
            'veterinario_recomendado_sede',
            'veterinario_recomendado_carga',
            'justificacion_recomendacion',
            'carga_veterinarios_snapshot',
            'decision_prioridad',
            'decision_prioridad_por_id',
            'decision_prioridad_por_nombre',
            'decision_prioridad_en',
            'decision_prioridad_observacion',
            'prioridad_aplicada',
            'decision_veterinario',
            'decision_veterinario_por_id',
            'decision_veterinario_por_nombre',
            'decision_veterinario_en',
            'decision_veterinario_observacion',
            'veterinario_asignado_decision_id',
            'historial_decisiones',
            'modelo_texto_usado',
            'modelo_vision_usado',
            'fecha_creacion',
            'fecha_actualizacion',
        ]
        read_only_fields = fields

    def _nombre_usuario(self, user_obj):
        return _nombre_usuario_triaje(user_obj)

    def get_automatizacion_activa(self, obj):
        return bool(getattr(settings, 'TRIAJE_N8N_ACTIVO', False))

    def get_tiene_fotografias(self, obj):
        peticion = getattr(obj, 'peticion', None)
        if not peticion:
            return False
        _, todas = extraer_fotografias_peticion(peticion)
        return any(detectar_tipo_archivo(u) == 'imagen' for u in todas)

    def get_puede_reintentar(self, obj):
        return calcular_puede_reintentar_triaje(obj)

    def get_veterinario_recomendado_nombre(self, obj):
        return self._nombre_usuario(getattr(obj, 'veterinario_recomendado', None))

    def get_veterinario_recomendado_sede(self, obj):
        return extraer_veterinario_recomendado_sede(obj)

    def get_veterinario_recomendado_carga(self, obj):
        return extraer_veterinario_recomendado_carga(obj)

    def get_posibles_duplicados_detalle(self, obj):
        candidatos = getattr(obj, 'posibles_duplicados', None) or []
        if not isinstance(candidatos, list) or not candidatos:
            return []
        pet_id = getattr(obj, 'peticion_id', None)
        alertas_por_otro = {}
        if pet_id:
            alertas = (
                PeticionPosibleDuplicado.objects
                .filter(peticion_menor_id=pet_id)
                .only('id_alerta', 'peticion_menor_id', 'peticion_mayor_id', 'estado_revision', 'similitud_texto', 'porcentaje_similitud_visual', 'distancia_metros', 'metodo_imagen')
                | PeticionPosibleDuplicado.objects
                .filter(peticion_mayor_id=pet_id)
                .only('id_alerta', 'peticion_menor_id', 'peticion_mayor_id', 'estado_revision', 'similitud_texto', 'porcentaje_similitud_visual', 'distancia_metros', 'metodo_imagen')
            )
            for al in alertas:
                otro_id = al.peticion_mayor_id if al.peticion_menor_id == pet_id else al.peticion_menor_id
                alertas_por_otro[otro_id] = al

        resultado = []
        for c in candidatos:
            if not isinstance(c, dict):
                continue
            cand_id = c.get('id_peticion')
            alerta = alertas_por_otro.get(cand_id)
            item = {**c}
            if alerta is not None:
                item['id_alerta'] = alerta.id_alerta
                item['estado_revision'] = alerta.estado_revision
                if item.get('similitud_texto') is None and alerta.similitud_texto is not None:
                    item['similitud_texto'] = alerta.similitud_texto
                if item.get('porcentaje_similitud_visual') is None and alerta.porcentaje_similitud_visual is not None:
                    item['porcentaje_similitud_visual'] = alerta.porcentaje_similitud_visual
                if item.get('distancia_metros') is None and alerta.distancia_metros is not None:
                    item['distancia_metros'] = alerta.distancia_metros
                if not item.get('metodo_imagen') and alerta.metodo_imagen:
                    item['metodo_imagen'] = alerta.metodo_imagen
            else:
                item.setdefault('id_alerta', None)
                item.setdefault('estado_revision', None)
            resultado.append(item)
        return resultado

    def get_decision_prioridad_por_nombre(self, obj):
        return self._nombre_usuario(getattr(obj, 'decision_prioridad_por', None))

    def get_decision_veterinario_por_nombre(self, obj):
        return self._nombre_usuario(getattr(obj, 'decision_veterinario_por', None))
