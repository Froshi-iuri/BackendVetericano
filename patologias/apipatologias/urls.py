from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PatologiaViewSet

router = DefaultRouter()
router.register(r'patologias', PatologiaViewSet, basename='patologias')

urlpatterns = [
    path('', include(router.urls)),
]