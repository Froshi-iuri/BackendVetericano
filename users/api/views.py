import hmac
import math
import time
import unicodedata
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q, Count
from django.db.models.deletion import ProtectedError, RestrictedError
from django.utils.dateparse import parse_date
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import generics, mixins, serializers, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
try:
    from rest_framework_simplejwt.tokens import RefreshToken
except ImportError:
    RefreshToken = None
from .serializers import (
    RegisterCustomSerializer,
    ConfirmarCodigoRegistroSerializer,
    LoginCustomSerializer,
    GoogleLoginSerializer,
    UsuariosSerializer,
    RolSerializer,
    SedeSerializer,
    SedeEstadoSerializer,
    AdministradorSuperAdminSerializer,
)
from users.services.email_service import (
    construir_plantilla_otp_vetericano,
    enviar_correo_transaccional_brevo,
)
from users.views import (
    OTP_COOLDOWN_SEGUNDOS,
    OTP_MAX_INTENTOS_FALLIDOS,
    OTP_MAX_SOLICITUDES_HORA,
    OTP_TIMEOUT_SEGUNDOS,
    generar_codigo_otp,
)
try:
    from users.services.google_oauth_service import (
        GoogleAccountPolicyError,
        GoogleAuthConfigError,
        GoogleAuthUnavailableError,
        GoogleTokenInvalidError,
    )
except ImportError:
    class GoogleAccountPolicyError(Exception):
        pass
    class GoogleAuthConfigError(Exception):
        pass
    class GoogleAuthUnavailableError(Exception):
        pass
    class GoogleTokenInvalidError(Exception):
        pass
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
from vetericano.paginacion import PaginacionOpcional
from vetericano.ordenamiento import ordenar_peticiones

# Constantes de seguridad para protección anti-fuerza bruta en Login
LOGIN_MAX_INTENTOS_FALLIDOS = 6
LOGIN_BLOQUEO_SEGUNDOS = 900          # 15 minutos de bloqueo temporal tras 6 intentos fallidos
LOGIN_UMBRAL_ADVERTENCIA = 3          # A partir del 3er fallo se advierten los intentos restantes


class RegisterView(generics.GenericAPIView):

    # Esto es lo que le dice a Swagger:
    # "Usa este serializador para dibujar los campos".
    serializer_class = RegisterCustomSerializer
    authentication_classes = []
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


