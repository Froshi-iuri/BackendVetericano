from django.urls import path
from .views import LoginView, RegisterView

# EL PORQUÉ: Registramos las URLs específicas de la API de usuarios que el enrutador principal de Django expondrá.
urlpatterns = [
    path('login/', LoginView.as_view(), name='login'),
    path('register/', RegisterView.as_view(), name='register'),
]