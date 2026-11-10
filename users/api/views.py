import unicodedata
from django.db import IntegrityError, transaction
from django.db.models import Q, Count
from django.db.models.deletion import ProtectedError, RestrictedError
from django.utils.dateparse import parse_date
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import generics, mixins, serializers, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from .serializers import (
    RegisterCustomSerializer,
    LoginCustomSerializer,
    UsuariosSerializer,
    RolSerializer,
    SedeSerializer,
    SedeEstadoSerializer,
    AdministradorSuperAdminSerializer,
)
from .permissions import (
    EsAdministradorOReadOnly,
    EsSuperAdministrador,
    es_super_administrador,
    NOMBRES_ROL_SUPER_ADMIN,
)
from users.constants import (
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_VETERINARIO,
    ROL_PETICIONARIO,
    ROL_SUPER_ADMINISTRADOR,
)
from users.models import Usuarios, Rol, Sede
from peticiones.models import (
    Peticiones,
    EstadoPeticiones,
    SeguimientoPeticionesVisita,
)
from peticiones.api.serializers import ListarPeticionesSerializer



class RegisterView(generics.GenericAPIView):

    # Esto es lo que le dice a Swagger:
    # "Usa este serializador para dibujar los campos".
    serializer_class = RegisterCustomSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):

        serializer = self.get_serializer(data=request.data)

        # Verifica duplicados de email, que los campos obligatorios existan,
        # y que id_rol sea válido.
        serializer.is_valid(raise_exception=True)

        # Guarda en la BD (invocando nuestro método create
        # con la contraseña encriptada).
        serializer.save()

        return Response({
            "mensaje": "Usuario creado exitosamente.",
            "datos": serializer.data
        }, status=status.HTTP_201_CREATED)


class LoginView(generics.GenericAPIView):

    # Configura los campos 'email' y 'password' en Swagger.
    serializer_class = LoginCustomSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data
        
        # Generamos el par de tokens (acceso y refresco) para este usuario
        refresh = RefreshToken.for_user(user)
        
        # Inyectamos datos extra al token para que el frontend no tenga que hacer peticiones extra
        foto_actual = getattr(user, 'foto_perfil', None)
        # Inyectamos datos extra al token para que el frontend no tenga que hacer peticiones extra
        refresh['id_rol'] = user.id_rol.id_rol
        refresh['nombre_rol'] = user.id_rol.nombre_rol
        refresh['foto_perfil'] = foto_actual or ''
        refresh['foto'] = foto_actual or ''

        return Response({
            "mensaje": f"Bienvenido, {user.nombre} {user.apellido}",
            "email": user.email,
            "id_usuario": user.id_usuario,
            "nombre": user.nombre,
            "apellido": user.apellido,
            "telefono": getattr(user, 'telefono', None),
            "foto_perfil": foto_actual,
            "foto": foto_actual,
            "matricula_profesional": getattr(user, 'matricula_profesional', None),
            "id_rol": user.id_rol.id_rol,
            "tokens": {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            }
        }, status=status.HTTP_200_OK)


