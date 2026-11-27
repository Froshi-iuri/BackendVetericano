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
    TriajeDuplicadosInputSerializer,
    TriajePeticionSerializer,
    TriajeResultadoInputSerializer,
)
from triaje_ia.services import (
    calcular_carga_veterinarios,
    construir_payload_peticion_triaje,
    ejecutar_deteccion_duplicados_triaje,
    encolar_y_despachar_triaje_peticion,
    procesar_reintentos_pendientes,
    registrar_resultado_triaje,
)


class _VistaInternaTriajeBase(APIView):
    authentication_classes = [TriajeServiceTokenAuthentication]
    permission_classes = [IsAuthenticated, EsServicioTriaje]


class TriajeInternoContextoPeticionView(_VistaInternaTriajeBase):
    """
    GET /api/triaje-ia/interno/peticiones/<id_peticion>/
    Retorna el contexto completo de la petición (descripción, fotos Cloudinary y GPS)
    para el workflow de triaje en n8n.
    """

    def get(self, request, id_peticion):
        peticion = (
            Peticiones.objects.select_related('id_tipo', 'id_estado', 'id_ubicacion')
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
    GET /api/triaje-ia/interno/veterinarios/carga/
    Consulta la carga laboral actual de veterinarios activos y sugiere el candidato
    con menor carga activa.
    """

    def get(self, request):
        return Response(calcular_carga_veterinarios(), status=status.HTTP_200_OK)


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
        return Response(TriajePeticionSerializer(triaje).data, status=status.HTTP_200_OK)


class TriajeInternoReintentarPendientesView(_VistaInternaTriajeBase):
    """
    POST /api/triaje-ia/interno/reintentar-pendientes/
    Procesa los envíos pendientes o con fallo temporal vencido hacia n8n.
    """

    def post(self, request):
        limite = request.data.get('limite', 20) if isinstance(request.data, dict) else 20
        try:
            limite_int = int(limite)
        except (TypeError, ValueError):
            limite_int = 20
        return Response(procesar_reintentos_pendientes(limite=limite_int), status=status.HTTP_200_OK)


class TriajeDetallePeticionView(APIView):
    """
    GET /api/triaje-ia/peticiones/<id_peticion>/
    Consulta el resultado del triaje inteligente para Administrador o Jurídico.
    """

    permission_classes = [IsAuthenticated, EsJuridicoOAdminTriaje]

    def get(self, request, id_peticion):
        triaje = (
            TriajePeticion.objects.select_related('peticion', 'veterinario_recomendado')
            .filter(peticion_id=id_peticion)
            .first()
        )
        if not triaje:
            return Response(
                {'error': f'No existe registro de triaje para la petición #{id_peticion}.'},
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
        if not Peticiones.objects.filter(pk=id_peticion).exists():
            return Response(
                {'error': f'La petición #{id_peticion} no fue encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        forzar = bool(request.data.get('forzar', False)) if isinstance(request.data, dict) else False
        resultado = encolar_y_despachar_triaje_peticion(
            id_peticion,
            forzar_reenvio=forzar,
        )
        return Response(resultado, status=status.HTTP_200_OK)
