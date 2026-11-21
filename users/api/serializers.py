# serializers.py
import re # AnaC
from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
# Importamos el hash de contraseñas de Django. 
# Esto es CRÍTICO: nunca guardes contraseñas en texto plano.
from django.contrib.auth.hashers import make_password, check_password
from django.db import IntegrityError
from users.models import Usuarios, Rol, Sede
from users.constants import ROL_ADMINISTRADOR, ROL_SUPER_ADMINISTRADOR
from users.api.permissions import es_super_administrador, NOMBRES_ROL_SUPER_ADMIN
from peticiones.utils import subir_o_asegurar_cloudinary


def _normalizar_espacios(valor: str) -> str:
    """Normaliza espacios múltiples internos y elimina espacios al inicio y al final."""
    if valor is None:
        return ''
    return re.sub(r'\s+', ' ', str(valor)).strip()

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
        # 1. Buscamos al usuario por correo en nuestra tabla personalizada (insensible a mayúsculas/espacios).
        email_limpio = (data.get('email') or '').strip()
        user = Usuarios.objects.filter(email__iexact=email_limpio).first()

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
    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True
    )
    telefono = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    matricula_profesional = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    foto_perfil = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    foto = serializers.SerializerMethodField(read_only=True)
    nombre_sede = serializers.SerializerMethodField(read_only=True)

    def get_foto(self, obj):
        return getattr(obj, 'foto_perfil', None) or ''

    def get_nombre_sede(self, obj):
        sede = getattr(obj, 'id_sede', None)
        return getattr(sede, 'nombre', None) if sede else None

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = data.copy()
            if 'foto' in data and ('foto_perfil' not in data or not data.get('foto_perfil')):
                data['foto_perfil'] = data.get('foto')
        return super().to_internal_value(data)

    class Meta:
        model = Usuarios
        fields = (
            'id_usuario',
            'email',
            'identificacion',
            'password',
            'nombre',
            'apellido',
            'telefono',
            'matricula_profesional',
            'foto_perfil',
            'foto',
            'id_rol',
            'nombre_rol',
            'id_sede',
            'nombre_sede',
            'activo',
        )
        extra_kwargs = {
            'email': {'required': False},
            'identificacion': {'required': False},
            'password': {'write_only': True, 'required': False},
            'id_rol': {'required': False},
            'id_sede': {'required': False, 'allow_null': True},
            'matricula_profesional': {'required': False},
            'foto_perfil': {'required': False},
        }

    def _generar_password_por_defecto(self, validated_data):
        identificacion = (validated_data.get('identificacion') or '').strip()
        nombre = (validated_data.get('nombre') or '').strip()
        inicial = nombre[0].upper() if nombre else 'V'
        if not inicial.isalpha():
            inicial = 'V'
        base_id = identificacion if identificacion else '12345678'
        return f"{base_id}{inicial}"

    def create(self, validated_data):
        request = self.context.get('request')
        user_req = getattr(request, 'user', None)
        rol_req = getattr(user_req, 'id_rol', None)
        id_rol_req = getattr(rol_req, 'id_rol', None) if rol_req else getattr(user_req, 'id_rol_id', None)
        es_super_req = es_super_administrador(user_req)
        es_admin_req = (id_rol_req == 1 or es_super_req)

        # Si quien crea NO es administrador o no está autenticado, forzar Peticionario (rol 4)
        if not es_admin_req:
            rol_peticionario, _ = Rol.objects.get_or_create(nombre_rol='Peticionario')
            validated_data['id_rol'] = rol_peticionario
        elif 'id_rol' in validated_data and not es_super_req:
            rol_obj = validated_data.get('id_rol')
            rol_obj_id = getattr(rol_obj, 'id_rol', getattr(rol_obj, 'pk', None))
            rol_obj_nom = (getattr(rol_obj, 'nombre_rol', '') or '').strip().lower()
            if rol_obj_id == ROL_SUPER_ADMINISTRADOR or rol_obj_nom in NOMBRES_ROL_SUPER_ADMIN:
                raise PermissionDenied("Solo un Super Administrador puede asignar el rol de Super Administrador.")

        raw_password = (validated_data.pop('password', None) or '').strip()
        if not raw_password:
            raw_password = self._generar_password_por_defecto(validated_data)
        validated_data['password'] = make_password(raw_password)

        if not validated_data.get('id_rol'):
            rol_peticionario, _ = Rol.objects.get_or_create(nombre_rol='Peticionario')
            validated_data['id_rol'] = rol_peticionario

        return super().create(validated_data)

    def update(self, instance, validated_data):
        raw_password = (validated_data.pop('password', None) or '').strip()
        if raw_password:
            validated_data['password'] = make_password(raw_password)
        return super().update(instance, validated_data)

    def validate_foto_perfil(self, value):
        if not value:
            return None
        return subir_o_asegurar_cloudinary(value)

    def validate(self, data):
        request = self.context.get('request')
        instance = getattr(self, 'instance', None)

        es_admin_req = False
        es_super_req = False
        if request and request.user and getattr(request.user, 'is_authenticated', False):
            rol_req = getattr(request.user, 'id_rol', None)
            nom_rol = getattr(rol_req, 'nombre_rol', '').strip().lower() if rol_req else ''
            id_rol_req = getattr(rol_req, 'id_rol', None) if rol_req else getattr(request.user, 'id_rol_id', None)
            es_super_req = es_super_administrador(request.user)
            es_admin_req = (nom_rol in ['administrador', 'admin'] or id_rol_req == 1 or es_super_req)

        # Prohibición estricta: un Administrador (o cualquier otro rol no Super Admin)
        # NO puede crear ni asignar el rol de Super Administrador.
        if 'id_rol' in data and not es_super_req:
            nuevo_rol_obj = data.get('id_rol')
            nuevo_rol_id = getattr(nuevo_rol_obj, 'id_rol', getattr(nuevo_rol_obj, 'pk', None))
            nuevo_rol_nom = (getattr(nuevo_rol_obj, 'nombre_rol', '') or '').strip().lower()
            if nuevo_rol_id == ROL_SUPER_ADMINISTRADOR or nuevo_rol_nom in NOMBRES_ROL_SUPER_ADMIN:
                if es_admin_req:
                    raise PermissionDenied("Los Administradores no están autorizados para crear ni asignar el rol de Super Administrador.")
                if instance is not None:
                    raise PermissionDenied("No cuenta con privilegios para modificar el rol del usuario.")

        # Solo aplicamos estas restricciones cuando se está actualizando un usuario existente
        if instance is not None:
            rol_actual = getattr(instance, 'id_rol', None)
            nombre_rol_actual = getattr(rol_actual, 'nombre_rol', '').strip().lower() if rol_actual else ''
            id_rol_actual = getattr(rol_actual, 'id_rol', None) if rol_actual else getattr(instance, 'id_rol_id', None)

            if (id_rol_actual == ROL_SUPER_ADMINISTRADOR or nombre_rol_actual in NOMBRES_ROL_SUPER_ADMIN) and not es_super_req:
                raise PermissionDenied("No cuenta con privilegios para modificar una cuenta de Super Administrador.")

            if not es_admin_req:
                if 'id_rol' in data:
                    nuevo_rol_obj = data.get('id_rol')
                    nuevo_rol_id = getattr(nuevo_rol_obj, 'id_rol', getattr(nuevo_rol_obj, 'pk', None))
                    rol_actual_id = getattr(instance, 'id_rol_id', getattr(getattr(instance, 'id_rol', None), 'id_rol', None))
                    if nuevo_rol_id is not None and nuevo_rol_id != rol_actual_id:
                        raise PermissionDenied("No cuenta con privilegios para modificar el rol del usuario.")
                if 'activo' in data and data.get('activo') != getattr(instance, 'activo', True):
                    raise PermissionDenied("No cuenta con privilegios para modificar el estado activo de la cuenta.")
                data.pop('id_rol', None)
                data.pop('activo', None)
                data.pop('id_sede', None)

            # -------------------------------------------------------------
            # REGLA 1: Anti Auto-Bloqueo
            # Un administrador no puede quitarse su propio rol ni auto-desactivarse
            # -------------------------------------------------------------
            if request and request.user and getattr(request.user, 'id_usuario', None) == instance.id_usuario:
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
            # (El Super Administrador puede gestionar administradores libremente)
            # -------------------------------------------------------------
            es_admin_actualmente = (nombre_rol_actual in ['administrador', 'admin'] or id_rol_actual == 1)

            if es_admin_actualmente and instance.activo and not es_super_req:
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