class UsuariosViewSet(viewsets.ModelViewSet):
    queryset = Usuarios.objects.all()
    serializer_class = UsuariosSerializer
    permission_classes = [EsAdministradorOReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = getattr(self.request, 'user', None)
        rol_id = getattr(user, 'id_rol_id', None)
        if rol_id != 1 and not es_super_administrador(user):
            # Cualquier rol distinto de Administrador únicamente puede acceder a su propio registro
            return queryset.filter(id_usuario=getattr(user, 'id_usuario', None))
        return queryset

    def _validar_prohibicion_super_admin(self, request):
        if es_super_administrador(request.user):
            return
        if isinstance(request.data, dict) and 'id_rol' in request.data:
            raw_rol = request.data.get('id_rol')
            raw_rol_str = str(raw_rol).strip().lower() if raw_rol is not None else ''
            if raw_rol_str == str(ROL_SUPER_ADMINISTRADOR) or raw_rol_str in NOMBRES_ROL_SUPER_ADMIN:
                if getattr(request.user, 'id_rol_id', None) == ROL_ADMINISTRADOR:
                    raise PermissionDenied(
                        "Los Administradores no están autorizados para crear ni asignar el rol de Super Administrador."
                    )
                raise PermissionDenied("No cuenta con privilegios para asignar el rol de Super Administrador.")

    def create(self, request, *args, **kwargs):
        self._validar_prohibicion_super_admin(request)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._validar_prohibicion_super_admin(request)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._validar_prohibicion_super_admin(request)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        # 1. Anti Auto-Eliminación
        if request.user and request.user.id_usuario == instance.id_usuario:
            return Response(
                {"detail": "Por seguridad del sistema, no puedes eliminar tu propia cuenta de Administrador."},
                status=status.HTTP_400_BAD_REQUEST
            )

        rol = getattr(instance, 'id_rol', None)
        nombre_rol = getattr(rol, 'nombre_rol', '').strip().lower() if rol else ''
        id_rol = getattr(rol, 'id_rol', None) if rol else getattr(instance, 'id_rol_id', None)

        if (id_rol == ROL_SUPER_ADMINISTRADOR or nombre_rol in NOMBRES_ROL_SUPER_ADMIN) and not es_super_administrador(request.user):
            raise PermissionDenied("No cuenta con permisos para eliminar una cuenta de Super Administrador.")

        # 2. Quórum Mínimo: No eliminar al último administrador activo
        es_admin = (nombre_rol in ['administrador', 'admin'] or id_rol == 1)

        if es_admin and instance.activo and not es_super_administrador(request.user):
            otros_admins = Usuarios.objects.filter(
                activo=True
            ).filter(
                Q(id_rol__nombre_rol__iexact='administrador') |
                Q(id_rol__nombre_rol__iexact='admin') |
                Q(id_rol=1)
            ).exclude(id_usuario=instance.id_usuario).count()

            if otros_admins == 0:
                return Response(
                    {"detail": "Operación denegada: Este usuario es el único Administrador activo del sistema. No es posible eliminarlo sin antes designar a otro Administrador."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        return super().destroy(request, *args, **kwargs)



class RolViewSet(viewsets.ModelViewSet):
    queryset = Rol.objects.all()
    serializer_class = RolSerializer
    permission_classes = [EsAdministradorOReadOnly]

    def create(self, request, *args, **kwargs):
        nombre_rol = str(request.data.get('nombre_rol', '') if isinstance(request.data, dict) else '').strip().lower()
        if nombre_rol in NOMBRES_ROL_SUPER_ADMIN and not es_super_administrador(request.user):
            raise PermissionDenied("Solo el Super Administrador puede gestionar el rol de Super Administrador.")
        return super().create(request, *args, **kwargs)


class SedeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    ViewSet para el módulo Super Administrador: Gestión de Sedes.
    Endpoints:
    - GET /api/sedes/        : Lista sedes activas e inactivas con total_usuarios.
    - POST /api/sedes/       : Registra una nueva sede con activo=True.
    - GET /api/sedes/{id}/   : Consulta una sede específica.
    - PATCH /api/sedes/{id}/ : Modifica únicamente el estado activo de la sede.
    """
    queryset = Sede.objects.all()
    serializer_class = SedeSerializer
    permission_classes = [EsSuperAdministrador]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        queryset = Sede.objects.annotate(
            total_usuarios_count=Count('usuarios', distinct=True)
        ).order_by('id_sede')

        query_params = getattr(self.request, 'query_params', {})
        activo_param = query_params.get('activo') if query_params else None
        if activo_param is not None:
            valor = str(activo_param).strip().lower()
            if valor in ('true', '1'):
                queryset = queryset.filter(activo=True)
            elif valor in ('false', '0'):
                queryset = queryset.filter(activo=False)

        return queryset

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        estado_serializer = SedeEstadoSerializer(data=request.data)
        estado_serializer.is_valid(raise_exception=True)

        instance.activo = estado_serializer.validated_data['activo']
        instance.save(update_fields=['activo'])

        output_serializer = self.get_serializer(instance)
        return Response(output_serializer.data, status=status.HTTP_200_OK)


# ==============================================================================
# MÓDULO SUPER ADMINISTRADOR: GESTIÓN DE ADMINISTRADORES (CRUD) Y REPORTES
# ==============================================================================

def _normalizar_sin_acentos(texto: str) -> str:
    if not texto:
        return ''
    return ''.join(
        c for c in unicodedata.normalize('NFD', str(texto))
        if unicodedata.category(c) != 'Mn'
    ).lower().strip()


def _obtener_queryset_administradores():
    """Retorna el QuerySet base de usuarios con rol Administrador (id_rol = 1)."""
    return (
        Usuarios.objects.select_related('id_rol', 'id_sede')
        .filter(
            Q(id_rol_id=ROL_ADMINISTRADOR)
            | Q(id_rol__nombre_rol__iexact='administrador')
            | Q(id_rol__nombre_rol__iexact='admin')
        )
        .order_by('id_usuario')
    )


class SuperAdminCrearAdministradorView(generics.CreateAPIView):
    """
    POST /api/super-administrador/administradores/crear/
    Permite exclusivamente al Super Administrador crear nuevos usuarios con rol Administrador (id_rol = 1).
    """
    serializer_class = AdministradorSuperAdminSerializer
    permission_classes = [EsSuperAdministrador]

    @swagger_auto_schema(
        tags=['Super Administrador - Administradores'],
        operation_summary='Crear un nuevo Administrador',
        operation_description=(
            'Permite al Super Administrador registrar un nuevo usuario con rol Administrador (id_rol=1). '
            'Encripta la contraseña automáticamente y asigna activo=True por defecto.'
        ),
        request_body=AdministradorSuperAdminSerializer,
        responses={
            201: AdministradorSuperAdminSerializer,
            400: 'Datos inválidos o correo/identificación duplicados.',
            401: 'No autenticado (Token JWT faltante o inválido).',
            403: 'Acceso denegado (Exclusivo para Super Administrador).',
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class SuperAdminListarAdministradoresView(generics.ListAPIView):
    """
    GET /api/super-administrador/administradores/
    Lista todos los usuarios con rol Administrador (id_rol = 1).
    Soporta filtros opcionales: ?activo=true|false, ?id_sede=<id>, ?search=<texto>.
    """
    serializer_class = AdministradorSuperAdminSerializer
    permission_classes = [EsSuperAdministrador]

    def get_queryset(self):
        queryset = _obtener_queryset_administradores()
        query_params = getattr(self.request, 'query_params', {}) or {}

        activo_param = query_params.get('activo')
        if activo_param is not None:
            val = str(activo_param).strip().lower()
            if val in ('true', '1'):
                queryset = queryset.filter(activo=True)
            elif val in ('false', '0'):
                queryset = queryset.filter(activo=False)

        id_sede_param = query_params.get('id_sede')
        if id_sede_param is not None and str(id_sede_param).strip().isdigit():
            queryset = queryset.filter(id_sede_id=int(id_sede_param))

        search_param = (query_params.get('search') or '').strip()
        if search_param:
            queryset = queryset.filter(
                Q(nombre__icontains=search_param)
                | Q(apellido__icontains=search_param)
                | Q(email__icontains=search_param)
                | Q(identificacion__icontains=search_param)
            )

        return queryset

    @swagger_auto_schema(
        tags=['Super Administrador - Administradores'],
        operation_summary='Listar Administradores',
        operation_description='Lista todos los usuarios con rol Administrador registrados en el sistema.',
        manual_parameters=[
            openapi.Parameter(
                'activo',
                openapi.IN_QUERY,
                description='Filtrar por estado activo (true o false)',
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                'id_sede',
                openapi.IN_QUERY,
                description='Filtrar por ID de sede asignada',
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
            openapi.Parameter(
                'search',
                openapi.IN_QUERY,
                description='Buscar por nombre, apellido, email o identificación',
                type=openapi.TYPE_STRING,
                required=False,
            ),
        ],
        responses={
            200: AdministradorSuperAdminSerializer(many=True),
            401: 'No autenticado.',
            403: 'Acceso denegado (Exclusivo para Super Administrador).',
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class SuperAdminDetalleAdministradorView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET /api/super-administrador/administradores/<id>/
    PUT /api/super-administrador/administradores/<id>/
    PATCH /api/super-administrador/administradores/<id>/
    DELETE /api/super-administrador/administradores/<id>/
    """
    serializer_class = AdministradorSuperAdminSerializer
    permission_classes = [EsSuperAdministrador]
    lookup_field = 'id_usuario'
    lookup_url_kwarg = 'id'

    def get_queryset(self):
        return _obtener_queryset_administradores()

    @swagger_auto_schema(
        tags=['Super Administrador - Administradores'],
        operation_summary='Consultar Administrador por ID',
        operation_description='Obtiene la información detallada de un Administrador por su ID.',
        responses={
            200: AdministradorSuperAdminSerializer,
            401: 'No autenticado.',
            403: 'Acceso denegado (Exclusivo para Super Administrador).',
            404: 'Administrador no encontrado.',
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Super Administrador - Administradores'],
        operation_summary='Actualizar Administrador (PUT)',
        operation_description='Actualiza los datos de un Administrador existente.',
        request_body=AdministradorSuperAdminSerializer,
        responses={
            200: AdministradorSuperAdminSerializer,
            400: 'Datos inválidos.',
            401: 'No autenticado.',
            403: 'Acceso denegado (Exclusivo para Super Administrador).',
            404: 'Administrador no encontrado.',
        },
    )
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Super Administrador - Administradores'],
        operation_summary='Actualizar parcialmente o activar/desactivar Administrador (PATCH)',
        operation_description='Edita parcialmente un Administrador o cambia su estado activo (activo=true/false).',
        request_body=AdministradorSuperAdminSerializer,
        responses={
            200: AdministradorSuperAdminSerializer,
            400: 'Datos inválidos.',
            401: 'No autenticado.',
            403: 'Acceso denegado (Exclusivo para Super Administrador).',
            404: 'Administrador no encontrado.',
        },
    )
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Super Administrador - Administradores'],
        operation_summary='Eliminar o desactivar Administrador (DELETE)',
        operation_description=(
            'Elimina o desactiva un Administrador por su ID. '
            'Si se envía ?desactivar=true o si el administrador posee registros históricos asociados '
            '(integridad referencial), la cuenta se desactiva (activo=False) preservando la trazabilidad.'
        ),
        manual_parameters=[
            openapi.Parameter(
                'desactivar',
                openapi.IN_QUERY,
                description='Si es true, realiza desactivación lógica (activo=false) en lugar de borrado físico.',
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
        ],
        responses={
            200: 'Administrador eliminado o desactivado exitosamente.',
            401: 'No autenticado.',
            403: 'Acceso denegado (Exclusivo para Super Administrador).',
            404: 'Administrador no encontrado.',
        },
    )
    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        id_admin = instance.id_usuario

        query_params = getattr(request, 'query_params', {}) or {}
        eliminar_fisico = str(query_params.get('eliminar', '')).strip().lower() in ('true', '1', 'si')

        if eliminar_fisico:
            try:
                with transaction.atomic():
                    Usuarios.objects.filter(pk=id_admin)._raw_delete(using=Usuarios.objects.db)
                return Response(
                    {
                        'mensaje': 'Administrador eliminado exitosamente.',
                        'id_usuario': id_admin,
                        'eliminado': True,
                        'activo': False,
                    },
                    status=status.HTTP_200_OK,
                )
            except Exception:
                pass

        instance.activo = False
        instance.save(update_fields=['activo'])
        serializer = self.get_serializer(instance)
        return Response(
            {
                'mensaje': 'Administrador desactivado exitosamente.',
                'id_usuario': id_admin,
                'eliminado': False,
                'activo': False,
                'datos': serializer.data,
            },
            status=status.HTTP_200_OK,
        )


# ==============================================================================
# REPORTES DEL SUPER ADMINISTRADOR
# ==============================================================================

PARAMETROS_SWAGGER_REPORTES = [
    openapi.Parameter(
        'fecha',
        openapi.IN_QUERY,
        description='Filtrar por fecha exacta de la petición (formato YYYY-MM-DD)',
        type=openapi.TYPE_STRING,
        format=openapi.FORMAT_DATE,
        required=False,
    ),
    openapi.Parameter(
        'fecha_desde',
        openapi.IN_QUERY,
        description='Filtrar peticiones desde esta fecha inclusive (formato YYYY-MM-DD)',
        type=openapi.TYPE_STRING,
        format=openapi.FORMAT_DATE,
        required=False,
    ),
    openapi.Parameter(
        'fecha_hasta',
        openapi.IN_QUERY,
        description='Filtrar peticiones hasta esta fecha inclusive (formato YYYY-MM-DD)',
        type=openapi.TYPE_STRING,
        format=openapi.FORMAT_DATE,
        required=False,
    ),
    openapi.Parameter(
        'estado',
        openapi.IN_QUERY,
        description='Filtrar por nombre o ID del estado de la petición (ej. Atendida, En observación, Asignada)',
        type=openapi.TYPE_STRING,
        required=False,
    ),
    openapi.Parameter(
        'id_estado',
        openapi.IN_QUERY,
        description='Filtrar por ID numérico del estado (tabla estado_peticiones)',
        type=openapi.TYPE_INTEGER,
        required=False,
    ),
    openapi.Parameter(
        'veterinario',
        openapi.IN_QUERY,
        description='Filtrar por ID o nombre del veterinario asignado (asignado_a)',
        type=openapi.TYPE_STRING,
        required=False,
    ),
    openapi.Parameter(
        'asignado_a',
        openapi.IN_QUERY,
        description='Filtrar por ID del veterinario asignado (alias de veterinario)',
        type=openapi.TYPE_INTEGER,
        required=False,
    ),
]


def _obtener_filtro_casos_atendidos() -> Q:
    """
    Condición canónica para identificar peticiones/casos atendidos:
    peticiones en estados de atención/resolución clínica o que cuentan con
    al menos un acta registrada en seguimiento_peticiones_visita.
    """
    return (
        Q(id_estado__nombre__icontains='atendid')
        | Q(id_estado__nombre__icontains='resuelt')
        | Q(id_estado__nombre__icontains='finaliz')
        | Q(id_estado__nombre__icontains='alta')
        | Q(id_estado__nombre__icontains='tratam')
        | Q(id_estado__nombre__icontains='observac')
        | Q(id_estado__nombre__icontains='fallecid')
        | Q(id_estado__nombre__icontains='transferid')
        | Q(seguimientopeticionesvisita__isnull=False)
    )


def _aplicar_filtros_reportes_peticiones(queryset, query_params):
    """
    Aplica filtros opcionales por fecha (fecha, fecha_desde, fecha_hasta),
    estado (estado, id_estado) y veterinario asignado (veterinario, asignado_a, id_veterinario)
    sobre un QuerySet de Peticiones.
    """
    params = query_params or {}

    # 1. Filtro por fecha exacta (YYYY-MM-DD)
    fecha_raw = (params.get('fecha') or '').strip()
    if fecha_raw:
        fecha_parsed = parse_date(fecha_raw)
        if not fecha_parsed:
            raise serializers.ValidationError({
                'fecha': "Formato de fecha inválido. Use el formato 'YYYY-MM-DD'."
            })
        queryset = queryset.filter(fecha__date=fecha_parsed)

    # 2. Filtro por rango de fechas (fecha_desde / fecha_inicio, fecha_hasta / fecha_fin)
    fecha_desde_raw = (params.get('fecha_desde') or params.get('fecha_inicio') or '').strip()
    if fecha_desde_raw:
        fecha_desde_parsed = parse_date(fecha_desde_raw)
        if not fecha_desde_parsed:
            raise serializers.ValidationError({
                'fecha_desde': "Formato de fecha_desde inválido. Use el formato 'YYYY-MM-DD'."
            })
        queryset = queryset.filter(fecha__date__gte=fecha_desde_parsed)

    fecha_hasta_raw = (params.get('fecha_hasta') or params.get('fecha_fin') or '').strip()
    if fecha_hasta_raw:
        fecha_hasta_parsed = parse_date(fecha_hasta_raw)
        if not fecha_hasta_parsed:
            raise serializers.ValidationError({
                'fecha_hasta': "Formato de fecha_hasta inválido. Use el formato 'YYYY-MM-DD'."
            })
        queryset = queryset.filter(fecha__date__lte=fecha_hasta_parsed)

    # 3. Filtro por estado (id_estado o nombre de estado)
    id_estado_raw = (params.get('id_estado') or '').strip()
    if id_estado_raw:
        if not id_estado_raw.isdigit():
            raise serializers.ValidationError({
                'id_estado': 'El parámetro id_estado debe ser un número entero.'
            })
        queryset = queryset.filter(id_estado_id=int(id_estado_raw))

    estado_raw = (params.get('estado') or '').strip()
    if estado_raw:
        if estado_raw.isdigit():
            queryset = queryset.filter(id_estado_id=int(estado_raw))
        else:
            norm_buscado = _normalizar_sin_acentos(estado_raw)
            ids_coincidentes = []
            try:
                for est_obj in EstadoPeticiones.objects.all():
                    norm_est = _normalizar_sin_acentos(est_obj.nombre)
                    if norm_est == norm_buscado or norm_buscado in norm_est:
                        ids_coincidentes.append(est_obj.id_estado)
            except Exception:
                ids_coincidentes = []

            if ids_coincidentes:
                queryset = queryset.filter(id_estado_id__in=ids_coincidentes)
            else:
                queryset = queryset.filter(id_estado__nombre__icontains=estado_raw)

    # 4. Filtro por veterinario asignado (veterinario, asignado_a, id_veterinario, veterinario_asignado)
    vet_raw = (
        params.get('veterinario')
        or params.get('asignado_a')
        or params.get('id_veterinario')
        or params.get('veterinario_asignado')
        or ''
    )
    vet_raw = str(vet_raw).strip()
    if vet_raw:
        if vet_raw.isdigit():
            queryset = queryset.filter(asignado_a_id=int(vet_raw))
        else:
            queryset = queryset.filter(
                Q(asignado_a__nombre__icontains=vet_raw)
                | Q(asignado_a__apellido__icontains=vet_raw)
            )

    return queryset


class SuperAdminReportePeticionesView(generics.ListAPIView):
    """
    GET /api/super-administrador/reportes/peticiones/
    Consulta todas las peticiones del sistema con soporte de filtros por
    fecha (fecha, fecha_desde, fecha_hasta), estado (estado, id_estado)
    y veterinario asignado (veterinario, asignado_a, id_veterinario).
    """
    serializer_class = ListarPeticionesSerializer
    permission_classes = [EsSuperAdministrador]

    def get_queryset(self):
        queryset = (
            Peticiones.objects.select_related(
                'id_tipo',
                'id_estado',
                'id_ubicacion',
                'responsable',
                'asignado_a',
                'asignado_por',
            )
            .prefetch_related('evidenciapeticiones_set', 'historial_cambios')
            .all()
        )
        queryset = _aplicar_filtros_reportes_peticiones(
            queryset,
            getattr(self.request, 'query_params', {}),
        )
        return queryset.order_by('-fecha', '-id_peticion')

    @swagger_auto_schema(
        tags=['Super Administrador - Reportes'],
        operation_summary='Reporte de todas las peticiones',
        operation_description=(
            'Consulta todas las peticiones registradas en el sistema. '
            'Permite filtrar por fecha (fecha, fecha_desde, fecha_hasta), '
            'estado (estado, id_estado) y veterinario asignado (veterinario, asignado_a).'
        ),
        manual_parameters=PARAMETROS_SWAGGER_REPORTES,
        responses={
            200: ListarPeticionesSerializer(many=True),
            400: 'Parámetros de filtro inválidos.',
            401: 'No autenticado.',
            403: 'Acceso denegado (Exclusivo para Super Administrador).',
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class SuperAdminReporteAtendidasView(generics.ListAPIView):
    """
    GET /api/super-administrador/reportes/atendidas/
    Consulta todos los casos atendidos del sistema con soporte de filtros por
    fecha (fecha, fecha_desde, fecha_hasta), estado (estado, id_estado)
    y veterinario asignado (veterinario, asignado_a, id_veterinario).
    """
    serializer_class = ListarPeticionesSerializer
    permission_classes = [EsSuperAdministrador]

    def get_queryset(self):
        queryset = (
            Peticiones.objects.select_related(
                'id_tipo',
                'id_estado',
                'id_ubicacion',
                'responsable',
                'asignado_a',
                'asignado_por',
            )
            .prefetch_related('evidenciapeticiones_set', 'historial_cambios')
            .filter(_obtener_filtro_casos_atendidos())
        )
        queryset = _aplicar_filtros_reportes_peticiones(
            queryset,
            getattr(self.request, 'query_params', {}),
        )
        return queryset.distinct().order_by('-fecha', '-id_peticion')

    @swagger_auto_schema(
        tags=['Super Administrador - Reportes'],
        operation_summary='Reporte de casos atendidos',
        operation_description=(
            'Consulta todas las peticiones que han sido atendidas (por estado de atención/resolución '
            'o con acta de visita clínica registrada). Soporta filtros por fecha, estado y veterinario asignado.'
        ),
        manual_parameters=PARAMETROS_SWAGGER_REPORTES,
        responses={
            200: ListarPeticionesSerializer(many=True),
            400: 'Parámetros de filtro inválidos.',
            401: 'No autenticado.',
            403: 'Acceso denegado (Exclusivo para Super Administrador).',
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class SuperAdminReporteEstadisticasView(APIView):
    """
    GET /api/super-administrador/reportes/estadisticas/
    Consulta las estadísticas generales del sistema (peticiones, casos atendidos,
    distribución por estado, tipo, prioridad, veterinarios, usuarios y sedes).
    Soporta los mismos filtros opcionales por fecha, estado y veterinario asignado.
    """
    permission_classes = [EsSuperAdministrador]

    @swagger_auto_schema(
        tags=['Super Administrador - Reportes'],
        operation_summary='Reporte de estadísticas generales',
        operation_description=(
            'Retorna estadísticas generales consolidadas del sistema: totales de peticiones, '
            'casos atendidos, pendientes, en proceso, finalizadas, distribución por estado y tipo, '
            'rendimiento por veterinario asignado, resumen de usuarios por rol y sedes. '
            'Soporta filtros por fecha, estado y veterinario asignado.'
        ),
        manual_parameters=PARAMETROS_SWAGGER_REPORTES,
        responses={
            200: openapi.Response('Estadísticas generales del sistema'),
            400: 'Parámetros de filtro inválidos.',
            401: 'No autenticado.',
            403: 'Acceso denegado (Exclusivo para Super Administrador).',
        },
    )
    def get(self, request, *args, **kwargs):
        query_params = getattr(request, 'query_params', {}) or {}
        peticiones_qs = _aplicar_filtros_reportes_peticiones(
            Peticiones.objects.all(),
            query_params,
        )

        total_peticiones = peticiones_qs.count()
        casos_atendidos = peticiones_qs.filter(_obtener_filtro_casos_atendidos()).distinct().count()

        # Distribución por estado
        estados_agrupados = (
            peticiones_qs.values('id_estado', 'id_estado__nombre')
            .annotate(total=Count('id_peticion'))
            .order_by('id_estado')
        )
        por_estado = []
        distribucion_estados = {}
        pendientes = 0
        en_proceso = 0
        finalizadas = 0

        for item in estados_agrupados:
            nombre_est = item['id_estado__nombre'] or 'Sin estado'
            cant = item['total']
            por_estado.append({
                'id_estado': item['id_estado'],
                'estado': nombre_est,
                'total': cant,
            })
            distribucion_estados[nombre_est] = cant

            est_norm = _normalizar_sin_acentos(nombre_est)
            if any(t in est_norm for t in ('atendid', 'finaliz', 'resuelt', 'alta', 'cerrad')):
                finalizadas += cant
            elif any(t in est_norm for t in ('proceso', 'tratam', 'observac', 'curso', 'atenci')):
                en_proceso += cant
            else:
                pendientes += cant

        # Distribución por tipo de petición
        tipos_agrupados = (
            peticiones_qs.values('id_tipo', 'id_tipo__nombre')
            .annotate(total=Count('id_peticion'))
            .order_by('id_tipo')
        )
        por_tipo = [
            {
                'id_tipo': item['id_tipo'],
                'tipo': (item['id_tipo__nombre'] or 'Sin tipo').strip(),
                'total': item['total'],
            }
            for item in tipos_agrupados
        ]

        # Distribución por prioridad
        prioridades_agrupadas = (
            peticiones_qs.values('prioridad')
            .annotate(total=Count('id_peticion'))
            .order_by('prioridad')
        )
        por_prioridad = {
            (item['prioridad'] or 'Sin definir'): item['total']
            for item in prioridades_agrupadas
        }

        urgentes = peticiones_qs.filter(
            Q(prioridad__iexact='alta')
            | Q(prioridad__iexact='urgente')
            | Q(prioridad__icontains='urg')
            | Q(prioridad__icontains='alt')
        ).count()

        sin_asignar = peticiones_qs.filter(asignado_a__isnull=True).count()
        con_veterinario = total_peticiones - sin_asignar
        porcentaje_atencion = round((casos_atendidos / total_peticiones) * 100, 2) if total_peticiones > 0 else 0.0

        # Estadísticas por veterinario asignado
        vets_agrupados = (
            peticiones_qs.exclude(asignado_a__isnull=True)
            .values(
                'asignado_a_id',
                'asignado_a__nombre',
                'asignado_a__apellido',
                'asignado_a__email',
            )
            .annotate(
                total_asignadas=Count('id_peticion', distinct=True),
                casos_atendidos=Count(
                    'id_peticion',
                    filter=_obtener_filtro_casos_atendidos(),
                    distinct=True,
                ),
            )
            .order_by('-total_asignadas', 'asignado_a__nombre')
        )
        por_veterinario = []
        for v in vets_agrupados:
            tot_v = v['total_asignadas']
            atend_v = v['casos_atendidos']
            nombre_v = f"{v['asignado_a__nombre'] or ''} {v['asignado_a__apellido'] or ''}".strip()
            por_veterinario.append({
                'id_veterinario': v['asignado_a_id'],
                'nombre': v['asignado_a__nombre'],
                'apellido': v['asignado_a__apellido'],
                'nombre_completo': nombre_v,
                'email': v['asignado_a__email'],
                'total_asignadas': tot_v,
                'casos_atendidos': atend_v,
                'pendientes_atencion': max(tot_v - atend_v, 0),
                'porcentaje_atencion': round((atend_v / tot_v) * 100, 2) if tot_v > 0 else 0.0,
            })

        # Resumen de usuarios y roles
        total_usuarios = Usuarios.objects.count()
        usuarios_activos = Usuarios.objects.filter(activo=True).count()
        usuarios_inactivos = total_usuarios - usuarios_activos
        usuarios_por_rol = {
            'administrador': Usuarios.objects.filter(id_rol_id=ROL_ADMINISTRADOR).count(),
            'juridico': Usuarios.objects.filter(id_rol_id=ROL_JURIDICO).count(),
            'veterinario': Usuarios.objects.filter(id_rol_id=ROL_VETERINARIO).count(),
            'peticionario': Usuarios.objects.filter(id_rol_id=ROL_PETICIONARIO).count(),
            'super_administrador': Usuarios.objects.filter(id_rol_id=ROL_SUPER_ADMINISTRADOR).count(),
        }

        # Resumen de sedes
        total_sedes = Sede.objects.count()
        sedes_activas = Sede.objects.filter(activo=True).count()
        sedes_inactivas = total_sedes - sedes_activas

        total_seguimientos = SeguimientoPeticionesVisita.objects.filter(
            id_peticion__in=peticiones_qs
        ).count()

        filtros_aplicados = {
            'fecha': query_params.get('fecha'),
            'fecha_desde': query_params.get('fecha_desde') or query_params.get('fecha_inicio'),
            'fecha_hasta': query_params.get('fecha_hasta') or query_params.get('fecha_fin'),
            'estado': query_params.get('estado'),
            'id_estado': query_params.get('id_estado'),
            'veterinario': (
                query_params.get('veterinario')
                or query_params.get('asignado_a')
                or query_params.get('id_veterinario')
                or query_params.get('veterinario_asignado')
            ),
        }

        return Response(
            {
                'total_peticiones': total_peticiones,
                'casos_atendidos': casos_atendidos,
                'pendientes': pendientes,
                'en_proceso': en_proceso,
                'finalizadas': finalizadas,
                'urgentes': urgentes,
                'sin_asignar': sin_asignar,
                'con_veterinario_asignado': con_veterinario,
                'porcentaje_atencion': porcentaje_atencion,
                'total_seguimientos_visita': total_seguimientos,
                'resumen_peticiones': {
                    'total_peticiones': total_peticiones,
                    'casos_atendidos': casos_atendidos,
                    'pendientes': pendientes,
                    'en_proceso': en_proceso,
                    'finalizadas': finalizadas,
                    'urgentes': urgentes,
                    'sin_asignar': sin_asignar,
                    'con_veterinario_asignado': con_veterinario,
                    'porcentaje_atencion': porcentaje_atencion,
                },
                'por_estado': por_estado,
                'distribucion_estados': distribucion_estados,
                'por_tipo': por_tipo,
                'por_prioridad': por_prioridad,
                'por_veterinario': por_veterinario,
                'usuarios': {
                    'total_usuarios': total_usuarios,
                    'activos': usuarios_activos,
                    'inactivos': usuarios_inactivos,
                    'por_rol': usuarios_por_rol,
                },
                'sedes': {
                    'total_sedes': total_sedes,
                    'activas': sedes_activas,
                    'inactivas': sedes_inactivas,
                },
                'filtros_aplicados': filtros_aplicados,
            },
            status=status.HTTP_200_OK,
        )

