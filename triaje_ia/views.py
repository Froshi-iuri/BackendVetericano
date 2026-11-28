from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from peticiones.models import Peticiones
from peticiones.services.duplicados_service import PeticionNoEncontradaError
from triaje_ia.authentication import TriajeServiceTokenAuthentication
from triaje_ia.models import TriajePeticion
from triaje_ia.permissions import EsJuridicoOAdminTriaje, EsServicioTriaje
from triaje_ia.serializers import (
    TriajeDecisionInputSerializer,
    TriajeDuplicadosInputSerializer,
    TriajePeticionSerializer,
    TriajeResultadoInputSerializer,
)
from triaje_ia.services import (
    StaleTriajeCallbackError,
    TriajeDecisionInvalidaError,
    TriajeNoEncontradoError,
    calcular_carga_veterinarios,
    construir_payload_peticion_triaje,
    ejecutar_deteccion_duplicados_triaje,
    encolar_y_despachar_triaje_peticion,
    obtener_foto_sanitizada_para_triaje,
    recuperar_trabajos_interrumpidos,
    registrar_decision_humana_triaje,
    registrar_resultado_triaje,
)


class _VistaInternaTriajeBase(APIView):
    authentication_classes = [TriajeServiceTokenAuthentication]
    permission_classes = [IsAuthenticated, EsServicioTriaje]


def _parse_int_opcional(valor):
    if valor is None or not str(valor).strip().isdigit():
        return None
    num = int(str(valor).strip())
    return num if num > 0 else None


