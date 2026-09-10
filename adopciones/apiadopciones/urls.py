from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AdopcionAnimalViewSet

router = DefaultRouter()
router.register(r'adopciones', AdopcionAnimalViewSet, basename='adopcion')

urlpatterns = [
    path('', include(router.urls)),
]