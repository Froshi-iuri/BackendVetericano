from rest_framework import viewsets
from users.api.permissions import PermisoAdopciones
from animales.models import Animal
from .serializers import AdopcionAnimalSerializer
from realtime import notificar
from vetericano.paginacion import PaginacionOpcional

# ADO-01: los cambios del catálogo de adopciones refrescan las vistas autorizadas (rol 1).
ROLES_ADOPCIONES_AS = (1,)


class AdopcionAnimalViewSet(viewsets.ModelViewSet):
    queryset = Animal.objects.filter(activo=True)
    serializer_class = AdopcionAnimalSerializer
    permission_classes = [PermisoAdopciones]
    pagination_class = PaginacionOpcional

    def get_queryset(self):
        # Si es una petición para listar, podemos devolver solo los disponibles, 
        # o todos si el frontend admin quiere verlos todos.
        # Por ahora dejamos activo=True. El frontend parece pedir todos.
        return super().get_queryset()

    def _avisar_catalogo(self, accion, instancia):
        notificar.actualizacion_silenciosa(
            'adopcion.catalogo_actualizado', accion=accion, roles=ROLES_ADOPCIONES_AS,
            referencia={'id_animal': getattr(instancia, 'pk', None)},
        )

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._avisar_catalogo('creado', serializer.instance)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._avisar_catalogo('actualizado', serializer.instance)

    def perform_destroy(self, instance):
        pk = instance.pk
        super().perform_destroy(instance)
        notificar.actualizacion_silenciosa(
            'adopcion.catalogo_actualizado', accion='eliminado', roles=ROLES_ADOPCIONES_AS,
            referencia={'id_animal': pk},
        )
