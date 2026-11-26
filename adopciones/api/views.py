from rest_framework import viewsets
from users.api.permissions import PermisoAdopciones, es_administrador
from animales.models import Animal
from .serializers import AdopcionAnimalSerializer
from realtime import notificar
from django.db.models import F
from vetericano.paginacion import PaginacionOpcional

# ADO-01: los cambios del catálogo de adopciones refrescan las vistas autorizadas (rol 1).
ROLES_ADOPCIONES_AS = (1,)


class AdopcionAnimalViewSet(viewsets.ModelViewSet):
    # Disponibles para adopción primero; luego los ingresos más recientes.
    queryset = Animal.objects.filter(activo=True).order_by(
        '-disponible_adopcion', F('fecha_ingreso').desc(nulls_last=True), '-id_animal',
    )
    serializer_class = AdopcionAnimalSerializer
    permission_classes = [PermisoAdopciones]
    pagination_class = PaginacionOpcional

    def get_queryset(self):
        # El Administrador gestiona el catálogo completo (incluye los "No Disponible").
        # Para el público, ciudadanos y demás roles solo existen los animales disponibles
        # para adopción: los pacientes clínicos no aparecen en la lista y su detalle da 404.
        queryset = super().get_queryset()
        if es_administrador(getattr(self.request, 'user', None)):
            return queryset
        return queryset.filter(disponible_adopcion=True)

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
