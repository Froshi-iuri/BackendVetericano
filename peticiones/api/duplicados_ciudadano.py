"""
Serializer, throttles y vistas del flujo ciudadano de detección de duplicados
y del proxy cifrado de miniaturas sanitizadas.
"""

import math
from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from peticiones.api.serializers import (
    ModerarFotoDuplicadoSerializer,
    RevocarFotoDuplicadoSerializer,
)
from peticiones.models import Peticiones
from peticiones.services.dinov2_service import es_dinov2_activo
from peticiones.services.duplicados_ciudadano_service import (
    detectar_duplicados_ciudadano,
    obtener_miniatura_sanitizada_desde_token,
)
from peticiones.services.foto_autorizada_service import (
    moderar_fotos_peticion,
    revocar_fotos_peticion,
)
from users.api.permissions import EsPeticionario, IsJuridicoOrAdmin


class _DescripcionCiudadanoField(serializers.CharField):
    """Cadena recortada que rechaza tipos no textuales antes de validar longitud."""

    def to_internal_value(self, data):
        if data is None and self.allow_null:
            return None
        if not isinstance(data, str):
            self.fail('invalid')
        return super().to_internal_value(data)


class _CoordenadaFinitaField(serializers.FloatField):
    """Coordenada numérica finita (rechaza booleanos, NaN e infinitos)."""

    def to_internal_value(self, data):
        if data is None and self.allow_null:
            return None
        if isinstance(data, bool):
            self.fail('invalid')
        valor = super().to_internal_value(data)
        if not math.isfinite(valor):
            self.fail('invalid')
        return valor


class DetectarDuplicadosCiudadanoSerializer(serializers.Serializer):
    """
    Serializer de entrada para POST /api/peticiones/detectar-duplicados-ciudadano/.
    Con DINOv2 inactivo mantiene descripción + GPS obligatorios. Con DINOv2 activo
    permite priorizar fotografía(s) Cloudinary y GPS, dejando `descripcion` opcional
    como dato complementario; parámetros de algoritmo siguen ignorándose.
    """

    descripcion = _DescripcionCiudadanoField(
        required=False,
        allow_null=True,
        allow_blank=True,
        trim_whitespace=True,
        max_length=1000,
    )
    latitud = _CoordenadaFinitaField(
        required=False,
        allow_null=True,
        min_value=-90.0,
        max_value=90.0,
    )
    longitud = _CoordenadaFinitaField(
        required=False,
        allow_null=True,
        min_value=-180.0,
        max_value=180.0,
    )
    foto = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=2048,
        trim_whitespace=True,
    )
    fotos = serializers.ListField(
        child=serializers.CharField(allow_blank=True, max_length=2048, trim_whitespace=True),
        required=False,
        allow_null=True,
        allow_empty=True,
        max_length=3,
    )

    def validate(self, attrs):
        dinov2_activo = es_dinov2_activo()
        descripcion = attrs.get('descripcion')
        latitud = attrs.get('latitud')
        longitud = attrs.get('longitud')

        if (latitud is None) != (longitud is None):
            raise serializers.ValidationError(
                "Debe enviar tanto 'latitud' como 'longitud' para usar geolocalización."
            )

        if not dinov2_activo:
            errores = {}
            if descripcion is None or not str(descripcion).strip():
                errores['descripcion'] = 'Este campo es requerido.'
            elif len(str(descripcion).strip()) < 10:
                errores['descripcion'] = 'Asegúrese de que este campo tenga al menos 10 caracteres.'
            if latitud is None:
                errores['latitud'] = 'Este campo es requerido.'
            if longitud is None:
                errores['longitud'] = 'Este campo es requerido.'
            if errores:
                raise serializers.ValidationError(errores)
            attrs['descripcion'] = str(descripcion).strip()
            return attrs

        tiene_gps = latitud is not None and longitud is not None
        tiene_foto = bool((attrs.get('foto') or '').strip()) or any(
            str(u or '').strip() for u in (attrs.get('fotos') or ())
        )
        tiene_desc = bool(descripcion and len(str(descripcion).strip()) >= 10)
        if not tiene_gps and not tiene_foto and not tiene_desc:
            raise serializers.ValidationError(
                "Debe proporcionar fotografías ('foto'/'fotos'), coordenadas GPS ('latitud'/'longitud') o una 'descripcion' de al menos 10 caracteres."
            )
        attrs['descripcion'] = str(descripcion).strip() if isinstance(descripcion, str) else ''
        return attrs


class DuplicadosCiudadanoRafagaThrottle(UserRateThrottle):
    scope = 'duplicados_ciudadano_rafaga'


class DuplicadosCiudadanoSostenidoThrottle(UserRateThrottle):
    scope = 'duplicados_ciudadano_sostenido'


class MiniaturaCiudadanoRafagaThrottle(SimpleRateThrottle):
    scope = 'duplicados_ciudadano_miniatura'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class MiniaturaCiudadanoSostenidoThrottle(SimpleRateThrottle):
    scope = 'duplicados_ciudadano_miniatura_sostenido'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class ModerarFotoDuplicadoThrottle(UserRateThrottle):
    scope = 'duplicados_ciudadano_moderacion'


