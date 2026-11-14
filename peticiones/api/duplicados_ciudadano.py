"""
Serializer, throttles y vista del endpoint ciudadano de detección de duplicados.
"""

import math

from rest_framework import generics, serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from peticiones.services.duplicados_ciudadano_service import detectar_duplicados_ciudadano
from users.api.permissions import EsPeticionario


class _DescripcionCiudadanoField(serializers.CharField):
    """Cadena obligatoria recortada antes de validar longitud."""

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail('invalid')
        return super().to_internal_value(data)


class _CoordenadaFinitaField(serializers.FloatField):
    """Coordenada numérica finita obligatoria (rechaza booleanos, NaN e infinitos)."""

    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail('invalid')
        valor = super().to_internal_value(data)
        if not math.isfinite(valor):
            self.fail('invalid')
        return valor


class DetectarDuplicadosCiudadanoSerializer(serializers.Serializer):
    """
    Serializer de entrada para POST /api/peticiones/detectar-duplicados-ciudadano/.
    Solo admite `descripcion`, `latitud` y `longitud`; cualquier otro campo es ignorado.
    """

    descripcion = _DescripcionCiudadanoField(
        required=True,
        allow_null=False,
        allow_blank=False,
        trim_whitespace=True,
        min_length=10,
        max_length=1000,
    )
    latitud = _CoordenadaFinitaField(
        required=True,
        allow_null=False,
        min_value=-90.0,
        max_value=90.0,
    )
    longitud = _CoordenadaFinitaField(
        required=True,
        allow_null=False,
        min_value=-180.0,
        max_value=180.0,
    )


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

        resultado = detectar_duplicados_ciudadano(
            descripcion=datos['descripcion'],
            latitud=datos['latitud'],
            longitud=datos['longitud'],
        )
        return Response(resultado, status=status.HTTP_200_OK)
