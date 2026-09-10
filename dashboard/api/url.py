from django.urls import path
from .views import DashboardView

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),

    path('api/usuarios/dashboard/', include('dashboard.api.urls')),
]