class SedeSerializer(serializers.ModelSerializer):
    """
    Serializador del módulo Super Administrador: Gestión de Sedes.
    Valida campos obligatorios, normaliza espacios, verifica unicidad
    insensible a mayúsculas/minúsculas de nombre y código, fuerza activo=True
    en la creación e incluye total_usuarios.
    """
    nombre = serializers.CharField(
        required=True,
        allow_blank=False,
        allow_null=False,
        max_length=150,
        validators=[],
        error_messages={
            'required': 'El nombre de la sede es obligatorio.',
            'blank': 'El nombre de la sede es obligatorio.',
            'null': 'El nombre de la sede es obligatorio.',
        },
    )
    direccion = serializers.CharField(
        required=True,
        allow_blank=False,
        allow_null=False,
        max_length=255,
        error_messages={
            'required': 'La dirección de la sede es obligatoria.',
            'blank': 'La dirección de la sede es obligatoria.',
            'null': 'La dirección de la sede es obligatoria.',
        },
    )
    ciudad = serializers.CharField(
        required=True,
        allow_blank=False,
        allow_null=False,
        max_length=100,
        error_messages={
            'required': 'La ciudad de la sede es obligatoria.',
            'blank': 'La ciudad de la sede es obligatoria.',
            'null': 'La ciudad de la sede es obligatoria.',
        },
    )
    telefono = serializers.CharField(
        required=True,
        allow_blank=False,
        allow_null=False,
        max_length=30,
        error_messages={
            'required': 'El teléfono de la sede es obligatorio.',
            'blank': 'El teléfono de la sede es obligatorio.',
            'null': 'El teléfono de la sede es obligatorio.',
        },
    )
    codigo = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=50,
        validators=[],
    )
    activo = serializers.BooleanField(read_only=True, default=True)
    total_usuarios = serializers.SerializerMethodField(read_only=True)
    fecha_creacion = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Sede
        fields = (
            'id_sede',
            'nombre',
            'direccion',
            'ciudad',
            'telefono',
            'activo',
            'codigo',
            'total_usuarios',
            'fecha_creacion',
        )
        read_only_fields = (
            'id_sede',
            'activo',
            'total_usuarios',
            'fecha_creacion',
        )

    def get_total_usuarios(self, obj):
        conteo_anotado = getattr(obj, 'total_usuarios_count', None)
        if conteo_anotado is not None:
            return int(conteo_anotado)
        pk = getattr(obj, 'pk', getattr(obj, 'id_sede', None))
        if pk is not None:
            return Usuarios.objects.filter(id_sede_id=pk).count()
        return 0

    def validate_nombre(self, value):
        nombre_limpio = _normalizar_espacios(value)
        if not nombre_limpio:
            raise serializers.ValidationError("El nombre de la sede es obligatorio.")

        qs = Sede.objects.all()
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.filter(nombre__iexact=nombre_limpio).exists():
            raise serializers.ValidationError("Ya existe una sede registrada con este nombre.")

        for existente in qs.only('id_sede', 'nombre'):
            if _normalizar_espacios(existente.nombre).lower() == nombre_limpio.lower():
                raise serializers.ValidationError("Ya existe una sede registrada con este nombre.")

        return nombre_limpio

    def validate_direccion(self, value):
        direccion_limpia = _normalizar_espacios(value)
        if not direccion_limpia:
            raise serializers.ValidationError("La dirección de la sede es obligatoria.")
        return direccion_limpia

    def validate_ciudad(self, value):
        ciudad_limpia = _normalizar_espacios(value)
        if not ciudad_limpia:
            raise serializers.ValidationError("La ciudad de la sede es obligatoria.")
        return ciudad_limpia

    def validate_telefono(self, value):
        telefono_limpio = _normalizar_espacios(value)
        if not telefono_limpio:
            raise serializers.ValidationError("El teléfono de la sede es obligatorio.")
        return telefono_limpio

    def validate_codigo(self, value):
        if value is None:
            return None
        codigo_limpio = _normalizar_espacios(value)
        if not codigo_limpio:
            return None

        qs = Sede.objects.all()
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.filter(codigo__iexact=codigo_limpio).exists():
            raise serializers.ValidationError("Ya existe una sede registrada con este código.")

        for existente in qs.exclude(codigo__isnull=True).only('id_sede', 'codigo'):
            if _normalizar_espacios(existente.codigo).lower() == codigo_limpio.lower():
                raise serializers.ValidationError("Ya existe una sede registrada con este código.")

        return codigo_limpio

    def create(self, validated_data):
        # Forzar activo=True desde el backend ignorando cualquier intento del cliente de manipular campos internos
        validated_data['activo'] = True
        try:
            return Sede.objects.create(**validated_data)
        except IntegrityError:
            raise serializers.ValidationError({
                "detail": "Ya existe una sede con el mismo nombre o código institucional."
            })


