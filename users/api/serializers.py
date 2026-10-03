# serializers.py
import re # AnaC
from django.db.models import Q
from rest_framework import serializers
# Importamos el hash de contraseñas de Django. 
# Esto es CRÍTICO: nunca guardes contraseñas en texto plano.
from django.contrib.auth.hashers import make_password, check_password
from users.models import Usuarios, Rol

class RegisterCustomSerializer(serializers.ModelSerializer):
    # Ya no declaramos id_rol aquí arriba. 
    # Dejamos que DRF use exclusivamente lo definido en Meta.fields.

    class Meta:
        model = Usuarios
        fields = ('email', 'identificacion', 'password', 'nombre', 'apellido', 'telefono')
        extra_kwargs = {'password': {'write_only': True}}

    # AnaC
    def validate_password(self, value):
        """Valida que la contraseña cumpla con las reglas de negocio de seguridad."""
        if len(value) < 8:
            raise serializers.ValidationError("La contraseña debe tener al menos 8 caracteres.")
        
        if not re.search(r'[A-Z]', value):
            raise serializers.ValidationError("La contraseña debe incluir al menos una letra mayúscula.")
            
        return value
    # AnaC

    def create(self, validated_data):
        validated_data['password'] = make_password(validated_data['password'])
        
        # Obtenemos o creamos el rol por defecto
        rol_peticionario, _ = Rol.objects.get_or_create(nombre_rol='Peticionario')
        
        # Inyectamos el rol directamente en los datos validados antes de guardar
        validated_data['id_rol'] = rol_peticionario
        
        return super().create(validated_data)
class LoginCustomSerializer(serializers.Serializer):
    """
    Serializador que genera el formulario de Login en Swagger y
    verifica que el correo y la contraseña coincidan en PostgreSQL.
    """
    # Exigimos email y contraseña. Swagger usará estos campos para dibujar el formulario.
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        # 1. Buscamos al usuario por correo en nuestra tabla personalizada.
        # Si usas User.objects.get(), fallará si el correo no existe, por eso usamos filter y .first().
        user = Usuarios.objects.filter(email=data['email']).first()

        # 2. Validaciones encadenadas:
        # - ¿El usuario existe?
        # - check_password compara la contraseña plana de Swagger con el hash guardado en PostgreSQL.
        # - ¿El usuario tiene activo=True?
        if user and check_password(data['password'], user.password):
            if not user.activo:
                 raise serializers.ValidationError("Esta cuenta ha sido desactivada.")
            return user
        
        # Si algo de arriba falla, rechazamos el login.
        raise serializers.ValidationError("Credenciales incorrectas.")

class UsuariosSerializer(serializers.ModelSerializer):

    nombre_rol = serializers.CharField(
        source='id_rol.nombre_rol',
        read_only=True
    )
    telefono = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )

    class Meta:
        model = Usuarios
        fields = (
            'id_usuario',
            'email',
            'identificacion',
            'nombre',
            'apellido',
            'telefono',
            'id_rol',
            'nombre_rol',
            'activo',
        )

    def validate(self, data):
        request = self.context.get('request')
        instance = getattr(self, 'instance', None)

        # Solo aplicamos estas restricciones cuando se está actualizando un usuario existente
        if instance and request and request.user and request.user.is_authenticated:
            # -------------------------------------------------------------
            # REGLA 1: Anti Auto-Bloqueo
            # Un administrador no puede quitarse su propio rol ni auto-desactivarse
            # -------------------------------------------------------------
            if request.user.id_usuario == instance.id_usuario:
                if 'id_rol' in data:
                    nuevo_rol_id = getattr(data['id_rol'], 'pk', None)
                    rol_actual_id = getattr(instance, 'id_rol_id', None)
                    if nuevo_rol_id != rol_actual_id:
                        raise serializers.ValidationError({
                            "id_rol": "Por seguridad del sistema, no puedes modificar tu propio rol de Administrador. Esta acción debe ser realizada por otro Administrador."
                        })

                if data.get('activo') is False and instance.activo is True:
                    raise serializers.ValidationError({
                        "activo": "Por seguridad del sistema, no puedes desactivar tu propia cuenta mientras tu sesión esté activa."
                    })

            # -------------------------------------------------------------
            # REGLA 2: Quórum Mínimo / El Último Administrador
            # No se puede degradar ni desactivar al único Administrador activo
            # -------------------------------------------------------------
            rol_actual = getattr(instance, 'id_rol', None)
            nombre_rol_actual = getattr(rol_actual, 'nombre_rol', '').strip().lower() if rol_actual else ''
            id_rol_actual = getattr(rol_actual, 'id_rol', None) if rol_actual else None
            es_admin_actualmente = (nombre_rol_actual in ['administrador', 'admin'] or id_rol_actual == 1)

            if es_admin_actualmente and instance.activo:
                cambia_a_no_admin = False
                if 'id_rol' in data:
                    nuevo_rol = data.get('id_rol')
                    nuevo_nombre = getattr(nuevo_rol, 'nombre_rol', '').strip().lower() if nuevo_rol else ''
                    nuevo_id = getattr(nuevo_rol, 'pk', None)
                    if nuevo_nombre not in ['administrador', 'admin'] and nuevo_id != 1:
                        cambia_a_no_admin = True

                inactiva_cuenta = (data.get('activo') is False)

                if cambia_a_no_admin or inactiva_cuenta:
                    otros_admins = Usuarios.objects.filter(
                        activo=True
                    ).filter(
                        Q(id_rol__nombre_rol__iexact='administrador') |
                        Q(id_rol__nombre_rol__iexact='admin') |
                        Q(id_rol=1)
                    ).exclude(id_usuario=instance.id_usuario).count()

                    if otros_admins == 0:
                        raise serializers.ValidationError(
                            "Operación denegada: Este usuario es el único Administrador activo del sistema. "
                            "No se puede cambiar su rol ni desactivar su cuenta sin antes designar a otro Administrador."
                        )

        return data


class RolSerializer(serializers.ModelSerializer):

    class Meta:
        model = Rol
        fields = (
            'id_rol',
            'nombre_rol',
        )
