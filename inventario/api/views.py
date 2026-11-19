from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.db.models.deletion import ProtectedError, RestrictedError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from inventario import notificaciones_realtime as notif_rt
from inventario.models import Proveedores, Compra, DetalleCompra, Salidas, DetalleSalida, Inventarios
from inventario.services.compras_service import (
    verificar_compra_sin_dependencias,
    verificar_detalle_compra_sin_dependencias,
    verificar_lote_eliminable,
)
from inventario.services.exceptions import ConflictoOperacionInventarioError
from medicamentos.models import Medicamentos
from .kit_views import KitErrorHandlingMixin
from .permissions import EsGestorOLecturaStaffLegacy, PermisoInventariosLegacy
from .serializers import (
    MENSAJE_CREACION_DIRECTA_LOTE_NO_PERMITIDA,
    ProveedoresSerializer,
    CompraSerializer,
    DetalleCompraSerializer,
    SalidasSerializer,
    DetalleSalidaSerializer,
    InventariosSerializer,
)


_MENSAJE_SALIDA_LEGACY_BLOQUEADA = (
    "Las mutaciones sobre /api/inventario/detalles-salida/ están bloqueadas porque no existen "
    "consumidores activos y la operación modificaba existencias de Bodega General sin registro "
    "en Kardex ni tipo de movimiento aprobado. Use GET para consulta histórica."
)


class ProveedoresViewSet(viewsets.ModelViewSet):
    queryset = Proveedores.objects.all()
    serializer_class = ProveedoresSerializer
    permission_classes = [EsGestorOLecturaStaffLegacy]


class CompraViewSet(KitErrorHandlingMixin, viewsets.ModelViewSet):
    queryset = Compra.objects.all()
    serializer_class = CompraSerializer
    permission_classes = [EsGestorOLecturaStaffLegacy]

    @action(detail=False, methods=['get'])
    def estadisticas(self, request):
        facturas_registradas = Compra.objects.count()
        medicamentos_sistema = Medicamentos.objects.count()
        unidades_ingresadas = DetalleCompra.objects.aggregate(Sum('cantidad'))['cantidad__sum'] or 0

        return Response({
            'facturas_registradas': facturas_registradas,
            'medicamentos_sistema': medicamentos_sistema,
            'unidades_ingresadas': unidades_ingresadas
        })

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        compra_bloqueada = verificar_compra_sin_dependencias(instance.pk, "eliminar")
        try:
            compra_bloqueada.delete()
        except (ProtectedError, RestrictedError, IntegrityError) as exc:
            raise ConflictoOperacionInventarioError(
                f"No se puede eliminar la compra #{instance.pk} por restricciones de integridad referencial.",
                codigo="COMPRA_CON_DEPENDENCIAS",
            ) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


class DetalleCompraViewSet(KitErrorHandlingMixin, viewsets.ModelViewSet):
    queryset = DetalleCompra.objects.all()
    serializer_class = DetalleCompraSerializer
    permission_classes = [EsGestorOLecturaStaffLegacy]

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        detalle_bloqueado = verificar_detalle_compra_sin_dependencias(instance.pk, "eliminar")
        try:
            detalle_bloqueado.delete()
        except (ProtectedError, RestrictedError, IntegrityError) as exc:
            raise ConflictoOperacionInventarioError(
                f"No se puede eliminar el detalle de compra #{instance.pk} por restricciones de integridad referencial.",
                codigo="DETALLE_COMPRA_CON_DEPENDENCIAS",
            ) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


class SalidasViewSet(viewsets.ModelViewSet):
    queryset = Salidas.objects.all()
    serializer_class = SalidasSerializer
    permission_classes = [EsGestorOLecturaStaffLegacy]


class DetalleSalidaViewSet(KitErrorHandlingMixin, viewsets.ModelViewSet):
    queryset = DetalleSalida.objects.all()
    serializer_class = DetalleSalidaSerializer
    permission_classes = [EsGestorOLecturaStaffLegacy]

    def _bloquear_mutacion_legacy(self):
        raise ConflictoOperacionInventarioError(
            _MENSAJE_SALIDA_LEGACY_BLOQUEADA,
            codigo="SALIDA_LEGACY_BLOQUEADA",
        )

    def create(self, request, *args, **kwargs):
        self._bloquear_mutacion_legacy()

    def update(self, request, *args, **kwargs):
        self._bloquear_mutacion_legacy()

    def partial_update(self, request, *args, **kwargs):
        self._bloquear_mutacion_legacy()

    def destroy(self, request, *args, **kwargs):
        self._bloquear_mutacion_legacy()


class InventariosViewSet(KitErrorHandlingMixin, viewsets.ModelViewSet):
    queryset = Inventarios.objects.all()
    serializer_class = InventariosSerializer
    permission_classes = [PermisoInventariosLegacy]

    def create(self, request, *args, **kwargs):
        # Las permission_classes se evalúan antes en initial(): sin token 401, no gestor 403.
        # Para gestores, el alta directa de lotes queda bloqueada (409): un lote solo nace
        # con su ENTRADA_COMPRA en Kardex vía compras_service (compras/ o detalles-compra/).
        raise ConflictoOperacionInventarioError(
            MENSAJE_CREACION_DIRECTA_LOTE_NO_PERMITIDA,
            codigo="CREACION_DIRECTA_LOTE_NO_PERMITIDA",
        )

    def update(self, request, *args, **kwargs):
        if request.method == 'PUT':
            return Response(
                {
                    "error": "PUT_NO_PERMITIDO",
                    "detalle": "El reemplazo completo PUT no está permitido en /api/inventario/inventarios/{id}/. Use PATCH para actualizar únicamente 'estado' o 'fecha_vencimiento'.",
                },
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        return super().update(request, *args, **kwargs)

    # Tiempo real INV-02 (AO a rol_1/rol_2, tras el COMMIT): alta, ajuste y baja de lotes de bodega.
    # No cambian la respuesta: las evaluaciones son tolerantes y corren en su propio SAVEPOINT.
    def perform_create(self, serializer):
        super().perform_create(serializer)
        notif_rt.alertar_bodega(inventario_ids=[serializer.instance.pk])

    def perform_update(self, serializer):
        super().perform_update(serializer)
        notif_rt.alertar_bodega(inventario_ids=[serializer.instance.pk])

    def perform_destroy(self, instance):
        med_ids = notif_rt.medicamentos_de_lotes([instance.pk])
        try:
            super().perform_destroy(instance)
        except (ProtectedError, RestrictedError, IntegrityError) as exc:
            raise ConflictoOperacionInventarioError(
                f"No se puede eliminar el lote #{instance.pk} por restricciones de integridad referencial.",
                codigo="LOTE_CON_DEPENDENCIAS",
            ) from exc
        notif_rt.alertar_bodega(med_ids=med_ids)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        lote_bloqueado = verificar_lote_eliminable(instance.pk)
        self.perform_destroy(lote_bloqueado)
        return Response(status=status.HTTP_204_NO_CONTENT)
