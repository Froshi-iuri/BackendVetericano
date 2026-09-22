from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PatologiaViewSet

router = DefaultRouter()
router.register(r'patologias', PatologiaViewSet, basename='patologia')

urlpatterns = [
    path('', include(router.urls)),
]
