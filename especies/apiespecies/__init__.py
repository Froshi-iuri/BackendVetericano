# Puente de compatibilidad: apiespecies ahora delega a especies.api
from especies.api.serializers import EspecieSerializer, RazaSerializer  # noqa: F401
from especies.api.views import EspecieViewSet, RazaViewSet  # noqa: F401
from especies.api.urls import urlpatterns, router  # noqa: F401
