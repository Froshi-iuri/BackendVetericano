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

    def to_representation(self, instance):
        # Forzamos la lectura directa del atributo estado desde la instancia o la base de datos
        representation = super().to_representation(instance)
        estado_valor = getattr(instance, 'estado', None)
        
        if not estado_valor:
            # Si por alguna razón el ORM no lo mapeó, intentamos leerlo directo del diccionario de la base de datos si existe
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
        estado = validated_data.get('estado')
        if estado is None and self.initial_data and 'estado' in self.initial_data:
            estado = self.initial_data.get('estado')

        instance = super().update(instance, validated_data)

        if estado:
            estado_clean = str(estado).lower().strip()
            instance.estado = estado_clean
            try:
                instance.save(update_fields=['estado'])
            except Exception:
                try:
                    instance.save()
                except Exception:
                    pass

            try:
                from django.db import connection
                with connection.cursor() as cursor:
                    try:
                        cursor.execute("ALTER TABLE examen ADD COLUMN estado VARCHAR(50) DEFAULT 'activo'")
                    except Exception:
                        pass
                    cursor.execute("UPDATE examen SET estado = %s WHERE id_examen = %s", [estado_clean, instance.id_examen])
            except Exception as e:
                print("Error actualizando estado por SQL:", e)

        return instance

    def create(self, validated_data):
        estado = validated_data.get('estado')
        if estado is None and self.initial_data and 'estado' in self.initial_data:
            estado = self.initial_data.get('estado')

        instance = super().create(validated_data)

        if estado:
            estado_clean = str(estado).lower().strip()
            instance.estado = estado_clean
            try:
                instance.save()
            except Exception:
                pass

            try:
                from django.db import connection
                with connection.cursor() as cursor:
                    try:
                        cursor.execute("ALTER TABLE examen ADD COLUMN estado VARCHAR(50) DEFAULT 'activo'")
                    except Exception:
                        pass
                    cursor.execute("UPDATE examen SET estado = %s WHERE id_examen = %s", [estado_clean, instance.id_examen])
            except Exception as e:
                print("Error al asignar estado por SQL al crear:", e)

        return instance


class ProcedimientoRealizadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcedimientoRealizado
        fields = (
            'id_procedimiento',
            'id_hospitalizacion',
            'descripcion',
        )
        read_only_fields = ('id_procedimiento',)