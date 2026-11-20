from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from patologias.models import Patologia
from users.api.permissions import EsAdminOReadOnlyParaStaff
from .serializers import PatologiaSerializer


class PatologiaViewSet(viewsets.ModelViewSet):
    queryset = Patologia.objects.all().order_by('nombre')
    serializer_class = PatologiaSerializer
    permission_classes = [EsAdminOReadOnlyParaStaff]

    def create(self, request, *args, **kwargs):
        is_many = isinstance(request.data, list)
        serializer = self.get_serializer(data=request.data, many=is_many)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=['post'], url_path='carga-masiva')
    def carga_masiva(self, request):
        """
        Permite la carga masiva de patologías desde JSON o lista de objetos.
        Valida que cada patología tenga nombre, descripción y estado.
        """
        items = request.data
        if not isinstance(items, list):
            items = [items]

        creados = []
        actualizados = []
        errores = []

        for idx, item in enumerate(items, start=1):
            nombre = (item.get('nombre') or '').strip()
            if not nombre:
                errores.append(f"Fila {idx}: El nombre es obligatorio.")
                continue

            descripcion = (item.get('descripcion') or '').strip()
            tipo = (item.get('tipo_de_enfermedad') or '').strip()
            activo = item.get('activo', True)
            if isinstance(activo, str):
                activo = activo.lower() in ('activo', 'true', '1', 'si', 'sí')

            obj, created = Patologia.objects.update_or_create(
                nombre=nombre,
                defaults={
                    'descripcion': descripcion,
                    'tipo_de_enfermedad': tipo or None,
                    'activo': bool(activo)
                }
            )
            if created:
                creados.append(obj.nombre)
            else:
                actualizados.append(obj.nombre)

        return Response({
            'mensaje': f'Proceso completado. Creados: {len(creados)}, Actualizados: {len(actualizados)}, Errores: {len(errores)}',
            'creados_count': len(creados),
            'actualizados_count': len(actualizados),
            'errores': errores
        }, status=status.HTTP_200_OK if not errores or len(creados) > 0 else status.HTTP_400_BAD_REQUEST)
