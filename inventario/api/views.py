from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from .permissions import EsGestorOLecturaStaffLegacy, PermisoInventariosLegacy
from django.db import transaction
from django.db.models import Sum, Count
from inventario.models import Proveedores, Compra, DetalleCompra, Salidas, DetalleSalida, Inventarios
from inventario import notificaciones_realtime as notif_rt
from medicamentos.models import Medicamentos
from .serializers import (
    ProveedoresSerializer,
    CompraSerializer,
    DetalleCompraSerializer,
    SalidasSerializer,
    DetalleSalidaSerializer,
    InventariosSerializer,
)


class ProveedoresViewSet(viewsets.ModelViewSet):
    queryset = Proveedores.objects.all()
    serializer_class = ProveedoresSerializer
    permission_classes = [EsGestorOLecturaStaffLegacy]


class CompraViewSet(viewsets.ModelViewSet):
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


class DetalleCompraViewSet(viewsets.ModelViewSet):
    queryset = DetalleCompra.objects.all()
    serializer_class = DetalleCompraSerializer
    permission_classes = [EsGestorOLecturaStaffLegacy]


class SalidasViewSet(viewsets.ModelViewSet):
    queryset = Salidas.objects.all()
    serializer_class = SalidasSerializer
    permission_classes = [EsGestorOLecturaStaffLegacy]


class DetalleSalidaViewSet(viewsets.ModelViewSet):
    queryset = DetalleSalida.objects.all()
    serializer_class = DetalleSalidaSerializer
    permission_classes = [EsGestorOLecturaStaffLegacy]

    def get_serializer(self, *args, **kwargs):
        # Si la data es una lista, decirle a DRF que procese en modo 'many=True'
        if isinstance(kwargs.get("data", {}), list):
            kwargs["many"] = True
        return super().get_serializer(*args, **kwargs)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        # Al poner @transaction.atomic aquí en la vista, garantizamos que si se envía
        # un array de múltiples DetalleSalida, todos se guarden y descuenten inventario
        # en bloque. Si uno solo falla por stock insuficiente, todo el lote se revierte.
        return super().create(request, *args, **kwargs)


class InventariosViewSet(viewsets.ModelViewSet):
    queryset = Inventarios.objects.all()
    serializer_class = InventariosSerializer
    permission_classes = [PermisoInventariosLegacy]

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
        super().perform_destroy(instance)
        notif_rt.alertar_bodega(med_ids=med_ids)
