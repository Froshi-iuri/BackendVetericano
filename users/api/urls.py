from django.urls import path
from .views import LoginView, RegisterView
# AnaC
from users.views import solicitar_recuperacion, confirmar_recuperacion

# EL PORQUÉ: Registramos las URLs específicas de la API de usuarios que el enrutador principal de Django expondrá.
urlpatterns = [
    path('login/', LoginView.as_view(), name='login'),
    path('register/', RegisterView.as_view(), name='register'),
    # AnaC
    path('recuperar-password/', solicitar_recuperacion, name='solicitar_recuperacion'),
    path('confirmar-password/', confirmar_recuperacion, name='confirmar_recuperacion'),
]