def _construir_url_proxy_desde_request(request, token: str) -> str:
    ruta = f"{reverse('miniatura-duplicado-ciudadano')}?t={quote(token, safe='')}"
    base_cfg = str(getattr(settings, 'DUPLICADOS_CIUDADANO_PUBLIC_BASE_URL', '') or '').strip().rstrip('/')
    if base_cfg:
        return f"{base_cfg}{ruta}"
    return request.build_absolute_uri(ruta)


class DetectarDuplicadosCiudadanoView(generics.GenericAPIView):
    """
    POST /api/peticiones/detectar-duplicados-ciudadano/
    Permite al Peticionario (rol 4) consultar posibles reportes similares antes
    de enviar una petición, con parámetros fijos en servidor y salida anonimizada.
    """

    serializer_class = DetectarDuplicadosCiudadanoSerializer
    permission_classes = [IsAuthenticated, EsPeticionario]
    throttle_classes = [
        DuplicadosCiudadanoRafagaThrottle,
        DuplicadosCiudadanoSostenidoThrottle,
    ]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data

        uid_consulta = getattr(request.user, 'id_usuario', None) or getattr(request.user, 'pk', None)
        builder = lambda token: _construir_url_proxy_desde_request(request, token)

        if es_dinov2_activo():
            resultado = detectar_duplicados_ciudadano(
                descripcion=datos.get('descripcion', ''),
                latitud=datos.get('latitud'),
                longitud=datos.get('longitud'),
                foto=datos.get('foto'),
                fotos=datos.get('fotos'),
                id_usuario_consulta=uid_consulta,
                miniatura_url_builder=builder,
            )
        else:
            resultado = detectar_duplicados_ciudadano(
                descripcion=datos['descripcion'],
                latitud=datos['latitud'],
                longitud=datos['longitud'],
                id_usuario_consulta=uid_consulta,
                miniatura_url_builder=builder,
            )
        return Response(resultado, status=status.HTTP_200_OK)


class MiniaturaDuplicadoCiudadanoView(APIView):
    """
    GET /api/peticiones/duplicados/miniatura/?t=<token_cifrado>
    Entrega una miniatura JPEG sanitizada (difuminada y sin EXIF/URLs originales)
    validando token temporal cifrado, usuario consultante activo y vigencia actual en BD.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [
        MiniaturaCiudadanoRafagaThrottle,
        MiniaturaCiudadanoSostenidoThrottle,
    ]

    def get(self, request, *args, **kwargs):
        token = request.query_params.get('t') or request.query_params.get('token')
        imagen_bytes = obtener_miniatura_sanitizada_desde_token(token)
        if not imagen_bytes:
            resp_404 = HttpResponse(status=status.HTTP_404_NOT_FOUND)
            resp_404['Cache-Control'] = 'private, no-store, max-age=0'
            resp_404['X-Content-Type-Options'] = 'nosniff'
            return resp_404

        resp = HttpResponse(imagen_bytes, content_type='image/jpeg', status=status.HTTP_200_OK)
        resp['Cache-Control'] = 'private, no-store, max-age=0'
        resp['Pragma'] = 'no-cache'
        resp['Expires'] = '0'
        resp['X-Content-Type-Options'] = 'nosniff'
        resp['Referrer-Policy'] = 'no-referrer'
        resp['Content-Disposition'] = 'inline; filename="miniatura.jpg"'
        return resp


class ModerarFotoDuplicadoView(APIView):
    """
    POST /api/peticiones/<id_peticion>/duplicados/moderar-foto/
    Permite a Administrador o Jurídico ocultar o habilitar miniaturas tras revisión.
    """

    permission_classes = [IsAuthenticated, IsJuridicoOrAdmin]
    throttle_classes = [ModerarFotoDuplicadoThrottle]

    def post(self, request, id_peticion, *args, **kwargs):
        peticion = Peticiones.objects.filter(pk=id_peticion).first()
        if not peticion:
            return Response(
                {'error': f'Petición #{id_peticion} no encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ModerarFotoDuplicadoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data

        try:
            resultado = moderar_fotos_peticion(
                peticion=peticion,
                usuario_actor=request.user,
                accion=datos['accion'],
                ruta_archivo=datos.get('ruta_archivo'),
                motivo=datos.get('motivo'),
            )
        except PermissionError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except RuntimeError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(resultado, status=status.HTTP_200_OK)


class RevocarFotoDuplicadoView(APIView):
    """
    POST /api/peticiones/<id_peticion>/duplicados/revocar-foto/
    Permite al ciudadano titular de la petición revocar la publicación de sus fotografías.
    """

    permission_classes = [IsAuthenticated, EsPeticionario]
    throttle_classes = [ModerarFotoDuplicadoThrottle]

    def post(self, request, id_peticion, *args, **kwargs):
        peticion = Peticiones.objects.filter(pk=id_peticion).first()
        if not peticion:
            return Response(
                {'error': f'Petición #{id_peticion} no encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = RevocarFotoDuplicadoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data

        try:
            resultado = revocar_fotos_peticion(
                peticion=peticion,
                usuario_actor=request.user,
                ruta_archivo=datos.get('ruta_archivo'),
                motivo=datos.get('motivo'),
            )
        except PermissionError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(resultado, status=status.HTTP_200_OK)
