from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EventoVoluntariadoViewSet, PostulacionVoluntariadoViewSet

router = DefaultRouter()
router.register(r'voluntariado/eventos', EventoVoluntariadoViewSet, basename='evento-voluntariado')
router.register(r'voluntariado/postulaciones', PostulacionVoluntariadoViewSet, basename='postulacion-voluntariado')

urlpatterns = [
    path('', include(router.urls)),
]