from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum, Count
from inventario.models import Proveedores, Compra, DetalleCompra, Salidas, DetalleSalida, Inventarios
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
    permission_classes = [IsAuthenticated]


class CompraViewSet(viewsets.ModelViewSet):
    queryset = Compra.objects.all()
    serializer_class = CompraSerializer
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]


class SalidasViewSet(viewsets.ModelViewSet):
    queryset = Salidas.objects.all()
    serializer_class = SalidasSerializer
    permission_classes = [IsAuthenticated]


class DetalleSalidaViewSet(viewsets.ModelViewSet):
    queryset = DetalleSalida.objects.all()
    serializer_class = DetalleSalidaSerializer
    permission_classes = [IsAuthenticated]


class InventariosViewSet(viewsets.ModelViewSet):
    queryset = Inventarios.objects.all()
    serializer_class = InventariosSerializer
    permission_classes = [IsAuthenticated]
