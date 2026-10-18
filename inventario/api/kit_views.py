"""
Vistas API REST para el Módulo de Kit de Inventario.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.

Expone los endpoints del kit de dos niveles bajo /api/inventario/kit/:
- GET mi-kit/
- GET veterinario/{id}/
- POST asignar/
- POST retiro-directo/
- GET/POST solicitudes/
- GET solicitudes/{id}/
- POST solicitudes/{id}/aprobar/
- POST solicitudes/{id}/rechazar/
- POST solicitudes/{id}/cancelar/
- POST sincronizar-offline/
"""

from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from users.models import Usuarios
from inventario.models import SolicitudesKit, KitVeterinarioLotes
from inventario.constants import (
    ROL_VETERINARIO,
    SOLICITUD_TIPO_REPOSICION,
    KARDEX_TIPOS_PERMITIDOS,
    KARDEX_UBICACIONES_PERMITIDAS,
)
from inventario.api.permissions import (
    EsGestorInventario,
    EsVeterinario,
    EsGestorOVeterinario,
    _es_gestor,
    _es_veterinario,
)
from inventario.services.exceptions import (
    InventarioDomainError,
    SolicitudNoEncontradaError,
    PermisoGestorRequeridoError,
    SolicitudPermisoError,
    SolicitudEstadoInvalidaError,
    SolicitudDuplicadaError,
    KitCompletoError,
)
from inventario.services import (
    kit_service,
    solicitudes_service,
    consumo_service,
)
from inventario.services.kit_service import _obtener_fecha_hoy, _es_entero_valido
from .kit_serializers import (
    AsignarStockKitSerializer,
    RetiroDirectoKitSerializer,
    CrearSolicitudKitSerializer,
    AprobarSolicitudKitSerializer,
    RechazarSolicitudKitSerializer,
    SincronizarOfflineSerializer,
    SolicitudKitOutputSerializer,
    KardexOutputSerializer,
    ConflictoOutputSerializer,
)
from .kit_consultas import (
    consultar_snapshot_movil,
    consultar_alertas_kit,
    construir_queryset_kardex,
    construir_queryset_conflictos,
    validar_parametro_entero_positivo,
    validar_parametro_fecha_iso,
    validar_parametro_booleano,
    validar_parametro_choice,
    validar_rango_fechas,
)


# ============================================================================
# 1. TRADUCCIÓN DE ERRORES Y UTILIDADES COMPARTIDAS
# ============================================================================

def traducir_error_inventario(exc: InventarioDomainError) -> Response:
    """
    Traduce de forma centralizada cualquier excepción de dominio (InventarioDomainError)
    a su código HTTP REST correspondiente y cuerpo estructurado:
    - 404: SolicitudNoEncontradaError
    - 403: PermisoGestorRequeridoError, SolicitudPermisoError
    - 409: SolicitudEstadoInvalidaError, SolicitudDuplicadaError, KitCompletoError
    - 400: todo lo demás que herede de InventarioDomainError
    """
    status_code = status.HTTP_400_BAD_REQUEST
    if isinstance(exc, SolicitudNoEncontradaError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, (PermisoGestorRequeridoError, SolicitudPermisoError)):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, (SolicitudEstadoInvalidaError, SolicitudDuplicadaError, KitCompletoError)):
        status_code = status.HTTP_409_CONFLICT

    data = {
        "error": getattr(exc, 'codigo', 'ERROR_INVENTARIO'),
        "detalle": getattr(exc, 'message', str(exc)),
    }
    extra = getattr(exc, 'extra', None)
    if extra and isinstance(extra, dict):
        data = {**data, **extra}

    return Response(data, status=status_code)


class KitErrorHandlingMixin:
    """
    Mixin para capturar y traducir excepciones de dominio de inventario
    de manera declarativa en todas las vistas del kit.
    """
    def handle_exception(self, exc):
        if isinstance(exc, InventarioDomainError):
            return traducir_error_inventario(exc)
        return super().handle_exception(exc)