class TriajeInternoContextoPeticionView(_VistaInternaTriajeBase):
    """
    GET /api/triaje-ia/interno/peticiones/<id_peticion>/
    Retorna el contexto minimizado y sanitizado de la petición para el workflow de n8n.
    """

    def get(self, request, id_peticion):
        peticion = (
            Peticiones.objects.select_related('id_tipo', 'id_estado', 'id_ubicacion', 'responsable')
            .prefetch_related('evidenciapeticiones_set')
            .filter(pk=id_peticion)
            .first()
        )
        if not peticion:
            return Response(
                {'error': f'La petición #{id_peticion} no fue encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(construir_payload_peticion_triaje(peticion), status=status.HTTP_200_OK)


class TriajeInternoFotoSeguraView(_VistaInternaTriajeBase):
    """
    GET /api/triaje-ia/interno/peticiones/<id_peticion>/foto-segura/
    Entrega la fotografía principal re-codificada en JPEG limpio sin metadatos EXIF/GPS/XMP
    ni perfiles de cámara antes de ser enviada a n8n y Gemini Vision.
    """

    def get(self, request, id_peticion):
        try:
            jpeg_bytes = obtener_foto_sanitizada_para_triaje(id_peticion)
        except PermissionError as exc:
            return Response(
                {'error': str(exc), 'codigo': 'FOTOS_IA_DESACTIVADAS'},
                status=status.HTTP_403_FORBIDDEN,
            )
        except (PeticionNoEncontradaError, TriajeNoEncontradoError) as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )
        response = HttpResponse(jpeg_bytes, content_type='image/jpeg')
        response['Cache-Control'] = 'no-store'
        return response


class TriajeInternoDuplicadosView(_VistaInternaTriajeBase):
    """
    POST /api/triaje-ia/interno/peticiones/<id_peticion>/duplicados/
    Reutiliza la detección de duplicados existente (texto pg_trgm, GPS y DINOv2/dHash).
    """

    def post(self, request, id_peticion):
        serializer = TriajeDuplicadosInputSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data
        try:
            resultado = ejecutar_deteccion_duplicados_triaje(
                id_peticion,
                usar_dinov2=datos['usar_dinov2'],
                registrar_alertas=datos['registrar_alertas'],
            )
        except PeticionNoEncontradaError:
            return Response(
                {'error': f'La petición #{id_peticion} no fue encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(resultado, status=status.HTTP_200_OK)


class TriajeInternoCargaVeterinariosView(_VistaInternaTriajeBase):
    """
    GET /api/triaje-ia/interno/veterinarios/carga/?id_peticion=<id>&id_sede=<id>&solo_sede=true|false
    Consulta la carga laboral actual de veterinarios activos (con sede activa) y sugiere
    el candidato elegible con menor carga activa respetando reglas de asignación.
    """

    def get(self, request):
        id_peticion = _parse_int_opcional(request.query_params.get('id_peticion'))
        id_sede = _parse_int_opcional(request.query_params.get('id_sede'))
        solo_sede = str(request.query_params.get('solo_sede', '')).strip().lower() in ('true', '1', 'si')
        try:
            data = calcular_carga_veterinarios(
                id_peticion=id_peticion,
                id_sede=id_sede,
                solo_sede=solo_sede,
            )
        except PeticionNoEncontradaError:
            return Response(
                {'error': f'La petición #{id_peticion} no fue encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(data, status=status.HTTP_200_OK)


class TriajeInternoGuardarResultadoView(_VistaInternaTriajeBase):
    """
    POST /api/triaje-ia/interno/peticiones/<id_peticion>/resultado/
    Guarda de forma idempotente la prioridad sugerida, justificación, duplicados y
    veterinario recomendado sin modificar Peticiones.prioridad ni asignado_a.
    """

    def post(self, request, id_peticion):
        serializer = TriajeResultadoInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            triaje = registrar_resultado_triaje(id_peticion, serializer.validated_data)
        except PeticionNoEncontradaError:
            return Response(
                {'error': f'La petición #{id_peticion} no fue encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except StaleTriajeCallbackError as exc:
            return Response(
                {'error': str(exc), 'callback_obsoleto': True},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(TriajePeticionSerializer(triaje).data, status=status.HTTP_200_OK)


class TriajeInternoReintentarPendientesView(_VistaInternaTriajeBase):
    """
    POST /api/triaje-ia/interno/reintentar-pendientes/
    Recupera trabajos interrumpidos (lease/análisis expirado) y procesa envíos pendientes.
    """

    def post(self, request):
        limite = request.data.get('limite', 20) if isinstance(request.data, dict) else 20
        try:
            limite_int = int(limite)
        except (TypeError, ValueError):
            limite_int = 20
        return Response(
            recuperar_trabajos_interrumpidos(limite=limite_int),
            status=status.HTTP_200_OK,
        )


class TriajeDetallePeticionView(APIView):
    """
    GET /api/triaje-ia/peticiones/<id_peticion>/
    Consulta el resultado del triaje inteligente para Administrador o Jurídico.
    """

    permission_classes = [IsAuthenticated, EsJuridicoOAdminTriaje]

    def get(self, request, id_peticion):
        automatizacion_activa = bool(getattr(settings, 'TRIAJE_N8N_ACTIVO', False))
        peticion = Peticiones.objects.select_related('responsable').filter(pk=id_peticion).first()
        if not peticion:
            return Response(
                {
                    'error': f'La petición #{id_peticion} no fue encontrada.',
                    'existe_peticion': False,
                    'existe_triaje': False,
                    'automatizacion_activa': automatizacion_activa,
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        self.check_object_permissions(request, peticion)

        triaje = (
            TriajePeticion.objects.select_related(
                'peticion',
                'peticion__id_estado',
                'peticion__responsable',
                'peticion__asignado_a',
                'veterinario_recomendado',
                'veterinario_recomendado__id_sede',
                'decision_prioridad_por',
                'decision_veterinario_por',
            )
            .prefetch_related('peticion__evidenciapeticiones_set')
            .filter(peticion_id=id_peticion)
            .first()
        )
        if not triaje:
            return Response(
                {
                    'error': f'No existe registro de triaje para la petición #{id_peticion}.',
                    'id_peticion': peticion.id_peticion,
                    'existe_peticion': True,
                    'existe_triaje': False,
                    'automatizacion_activa': automatizacion_activa,
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(TriajePeticionSerializer(triaje).data, status=status.HTTP_200_OK)


class TriajeReintentarPeticionView(APIView):
    """
    POST /api/triaje-ia/peticiones/<id_peticion>/reintentar/
    Permite a Administrador o Jurídico reencolar manualmente el análisis de triaje en n8n.
    """

    permission_classes = [IsAuthenticated, EsJuridicoOAdminTriaje]

    def post(self, request, id_peticion):
        peticion = Peticiones.objects.select_related('responsable').filter(pk=id_peticion).first()
        if not peticion:
            return Response(
                {'error': f'La petición #{id_peticion} no fue encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        self.check_object_permissions(request, peticion)
        automatizacion_activa = bool(getattr(settings, 'TRIAJE_N8N_ACTIVO', False))
        if not automatizacion_activa:
            return Response(
                {
                    'ejecutado': False,
                    'motivo': 'DESACTIVADO',
                    'automatizacion_activa': False,
                },
                status=status.HTTP_200_OK,
            )
        forzar = bool(request.data.get('forzar', False)) if isinstance(request.data, dict) else False
        resultado = encolar_y_despachar_triaje_peticion(
            id_peticion,
            forzar_reenvio=forzar,
        )
        resultado['automatizacion_activa'] = automatizacion_activa
        return Response(resultado, status=status.HTTP_200_OK)


class TriajeDecisionPeticionView(APIView):
    """
    POST /api/triaje-ia/peticiones/<id_peticion>/decision/
    Permite a Administrador o Jurídico aceptar o rechazar recomendaciones del triaje
    mediante los flujos autorizados existentes y manteniendo auditoría humana.
    """

    permission_classes = [IsAuthenticated, EsJuridicoOAdminTriaje]

    def post(self, request, id_peticion):
        peticion = Peticiones.objects.select_related('responsable').filter(pk=id_peticion).first()
        if not peticion:
            return Response(
                {'error': f'La petición #{id_peticion} no fue encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        self.check_object_permissions(request, peticion)

        serializer = TriajeDecisionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            triaje, cambiada = registrar_decision_humana_triaje(
                id_peticion,
                request.user,
                serializer.validated_data,
            )
        except TriajeNoEncontradoError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except TriajeDecisionInvalidaError as exc:
            return Response(
                {'error': str(exc), 'codigo': exc.codigo},
                status=exc.status_code,
            )

        return Response(
            {
                'mensaje': (
                    'Decisión humana registrada correctamente.'
                    if cambiada
                    else 'La decisión humana ya se encontraba registrada previamente.'
                ),
                'cambiada': cambiada,
                'triaje': TriajePeticionSerializer(triaje).data,
            },
            status=status.HTTP_200_OK,
        )
