"""
Paginación OPCIONAL (opt-in) compartida por los endpoints de listado.

Contrato:
- Sin ``page`` ni ``page_size`` en la query: NO pagina. La vista responde exactamente
  como antes (por ejemplo, el arreglo plano ``[...]``), así los clientes actuales
  (app móvil) no se ven afectados.
- Con ``page`` y/o ``page_size``: respuesta estándar de DRF
  ``{"count", "next", "previous", "results"}``. ``page_size`` por defecto 20, máximo 100.
  Una página inexistente responde 404 (comportamiento estándar de DRF).
- El orden de la página es determinista: si el queryset no tiene orden se ordena por
  ``pk`` y, si lo tiene, se añade ``pk`` como desempate. Esto solo se aplica al paginar;
  la respuesta sin paginar conserva su orden original.

Uso:
- Vistas genéricas / ViewSets: ``pagination_class = PaginacionOpcional``.
- APIView manual::

      paginador = PaginacionOpcional()
      pagina = paginador.paginate_queryset(queryset, request, view=self)
      if pagina is not None:
          return paginador.get_paginated_response(Serializer(pagina, many=True).data)
      # ...respuesta original sin cambios
"""

from django.db.models import QuerySet
from rest_framework.pagination import PageNumberPagination


def ordenar_de_forma_estable(queryset):
    """Devuelve el queryset con un orden total (termina en ``pk``) para paginar sin saltos."""
    if not isinstance(queryset, QuerySet):
        return queryset
    consulta = queryset.query
    # DISTINCT ON y uniones (union/intersection) restringen el ORDER BY: no se tocan.
    if consulta.distinct_fields or consulta.combinator:
        return queryset

    campos = list(consulta.order_by)
    if not campos and consulta.default_ordering:
        campos = list(queryset.model._meta.ordering or [])
    if consulta.extra_order_by:
        return queryset

    nombre_pk = queryset.model._meta.pk.name
    for campo in campos:
        if isinstance(campo, str) and campo.lstrip('-') in ('pk', nombre_pk):
            return queryset
    return queryset.order_by(*campos, 'pk')


class PaginacionOpcional(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def solicitada(self, request):
        params = request.query_params
        return self.page_query_param in params or self.page_size_query_param in params

    def paginate_queryset(self, queryset, request, view=None):
        if not self.solicitada(request):
            return None
        return super().paginate_queryset(ordenar_de_forma_estable(queryset), request, view=view)