class KitStandardPagination(PageNumberPagination):
    """Paginación estándar para listados de solicitudes de kit."""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def consultar_resumen_kit_veterinario(id_veterinario: int) -> dict:
    """
    Función auxiliar de consulta para obtener el resumen del kit agrupado por medicamento.
    Lectura directa sin locks pessimistas, evitando N+1 mediante select_related.
    """
    hoy = _obtener_fecha_hoy()
    lotes_qs = KitVeterinarioLotes.objects.filter(
        id_veterinario=id_veterinario
    ).filter(
        Q(cantidad_actual__gt=0) | Q(cantidad_referencia__gt=0)
    ).select_related('id_medicamento').order_by('id_medicamento__nombre', 'id_kit_lote')

    meds_map = {}
    for lote in lotes_qs:
        med = lote.id_medicamento
        med_id = lote.id_medicamento_id
        if med_id not in meds_map:
            meds_map[med_id] = {
                'id_medicamento': med_id,
                'nombre': getattr(med, 'nombre', f'Medicamento #{med_id}'),
                'saldo_disponible': 0,
                'saldo_vencido': 0,
                'lotes': [],
            }

        es_vencido = bool(lote.fecha_vencimiento is not None and lote.fecha_vencimiento < hoy)
        cant = lote.cantidad_actual

        if es_vencido:
            meds_map[med_id]['saldo_vencido'] += cant
        else:
            meds_map[med_id]['saldo_disponible'] += cant

        meds_map[med_id]['lotes'].append({
            'id_kit_lote': lote.id_kit_lote,
            'id_inventario': lote.id_inventario_id,
            'cantidad_actual': lote.cantidad_actual,
            'cantidad_referencia': lote.cantidad_referencia,
            'fecha_vencimiento': lote.fecha_vencimiento.isoformat() if lote.fecha_vencimiento else None,
            'vencido': es_vencido,
        })

    items = list(meds_map.values())
    total_unidades = sum(item['saldo_disponible'] + item['saldo_vencido'] for item in items)

    return {
        'total_unidades': total_unidades,
        'items': items,
    }


# ============================================================================
# 2. VISTAS DEL KIT DE VETERINARIO
# ============================================================================

class KitMiKitView(KitErrorHandlingMixin, APIView):
    """
    GET /api/inventario/kit/mi-kit/
    Retorna el kit personal del veterinario autenticado agrupado por medicamento.
    """
    permission_classes = [EsVeterinario]

    def get(self, request):
        data = consultar_resumen_kit_veterinario(request.user.id_usuario)
        return Response(data, status=status.HTTP_200_OK)


class KitVeterinarioView(KitErrorHandlingMixin, APIView):
    """
    GET /api/inventario/kit/veterinario/{id}/
    Permite al gestor consultar el kit de cualquier veterinario.
    Retorna 404 si el usuario no existe, está inactivo o no es veterinario.
    """
    permission_classes = [EsGestorInventario]

    def get(self, request, id):
        if not _es_entero_valido(id, minimo=1):
            return Response(
                {"error": "ID_INVALIDO", "detalle": "El ID del veterinario debe ser un entero mayor a cero."},
                status=status.HTTP_400_BAD_REQUEST
            )

        vet = Usuarios.objects.filter(pk=id, activo=True, id_rol_id=ROL_VETERINARIO).first()
        if not vet:
            return Response(
                {"error": "VETERINARIO_NO_ENCONTRADO", "detalle": f"El veterinario #{id} no existe o no está activo."},
                status=status.HTTP_404_NOT_FOUND
            )

        data = consultar_resumen_kit_veterinario(id)
        return Response(data, status=status.HTTP_200_OK)


class KitAsignarStockView(KitErrorHandlingMixin, APIView):
    """
    POST /api/inventario/kit/asignar/
    Permite al gestor dotar stock desde bodega general hacia el kit del veterinario.
    """
    permission_classes = [EsGestorInventario]

    def post(self, request):
        serializer = AsignarStockKitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        val = serializer.validated_data

        resultado = kit_service.asignar_stock_a_kit(
            id_gestor=request.user.id_usuario,
            id_veterinario=val['id_veterinario'],
            items=val['items'],
            motivo=val['motivo'],
        )

        return Response({
            "mensaje": "Stock asignado exitosamente",
            "asignaciones": resultado,
        }, status=status.HTTP_201_CREATED)


class KitRetiroDirectoView(KitErrorHandlingMixin, APIView):
    """
    POST /api/inventario/kit/retiro-directo/
    Permite al gestor retirar stock de un medicamento del kit de un veterinario.
    """
    permission_classes = [EsGestorInventario]

    def post(self, request):
        serializer = RetiroDirectoKitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        val = serializer.validated_data

        resultado = kit_service.retirar_stock_de_kit(
            id_gestor=request.user.id_usuario,
            id_veterinario=val['id_veterinario'],
            id_medicamento=val['id_medicamento'],
            cantidad=val['cantidad'],
            motivo=val['motivo'],
        )

        return Response({
            "mensaje": "Retiro ejecutado exitosamente",
            "id_medicamento": resultado['id_medicamento'],
            "nombre_medicamento": resultado['nombre_medicamento'],
            "cantidad_total_retirada": resultado['cantidad_total_retirada'],
            "unidades_reintegradas": resultado['unidades_reintegradas_bodega'],
            "unidades_baja_vencimiento": resultado['unidades_baja_vencimiento'],
            "lotes_afectados": resultado['lotes_afectados'],
        }, status=status.HTTP_200_OK)


