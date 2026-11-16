"""
Serializer, throttles y vista del endpoint ciudadano de detección de duplicados.
"""

import math

from rest_framework import generics, serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from peticiones.services.dinov2_service import es_dinov2_activo
from peticiones.services.duplicados_ciudadano_service import detectar_duplicados_ciudadano
from users.api.permissions import EsPeticionario


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

        if es_dinov2_activo():
            resultado = detectar_duplicados_ciudadano(
                descripcion=datos.get('descripcion', ''),
                latitud=datos.get('latitud'),
                longitud=datos.get('longitud'),
                foto=datos.get('foto'),
                fotos=datos.get('fotos'),
            )
        else:
            resultado = detectar_duplicados_ciudadano(
                descripcion=datos['descripcion'],
                latitud=datos['latitud'],
                longitud=datos['longitud'],
            )
        return Response(resultado, status=status.HTTP_200_OK)
