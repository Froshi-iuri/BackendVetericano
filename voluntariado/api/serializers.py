from rest_framework import serializers
from voluntariado.models import (
    EventoVoluntariado,
    PostulacionVoluntariado,
    TiposEventos,
    Eventos,
    VoluntarioEvento,
)


class EventoVoluntariadoSerializer(serializers.ModelSerializer):
    total_postulados = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = EventoVoluntariado
        fields = '__all__'

    def get_total_postulados(self, obj):
        return obj.postulaciones.count()


class PostulacionVoluntariadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostulacionVoluntariado
        fields = '__all__'


class PostuladoDetalleSerializer(serializers.ModelSerializer):
    nombre = serializers.SerializerMethodField()
    apellido = serializers.SerializerMethodField()
    email = serializers.EmailField(source='correo', read_only=True)
    fechaPostulacion = serializers.DateTimeField(source='fecha_postulacion', read_only=True)

    class Meta:
        model = PostulacionVoluntariado
        fields = [
            'id',
            'nombre',
            'apellido',
            'nombre_completo',
            'email',
            'correo',
            'telefono',
            'identificacion',
            'edad',
            'estado',
            'fecha_postulacion',
            'fechaPostulacion',
            'evento',
            'usuario',
        ]

    def get_nombre(self, obj):
        if obj.usuario and obj.usuario.nombre:
            return obj.usuario.nombre
        return obj.nombre_completo

    def get_apellido(self, obj):
        if obj.usuario and obj.usuario.apellido:
            return obj.usuario.apellido
        return ''


class TiposEventosSerializer(serializers.ModelSerializer):
    class Meta:
        model = TiposEventos
        fields = '__all__'


class EventosSerializer(serializers.ModelSerializer):
    class Meta:
        model = Eventos
        fields = '__all__'


class VoluntarioEventoSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoluntarioEvento
        fields = '__all__'
