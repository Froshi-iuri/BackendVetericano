from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EspecieViewSet, RazaViewSet

router = DefaultRouter()
router.register(r'especies', EspecieViewSet, basename='especies')
router.register(r'razas', RazaViewSet, basename='razas')

urlpatterns = [
    path('', include(router.urls)),
]
