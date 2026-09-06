from django.urls import path, include
from rest_framework.routers import DefaultRouter
from ..views import EspecieViewSet


router = DefaultRouter()

urlpatterns = [
    path('api/', include(router.urls)),
    path('especies/', EspeciesView.as_view(), name='especies')
     
]