class SedeEstadoSerializer(serializers.Serializer):
    """
    Serializador exclusivo para PATCH /api/sedes/{id}/ que valida y actualiza
    únicamente el estado booleano 'activo' de una sede.
    """
    activo = serializers.BooleanField(
        required=True,
        allow_null=False,
        error_messages={
            'required': "El campo 'activo' es obligatorio.",
            'null': "El campo 'activo' no puede ser nulo.",
            'invalid': "El campo 'activo' debe ser un valor booleano (true o false).",
        },
    )

    def validate_activo(self, value):
        if isinstance(self.initial_data, dict):
            raw_val = self.initial_data.get('activo')
            if not isinstance(raw_val, bool) and str(raw_val).strip().lower() not in ('true', 'false'):
                raise serializers.ValidationError("El campo 'activo' debe ser un valor booleano (true o false).")
        return bool(value)


class AdministradorSuperAdminSerializer(UsuariosSerializer):
    """
    Serializador reutilizando UsuariosSerializer para el CRUD de Administradores
    exclusivo del Super Administrador (/api/super-administrador/administradores/).
    Garantiza que los usuarios gestionados tengan siempre el rol Administrador (id_rol = 1).
    """
    email = serializers.EmailField(
        required=False,
        max_length=150,
        validators=[],
    )
    identificacion = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=20,
        validators=[],
    )

    class Meta(UsuariosSerializer.Meta):
        model = Usuarios
        fields = UsuariosSerializer.Meta.fields
        read_only_fields = ('id_usuario', 'nombre_rol', 'nombre_sede', 'foto')

    def _obtener_rol_administrador(self):
        rol_admin = (
            Rol.objects.filter(id_rol=ROL_ADMINISTRADOR).first()
            or Rol.objects.filter(nombre_rol__iexact='Administrador').first()
        )
        if rol_admin is None:
            rol_admin = Rol(id_rol=ROL_ADMINISTRADOR, nombre_rol='Administrador')
        return rol_admin

    def validate_email(self, value):
        email_limpio = (value or '').strip()
        if not email_limpio:
            return email_limpio
        qs = Usuarios.objects.all()
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.filter(email__iexact=email_limpio).exists():
            raise serializers.ValidationError('Ya existe un usuario registrado con este correo electrónico.')
        return email_limpio

    def validate_identificacion(self, value):
        if value is None:
            return None
        id_limpio = str(value).strip()
        if not id_limpio:
            return None
        qs = Usuarios.objects.all()
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.filter(identificacion__iexact=id_limpio).exists():
            raise serializers.ValidationError('Ya existe un usuario registrado con esta identificación.')
        return id_limpio

    def validate(self, data):
        instance = getattr(self, 'instance', None)

        if instance is None:
            email = (data.get('email') or '').strip()
            nombre = (data.get('nombre') or '').strip()
            errores = {}
            if not email:
                errores['email'] = 'El correo electrónico es obligatorio para crear un administrador.'
            if not nombre:
                errores['nombre'] = 'El nombre es obligatorio para crear un administrador.'
            if errores:
                raise serializers.ValidationError(errores)

        if 'id_rol' in data and data['id_rol'] is not None:
            rol_obj = data['id_rol']
            rol_id = getattr(rol_obj, 'id_rol', getattr(rol_obj, 'pk', None))
            if rol_id is not None and int(rol_id) != ROL_ADMINISTRADOR:
                raise serializers.ValidationError({
                    'id_rol': 'En este módulo únicamente se permite gestionar usuarios con rol Administrador (id_rol = 1).'
                })

        return data

    def create(self, validated_data):
        validated_data['id_rol'] = self._obtener_rol_administrador()
        if 'activo' not in validated_data or validated_data['activo'] is None:
            validated_data['activo'] = True

        raw_password = (validated_data.pop('password', None) or '').strip()
        if not raw_password:
            raw_password = self._generar_password_por_defecto(validated_data)
        validated_data['password'] = make_password(raw_password)

        return Usuarios.objects.create(**validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('id_rol', None)
        raw_password = (validated_data.pop('password', None) or '').strip()
        if raw_password:
            validated_data['password'] = make_password(raw_password)
        return super(UsuariosSerializer, self).update(instance, validated_data)

