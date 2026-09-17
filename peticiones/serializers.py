from rest_framework import serializers
from users.models import TipoPeticion, Peticiones, EstadoPeticiones, Ubicaciones, Usuarios


class TipoPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoPeticion
        fields = ['id_tipo', 'nombre', 'descripcion', 'activo']


# Alias para retrocompatibilidad
TiposPeticionSerializer = TipoPeticionSerializer


class PeticionSerializer(serializers.ModelSerializer):
    tipo_detalle = TipoPeticionSerializer(source='id_tipo', read_only=True)
    estado_nombre = serializers.CharField(source='id_estado.nombre', read_only=True)
    responsable_nombre = serializers.SerializerMethodField(read_only=True)

    id_estado = serializers.PrimaryKeyRelatedField(
        queryset=EstadoPeticiones.objects.all(),
        required=False,
        allow_null=True
    )
    id_ubicacion = serializers.PrimaryKeyRelatedField(
        queryset=Ubicaciones.objects.all(),
        required=False,
        allow_null=True
    )
    responsable = serializers.PrimaryKeyRelatedField(
        queryset=Usuarios.objects.all(),
        required=False,
        allow_null=True
    )

    class Meta:
        model = Peticiones
        fields = [
            'id_peticion',
            'id_tipo',
            'tipo_detalle',
            'id_ubicacion',
            'id_estado',
            'estado_nombre',
            'responsable',
            'responsable_nombre',
            'descripcion',
            'prioridad',
            'fecha_asignacion',
            'fecha'
        ]
        read_only_fields = ['id_peticion', 'fecha']

    def get_responsable_nombre(self, obj):
        if obj.responsable:
            return f"{obj.responsable.nombre} {obj.responsable.apellido}".strip()
        return None

    def create(self, validated_data):
        # Si no se envía id_estado, asignamos un estado inicial por defecto
        if not validated_data.get('id_estado'):
            estado_inicial = (
                EstadoPeticiones.objects.filter(nombre__iexact='Borrador').first()
                or EstadoPeticiones.objects.filter(nombre__iexact='Pendiente').first()
                or EstadoPeticiones.objects.filter(activo=True).order_by('orden', 'id_estado').first()
                or EstadoPeticiones.objects.first()
            )
            if estado_inicial:
                validated_data['id_estado'] = estado_inicial

        return super().create(validated_data)


# Alias para retrocompatibilidad
IniciarPeticionSerializer = PeticionSerializer