class SolicitarCodigoRegistroView(generics.GenericAPIView):
    """
    POST /api/usuarios/register/solicitar-codigo/
    Paso 1 del registro con verificación de correo:
    - Valida que todos los datos del usuario (email, identificación, contraseña, nombre, apellido, teléfono)
      sean válidos y que ni el correo ni la identificación estén registrados en PostgreSQL.
    - Guarda temporalmente el pre-registro con la contraseña ya encriptada en caché por 10 minutos
      junto con un código OTP criptográfico de 6 dígitos (sin ensuciar PostgreSQL con cuentas fantasma).
    - Envía el código al correo mediante Brevo API (HTTPS 443) con cooldown de 60s y límite de 3 envíos/hora.
    """
    serializer_class = RegisterCustomSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        datos = serializer.validated_data
        email_limpio = (datos.get('email') or '').strip()
        email_key = email_limpio.lower()
        ahora = int(time.time())

        # 1. Verificar cooldown de 60 segundos entre reenvíos al mismo correo
        cooldown_until = cache.get(f'registro_cooldown_{email_key}')
        if isinstance(cooldown_until, (int, float)) and cooldown_until > ahora:
            segundos_restantes = max(1, int(cooldown_until - ahora))
            return Response(
                {
                    'error': f'Por favor espera {segundos_restantes} segundos antes de solicitar un nuevo código.',
                    'detail': f'Por favor espera {segundos_restantes} segundos antes de solicitar un nuevo código.',
                    'retry_after': segundos_restantes,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # 2. Verificar límite de máximo 3 solicitudes de código por hora
        solicitudes_hora = int(cache.get(f'registro_rate_{email_key}') or 0)
        if solicitudes_hora >= OTP_MAX_SOLICITUDES_HORA:
            msg_limite = (
                f'Has alcanzado el límite máximo de {OTP_MAX_SOLICITUDES_HORA} envíos de código '
                'de verificación por hora. Intenta nuevamente más tarde.'
            )
            return Response(
                {'error': msg_limite, 'detail': msg_limite},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        codigo = generar_codigo_otp()
        nombre_usuario = (datos.get('nombre') or '').strip()
        apellido_usuario = (datos.get('apellido') or '').strip()
        identificacion_usuario = (datos.get('identificacion') or '').strip() or None
        telefono_usuario = (datos.get('telefono') or '').strip() or None

        pre_registro_payload = {
            'codigo': codigo,
            'intentos': 0,
            'creado_en': ahora,
            'usuario': {
                'email': email_limpio,
                'identificacion': identificacion_usuario,
                'password_hash': make_password(datos['password']),
                'nombre': nombre_usuario,
                'apellido': apellido_usuario,
                'telefono': telefono_usuario,
            },
        }
        cache.set(f'registro_pendiente_{email_key}', pre_registro_payload, timeout=OTP_TIMEOUT_SEGUNDOS)

        asunto = 'Código de verificación de correo - Vetericano'
        html_content = construir_plantilla_otp_vetericano(
            nombre=nombre_usuario or 'Usuario',
            codigo=codigo,
            proposito='registro',
        )

        try:
            resultado = enviar_correo_transaccional_brevo(
                destinatario_email=email_limpio,
                destinatario_nombre=f"{nombre_usuario} {apellido_usuario}".strip() or 'Usuario',
                asunto=asunto,
                contenido_html=html_content,
            )
            if resultado.get('exito'):
                cache.set(
                    f'registro_cooldown_{email_key}',
                    ahora + OTP_COOLDOWN_SEGUNDOS,
                    timeout=OTP_COOLDOWN_SEGUNDOS,
                )
                cache.set(
                    f'registro_rate_{email_key}',
                    solicitudes_hora + 1,
                    timeout=3600,
                )
                return Response(
                    {
                        'mensaje': 'Te enviamos un código de verificación de 6 dígitos a tu correo electrónico.',
                        'email': email_limpio,
                        'cooldown_segundos': OTP_COOLDOWN_SEGUNDOS,
                        'expira_en_segundos': OTP_TIMEOUT_SEGUNDOS,
                    },
                    status=status.HTTP_200_OK,
                )

            cache.delete(f'registro_pendiente_{email_key}')
            return Response(
                {
                    'error': 'No fue posible enviar el correo de verificación. Verifica que el correo exista.',
                    'detail': resultado.get('error') or 'Error al enviar correo con Brevo.',
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            cache.delete(f'registro_pendiente_{email_key}')
            return Response(
                {
                    'error': f'Error técnico al enviar el correo de verificación: {str(exc)}',
                    'detail': str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ConfirmarCodigoRegistroView(generics.GenericAPIView):
    """
    POST /api/usuarios/register/confirmar-codigo/
    Paso 2 del registro con verificación de correo:
    - Valida el código OTP de 6 dígitos enviado al correo (con límite de 5 intentos fallidos).
    - Si es válido, inserta atómicamente el usuario real en PostgreSQL con rol Peticionario y activo=True,
      y limpia el pre-registro de la caché.
    """
    serializer_class = ConfirmarCodigoRegistroSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email_limpio = (serializer.validated_data.get('email') or '').strip()
        email_key = email_limpio.lower()
        codigo_ingresado = (serializer.validated_data.get('codigo') or '').strip()

        pre_registro = cache.get(f'registro_pendiente_{email_key}')
        if not isinstance(pre_registro, dict) or 'usuario' not in pre_registro:
            msg = 'El código de verificación ha expirado o no existe un registro pendiente para este correo.'
            return Response({'error': msg, 'detail': msg}, status=status.HTTP_400_BAD_REQUEST)

        codigo_guardado = str(pre_registro.get('codigo') or '').strip()
        intentos_actuales = int(pre_registro.get('intentos') or 0)

        if not codigo_guardado or not hmac.compare_digest(codigo_guardado, codigo_ingresado):
            nuevos_intentos = intentos_actuales + 1
            if nuevos_intentos >= OTP_MAX_INTENTOS_FALLIDOS:
                cache.delete(f'registro_pendiente_{email_key}')
                msg_anulado = (
                    f'Has superado el límite de {OTP_MAX_INTENTOS_FALLIDOS} intentos fallidos. '
                    'El código fue anulado por seguridad; solicita uno nuevo.'
                )
                return Response(
                    {'error': msg_anulado, 'detail': msg_anulado, 'intentos_restantes': 0},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            restantes = OTP_MAX_INTENTOS_FALLIDOS - nuevos_intentos
            pre_registro['intentos'] = nuevos_intentos
            cache.set(f'registro_pendiente_{email_key}', pre_registro, timeout=OTP_TIMEOUT_SEGUNDOS)
            msg_invalido = f'El código de verificación es incorrecto. Te quedan {restantes} intento(s).'
            return Response(
                {'error': msg_invalido, 'detail': msg_invalido, 'intentos_restantes': restantes},
                status=status.HTTP_400_BAD_REQUEST,
            )

        datos_usuario = pre_registro['usuario']
        email_real = datos_usuario['email']
        identificacion_real = datos_usuario.get('identificacion')

        # Verificar que ni el correo ni la identificación hayan sido ocupados mientras se confirmaba el código
        if Usuarios.objects.filter(email__iexact=email_real).exists():
            cache.delete(f'registro_pendiente_{email_key}')
            msg_dup = 'Ya existe un usuario registrado con este correo electrónico.'
            return Response({'error': msg_dup, 'detail': msg_dup}, status=status.HTTP_400_BAD_REQUEST)

        if identificacion_real and Usuarios.objects.filter(identificacion__iexact=identificacion_real).exists():
            cache.delete(f'registro_pendiente_{email_key}')
            msg_id_dup = 'Ya existe un usuario registrado con este número de identificación.'
            return Response({'error': msg_id_dup, 'detail': msg_id_dup}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            rol_peticionario, _ = Rol.objects.get_or_create(nombre_rol='Peticionario')
            nuevo_usuario = Usuarios.objects.create(
                email=email_real,
                identificacion=identificacion_real,
                password=datos_usuario['password_hash'],
                nombre=datos_usuario.get('nombre') or '',
                apellido=datos_usuario.get('apellido') or '',
                telefono=datos_usuario.get('telefono'),
                id_rol=rol_peticionario,
                activo=True,
            )

        cache.delete(f'registro_pendiente_{email_key}')
        cache.delete(f'registro_cooldown_{email_key}')

        return Response(
            {
                'mensaje': 'Correo verificado y cuenta creada exitosamente.',
                'email': nuevo_usuario.email,
                'datos': {
                    'id_usuario': nuevo_usuario.id_usuario,
                    'email': nuevo_usuario.email,
                    'identificacion': nuevo_usuario.identificacion,
                    'nombre': nuevo_usuario.nombre,
                    'apellido': nuevo_usuario.apellido,
                    'telefono': nuevo_usuario.telefono,
                },
            },
            status=status.HTTP_201_CREATED,
        )


def _construir_respuesta_login(user, *, es_nuevo_usuario=None):
    """Genera el par JWT con claims de rol/foto y construye la respuesta esperada por web y Android."""
    refresh = RefreshToken.for_user(user)
    foto_actual = getattr(user, 'foto_perfil', None)
    refresh['id_rol'] = user.id_rol.id_rol
    refresh['nombre_rol'] = user.id_rol.nombre_rol
    refresh['foto_perfil'] = foto_actual or ''
    refresh['foto'] = foto_actual or ''

    nombre_completo = f"{(user.nombre or '').strip()} {(user.apellido or '').strip()}".strip()
    payload = {
        "mensaje": f"Bienvenido, {nombre_completo or user.email}",
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
        },
    }
    if es_nuevo_usuario is not None:
        payload["es_nuevo_usuario"] = es_nuevo_usuario
    return payload


class LoginView(generics.GenericAPIView):

    # Configura los campos 'email' y 'password' en Swagger.
    serializer_class = LoginCustomSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        email_raw = ''
        if isinstance(request.data, dict):
            email_raw = (request.data.get('email') or '').strip()
        email_key = email_raw.lower()
        ahora = int(time.time())

        # 1. Verificar si la cuenta tiene un bloqueo temporal activo por superar 6 intentos fallidos
        if email_key:
            lock_until = cache.get(f'login_lock_{email_key}')
            if isinstance(lock_until, (int, float)) and lock_until > ahora:
                segundos_restantes = max(1, int(lock_until - ahora))
                minutos_restantes = max(1, math.ceil(segundos_restantes / 60))
                mensaje_bloqueo = (
                    f"Cuenta bloqueada temporalmente por demasiados intentos fallidos. "
                    f"Intenta nuevamente en {minutos_restantes} minuto(s) o restablece tu contraseña."
                )
                return Response(
                    {
                        "detail": mensaje_bloqueo,
                        "error": mensaje_bloqueo,
                        "non_field_errors": [mensaje_bloqueo],
                        "retry_after": segundos_restantes,
                        "intentos_restantes": 0,
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            # Solo contabilizamos como intento fallido de contraseña cuando el error es "Credenciales incorrectas."
            errores = exc.detail if isinstance(exc.detail, dict) else {}
            non_field = errores.get('non_field_errors', [])
            texto_error = str(non_field[0]) if non_field else ''

            if email_key and 'Credenciales incorrectas' in texto_error:
                fallos = int(cache.get(f'login_fail_{email_key}') or 0) + 1
                if fallos >= LOGIN_MAX_INTENTOS_FALLIDOS:
                    cache.delete(f'login_fail_{email_key}')
                    cache.set(
                        f'login_lock_{email_key}',
                        ahora + LOGIN_BLOQUEO_SEGUNDOS,
                        timeout=LOGIN_BLOQUEO_SEGUNDOS,
                    )
                    minutos_bloqueo = LOGIN_BLOQUEO_SEGUNDOS // 60
                    mensaje_bloqueo = (
                        f"Has alcanzado el límite de {LOGIN_MAX_INTENTOS_FALLIDOS} intentos fallidos. "
                        f"Por seguridad, el acceso a esta cuenta se ha bloqueado por {minutos_bloqueo} minutos."
                    )
                    return Response(
                        {
                            "detail": mensaje_bloqueo,
                            "error": mensaje_bloqueo,
                            "non_field_errors": [mensaje_bloqueo],
                            "retry_after": LOGIN_BLOQUEO_SEGUNDOS,
                            "intentos_restantes": 0,
                        },
                        status=status.HTTP_429_TOO_MANY_REQUESTS,
                    )

                cache.set(f'login_fail_{email_key}', fallos, timeout=LOGIN_BLOQUEO_SEGUNDOS)
                restantes = LOGIN_MAX_INTENTOS_FALLIDOS - fallos
                if fallos >= LOGIN_UMBRAL_ADVERTENCIA:
                    mensaje_adv = (
                        f"Credenciales incorrectas. Te quedan {restantes} intento(s) "
                        "antes del bloqueo temporal de 15 minutos."
                    )
                    return Response(
                        {
                            "detail": mensaje_adv,
                            "error": mensaje_adv,
                            "non_field_errors": [mensaje_adv],
                            "intentos_restantes": restantes,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            raise

        # Login exitoso: limpiar cualquier contador de intentos fallidos previo
        if email_key:
            cache.delete(f'login_fail_{email_key}')
            cache.delete(f'login_lock_{email_key}')

        user = serializer.validated_data
        return Response(_construir_respuesta_login(user), status=status.HTTP_200_OK)


class GoogleLoginView(generics.GenericAPIView):
    """
    Autentica o registra ciudadanos (rol Peticionario) mediante un ID Token verificado de Google.
    """
    serializer_class = GoogleLoginSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except GoogleAuthConfigError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except GoogleAuthUnavailableError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except GoogleTokenInvalidError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        except GoogleAccountPolicyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        resultado = serializer.validated_data
        return Response(
            _construir_respuesta_login(
                resultado['user'],
                es_nuevo_usuario=resultado['es_nuevo_usuario'],
            ),
            status=status.HTTP_200_OK,
        )


class UsuariosViewSet(viewsets.ModelViewSet):
    queryset = Usuarios.objects.all().order_by('nombre', 'apellido', 'id_usuario')
    serializer_class = UsuariosSerializer
    permission_classes = [EsAdministradorOReadOnly]
    pagination_class = PaginacionOpcional

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
    queryset = Rol.objects.all().order_by('nombre_rol', 'id_rol')
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
    pagination_class = PaginacionOpcional
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        queryset = Sede.objects.annotate(
            total_usuarios_count=Count('usuarios', distinct=True)
        ).order_by('nombre', 'id_sede')

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
        .order_by('nombre', 'apellido', 'id_usuario')
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
    pagination_class = PaginacionOpcional

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
    pagination_class = PaginacionOpcional

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
        return ordenar_peticiones(queryset)

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
    pagination_class = PaginacionOpcional

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

