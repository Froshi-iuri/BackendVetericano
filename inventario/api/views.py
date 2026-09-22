from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from inventario.models import Proveedores, Compra, DetalleCompra, Salidas, DetalleSalida, Inventarios
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
