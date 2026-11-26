# AnaC
# Recuperación de contraseña. La lógica (códigos de un solo uso, límites de intentos y de
# solicitudes, almacenamiento compartido entre workers) vive en users/services/recuperacion_service.py.
# Contrato con Angular y Android: mismas URLs y mismos campos de entrada; respuestas con
# {'mensaje': ...} en éxito y {'error': ...} en error (400, 429 o 503).

from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .services import recuperacion_service


def _datos(request):
    datos = request.data
    return datos if hasattr(datos, 'get') else {}


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def solicitar_recuperacion(request):
    resultado = recuperacion_service.solicitar_codigo(
        _datos(request).get('email'),
        ip=recuperacion_service.ip_cliente(request),
    )
    return Response(resultado.cuerpo, status=resultado.estado_http)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def confirmar_recuperacion(request):
    datos = _datos(request)
    resultado = recuperacion_service.confirmar_codigo(
        datos.get('email'),
        datos.get('codigo'),
        datos.get('nueva_password'),
        ip=recuperacion_service.ip_cliente(request),
    )
    return Response(resultado.cuerpo, status=resultado.estado_http)
