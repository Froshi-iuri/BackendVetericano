from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    EventoVoluntariadoViewSet,
    PostulacionVoluntariadoViewSet,
    TiposEventosViewSet,
    EventosViewSet,
    VoluntarioEventoViewSet,
)

router = DefaultRouter()
router.register(r'voluntariado/eventos', EventoVoluntariadoViewSet, basename='evento-voluntariado')
router.register(r'voluntariado/postulaciones', PostulacionVoluntariadoViewSet, basename='postulacion-voluntariado')
router.register(r'voluntariado/tipos-eventos', TiposEventosViewSet, basename='tipo-evento')
router.register(r'voluntariado/eventos-sistema', EventosViewSet, basename='evento-sistema')
router.register(r'voluntariado/voluntarios-evento', VoluntarioEventoViewSet, basename='voluntario-evento')

urlpatterns = [
    path('', include(router.urls)),
]