class SolicitudesKitListCreateView(KitErrorHandlingMixin, APIView):
    """
    GET /api/inventario/kit/solicitudes/
    POST /api/inventario/kit/solicitudes/

    - GET: Gestor ve todas las solicitudes; Veterinario SOLO las suyas. Paginada.
    - POST: Veterinario crea solicitud de reposición o devolución.
    """
    permission_classes = [EsGestorOVeterinario]

    def get(self, request):
        if _es_gestor(request.user):
            qs = SolicitudesKit.objects.all().select_related(
                'id_veterinario', 'id_gestor'
            ).prefetch_related(
                'detalles__id_medicamento'
            ).order_by('-fecha_solicitud')

            id_vet_param = request.query_params.get('id_veterinario')
            if id_vet_param:
                if id_vet_param.isdigit() and _es_entero_valido(int(id_vet_param), minimo=1):
                    qs = qs.filter(id_veterinario=int(id_vet_param))
        else:
            # El veterinario ve únicamente sus propias solicitudes
            qs = SolicitudesKit.objects.filter(
                id_veterinario=request.user.id_usuario
            ).select_related(
                'id_gestor'
            ).prefetch_related(
                'detalles__id_medicamento'
            ).order_by('-fecha_solicitud')

        estado_param = request.query_params.get('estado')
        if estado_param:
            qs = qs.filter(estado=estado_param.strip().upper())

        tipo_param = request.query_params.get('tipo')
        if tipo_param:
            qs = qs.filter(tipo_solicitud=tipo_param.strip().upper())

        paginator = KitStandardPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            serializer = SolicitudKitOutputSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = SolicitudKitOutputSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        if not _es_veterinario(request.user):
            return Response(
                {"error": "SOLICITUD_PERMISO_DENEGADO", "detalle": "Solo los veterinarios activos pueden crear solicitudes de kit."},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = CrearSolicitudKitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        val = serializer.validated_data

        if val['tipo_solicitud'] == SOLICITUD_TIPO_REPOSICION:
            solicitud_res = solicitudes_service.crear_solicitud_reposicion(
                id_veterinario=request.user.id_usuario,
                motivo=val['motivo'],
                items=val.get('items'),
                es_reposicion_total=val.get('es_reposicion_total', False),
            )
        else:
            solicitud_res = solicitudes_service.crear_solicitud_devolucion(
                id_veterinario=request.user.id_usuario,
                items=val['items'],
                motivo=val['motivo'],
            )

        return Response(solicitud_res, status=status.HTTP_201_CREATED)


class SolicitudKitDetailView(KitErrorHandlingMixin, APIView):
    """
    GET /api/inventario/kit/solicitudes/{id}/
    Consulta el detalle de una solicitud. Gestor ve cualquiera; Veterinario solo si es dueño.
    """
    permission_classes = [EsGestorOVeterinario]

    def get(self, request, pk):
        if not _es_entero_valido(pk, minimo=1):
            return Response(
                {"error": "ID_INVALIDO", "detalle": "id_solicitud debe ser un entero mayor a cero."},
                status=status.HTTP_400_BAD_REQUEST
            )

        solicitud = SolicitudesKit.objects.filter(pk=pk).select_related(
            'id_veterinario', 'id_gestor'
        ).prefetch_related(
            'detalles__id_medicamento'
        ).first()

        if not solicitud:
            return Response(
                {"error": "SOLICITUD_NO_ENCONTRADA", "detalle": f"La solicitud #{pk} no existe."},
                status=status.HTTP_404_NOT_FOUND
            )

        if _es_veterinario(request.user) and not _es_gestor(request.user):
            if solicitud.id_veterinario_id != request.user.id_usuario:
                return Response(
                    {"error": "SOLICITUD_PERMISO_DENEGADO", "detalle": "No tiene permiso para consultar esta solicitud."},
                    status=status.HTTP_403_FORBIDDEN
                )

        serializer = SolicitudKitOutputSerializer(solicitud)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SolicitudKitAprobarView(KitErrorHandlingMixin, APIView):
    """
    POST /api/inventario/kit/solicitudes/{id}/aprobar/
    Permite al gestor aprobar una solicitud (total o parcialmente).
    """
    permission_classes = [EsGestorInventario]

    def post(self, request, pk):
        if not _es_entero_valido(pk, minimo=1):
            return Response(
                {"error": "ID_INVALIDO", "detalle": "id_solicitud debe ser un entero mayor a cero."},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = AprobarSolicitudKitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        val = serializer.validated_data

        resultado = solicitudes_service.aprobar_solicitud(
            id_gestor=request.user.id_usuario,
            id_solicitud=pk,
            detalles_aprobados=val.get('aprobaciones'),
            observaciones_gestor=val.get('observaciones_gestor'),
        )

        return Response(resultado, status=status.HTTP_200_OK)


class SolicitudKitRechazarView(KitErrorHandlingMixin, APIView):
    """
    POST /api/inventario/kit/solicitudes/{id}/rechazar/
    Permite al gestor rechazar una solicitud pendiente.
    """
    permission_classes = [EsGestorInventario]

    def post(self, request, pk):
        if not _es_entero_valido(pk, minimo=1):
            return Response(
                {"error": "ID_INVALIDO", "detalle": "id_solicitud debe ser un entero mayor a cero."},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = RechazarSolicitudKitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        val = serializer.validated_data

        resultado = solicitudes_service.rechazar_solicitud(
            id_gestor=request.user.id_usuario,
            id_solicitud=pk,
            observaciones_gestor=val['observaciones_gestor'],
        )

        return Response(resultado, status=status.HTTP_200_OK)


class SolicitudKitCancelarView(KitErrorHandlingMixin, APIView):
    """
    POST /api/inventario/kit/solicitudes/{id}/cancelar/
    Permite al veterinario cancelar su propia solicitud pendiente.
    """
    permission_classes = [EsVeterinario]

    def post(self, request, pk):
        if not _es_entero_valido(pk, minimo=1):
            return Response(
                {"error": "ID_INVALIDO", "detalle": "id_solicitud debe ser un entero mayor a cero."},
                status=status.HTTP_400_BAD_REQUEST
            )

        resultado = solicitudes_service.cancelar_solicitud(
            id_veterinario=request.user.id_usuario,
            id_solicitud=pk,
        )

        return Response(resultado, status=status.HTTP_200_OK)


class KitSincronizarOfflineView(KitErrorHandlingMixin, APIView):
    """
    POST /api/inventario/kit/sincronizar-offline/
    Sincroniza un lote de consumos clínicos registrados offline por el veterinario.
    - 200 OK: Si todos los consumos se aplicaron exitosamente (conflictos vacíos).
    - 409 Conflict: Si al menos un consumo fue RECHAZADO_CONFLICTO por falta de stock.
    """
    permission_classes = [EsVeterinario]

    def post(self, request):
        serializer = SincronizarOfflineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        val = serializer.validated_data

        resultado = consumo_service.descontar_consumo_kit(
            id_veterinario=request.user.id_usuario,
            consumos=val['consumos'],
            origen_tipo='DIRECTO',
            origen_ids=None,
            es_offline=True,
        )

        conflictos = resultado.get('conflictos', [])
        aplicados = resultado.get('aplicados', [])

        if conflictos:
            return Response({
                "error": "CONFLICTO_STOCK_KIT",
                "aplicados": aplicados,
                "conflictos": conflictos,
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "aplicados": aplicados,
            "conflictos": [],
        }, status=status.HTTP_200_OK)


# ============================================================================
# 3. VISTAS DE CONSULTA, ALERTAS Y AUDITORÍA (TAREA 9)
# ============================================================================

class KitSnapshotMovilView(KitErrorHandlingMixin, APIView):
    """
    GET /api/inventario/kit/snapshot-movil/
    Provee el snapshot local del kit para sincronización y persistencia offline del veterinario.
    Parámetros opcionales:
    - id_medicamento: entero mayor a cero para filtrar por un fármaco específico.
    """
    permission_classes = [EsVeterinario]

    def get(self, request):
        id_med = validar_parametro_entero_positivo(
            request.query_params.get('id_medicamento'),
            'id_medicamento'
        )
        data = consultar_snapshot_movil(request.user.id_usuario, id_medicamento=id_med)
        return Response(data, status=status.HTTP_200_OK)


class KitAlertasView(KitErrorHandlingMixin, APIView):
    """
    GET /api/inventario/kit/alertas/
    Consulta alertas de stock (agotados, críticos, bajos) y próximos a vencer.
    - Veterinario: consulta exclusivamente su propio kit.
    - Gestor: consulta todos los kits o filtra por ?id_veterinario=.
    """
    permission_classes = [EsGestorOVeterinario]

    def get(self, request):
        es_gest = _es_gestor(request.user)
        id_vet_filtro = None

        if es_gest:
            id_vet_param = request.query_params.get('id_veterinario')
            if id_vet_param is not None:
                id_vet_filtro = validar_parametro_entero_positivo(id_vet_param, 'id_veterinario')
                vet = Usuarios.objects.filter(pk=id_vet_filtro, activo=True, id_rol_id=ROL_VETERINARIO).first()
                if not vet:
                    return Response(
                        {"error": "VETERINARIO_NO_ENCONTRADO", "detalle": f"El veterinario #{id_vet_filtro} no existe o no está activo."},
                        status=status.HTTP_404_NOT_FOUND
                    )

        data = consultar_alertas_kit(
            es_gestor=es_gest,
            id_veterinario_solicitante=request.user.id_usuario,
            id_veterinario_filtro=id_vet_filtro,
        )
        return Response(data, status=status.HTTP_200_OK)


class KitKardexView(KitErrorHandlingMixin, APIView):
    """
    GET /api/inventario/kit/kardex/
    Consulta cronológica inmutable de movimientos de kardex.
    - Paginada y ordenada fecha_hora DESC, id_kardex DESC.
    - Veterinario ve únicamente sus propios movimientos (id_veterinario forzado).
    - Gestor ve todos y puede filtrar por id_veterinario.
    - Filtros validados: id_medicamento, id_inventario, tipo_movimiento, ubicacion, desde, hasta.
    - Solo lectura (GET).
    """
    permission_classes = [EsGestorOVeterinario]

    def get(self, request):
        es_gest = _es_gestor(request.user)

        id_med = validar_parametro_entero_positivo(request.query_params.get('id_medicamento'), 'id_medicamento')
        id_inv = validar_parametro_entero_positivo(request.query_params.get('id_inventario'), 'id_inventario')
        id_vet = None
        if es_gest:
            id_vet = validar_parametro_entero_positivo(request.query_params.get('id_veterinario'), 'id_veterinario')

        tipo_mov = validar_parametro_choice(
            request.query_params.get('tipo_movimiento'),
            KARDEX_TIPOS_PERMITIDOS,
            'tipo_movimiento'
        )
        ubicacion = validar_parametro_choice(
            request.query_params.get('ubicacion'),
            KARDEX_UBICACIONES_PERMITIDAS,
            'ubicacion'
        )

        desde = validar_parametro_fecha_iso(request.query_params.get('desde'), 'desde')
        hasta = validar_parametro_fecha_iso(request.query_params.get('hasta'), 'hasta')
        validar_rango_fechas(desde, hasta)

        filtros = {
            'id_medicamento': id_med,
            'id_inventario': id_inv,
            'id_veterinario': id_vet,
            'tipo_movimiento': tipo_mov,
            'ubicacion': ubicacion,
            'desde': desde,
            'hasta': hasta,
        }

        qs = construir_queryset_kardex(
            es_gestor=es_gest,
            id_veterinario_solicitante=request.user.id_usuario,
            filtros=filtros,
        )

        paginator = KitStandardPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            serializer = KardexOutputSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = KardexOutputSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class KitConflictosView(KitErrorHandlingMixin, APIView):
    """
    GET /api/inventario/kit/conflictos/
    Bandeja de auditoría de consumos con estado RECHAZADO_CONFLICTO.
    - Paginada y ordenada cronológicamente inverso.
    - Exclusivo para Gestores de Inventario.
    - Filtros opcionales: resuelto=true|false, id_veterinario, id_medicamento.
    - Solo lectura.
    """
    permission_classes = [EsGestorInventario]

    def get(self, request):
        resuelto = validar_parametro_booleano(request.query_params.get('resuelto'), 'resuelto')
        id_vet = validar_parametro_entero_positivo(request.query_params.get('id_veterinario'), 'id_veterinario')
        id_med = validar_parametro_entero_positivo(request.query_params.get('id_medicamento'), 'id_medicamento')

        filtros = {
            'resuelto': resuelto,
            'id_veterinario': id_vet,
            'id_medicamento': id_med,
        }

        qs = construir_queryset_conflictos(filtros=filtros)

        paginator = KitStandardPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            serializer = ConflictoOutputSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = ConflictoOutputSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
