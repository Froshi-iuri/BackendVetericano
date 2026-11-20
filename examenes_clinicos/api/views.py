from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from examenes_clinicos.models import Examen, ProcedimientoRealizado
from users.api.permissions import EsAdminOReadOnlyParaStaff, PermisoClinica
from .serializers import ExamenSerializer, ProcedimientoRealizadoSerializer


class ExamenViewSet(viewsets.ModelViewSet):
    queryset = Examen.objects.all().order_by('-id_examen')
    serializer_class = ExamenSerializer
    permission_classes = [EsAdminOReadOnlyParaStaff]

    def create(self, request, *args, **kwargs):
        is_many = isinstance(request.data, list)
        serializer = self.get_serializer(data=request.data, many=is_many)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()

    @action(detail=False, methods=['post'], url_path='carga-masiva')
    def carga_masiva(self, request):
        """
        Permite la carga masiva de exámenes clínicos desde JSON o lista de objetos.
        Valida que cada examen tenga nombre y descripción.
        """
        items = request.data
        if not isinstance(items, list):
            items = [items]

        creados = []
        actualizados = []
        errores = []

        for idx, item in enumerate(items, start=1):
            nombre = (item.get('nombre') or item.get('nombre_tipo_examen') or item.get('nombre_tipo') or '').strip()
            if not nombre:
                errores.append(f"Fila {idx}: El nombre del examen es obligatorio.")
                continue

            descripcion = (item.get('descripcion') or item.get('descripcion_hallazgos') or '').strip()
            tipo = (item.get('tipo') or item.get('ruta_archivo_resultado') or 'Prueba Diagnóstica').strip()
            estado = str(item.get('estado', 'activo')).lower().strip()
            if estado in ('true', '1', 'si', 'sí', 'activo'):
                estado = 'activo'
            elif estado in ('false', '0', 'no', 'inactivo'):
                estado = 'inactivo'

            obj, created = Examen.objects.update_or_create(
                nombre_tipo_examen=nombre,
                id_consulta__isnull=True,
                defaults={
                    'descripcion_hallazgos': descripcion or None,
                    'ruta_archivo_resultado': tipo or None,
                    'estado': estado,
                    'solicitado': True
                }
            )
            if created:
                creados.append(obj.nombre_tipo_examen)
            else:
                actualizados.append(obj.nombre_tipo_examen)

        return Response({
            'mensaje': f'Proceso completado. Creados: {len(creados)}, Actualizados: {len(actualizados)}, Errores: {len(errores)}',
            'creados_count': len(creados),
            'actualizados_count': len(actualizados),
            'errores': errores
        }, status=status.HTTP_200_OK if not errores or len(creados) > 0 else status.HTTP_400_BAD_REQUEST)


class ProcedimientoRealizadoViewSet(viewsets.ModelViewSet):
    queryset = ProcedimientoRealizado.objects.all()
    serializer_class = ProcedimientoRealizadoSerializer
    permission_classes = [PermisoClinica]
