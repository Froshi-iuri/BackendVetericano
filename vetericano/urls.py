from django.contrib import admin
from django.urls import path, include
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

# EL PORQUÉ: Definimos la información básica de la documentación interactiva y la hacemos pública con 'AllowAny' para evitar la pantalla de login del admin al entrar a Swagger.
schema_view = get_schema_view(
    openapi.Info(
        title="Vetericano API",
        default_version='v1',
        description="API para la aplicación Vetericano",
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    # EL PORQUÉ: Mantenemos el panel de administración únicamente bajo la ruta '/admin/'.
    path('admin/', admin.site.urls),
    
    # EL PORQUÉ: Conectamos el archivo 'users/urls.py' para habilitar los endpoints '/api/users/login/' y '/api/users/register/'. Si omitimos esto, Django no sabrá dónde existen las funciones de autenticación.
    path('api/users/', include('users.api.urls')),
    
    # EL PORQUÉ: Mantenemos la interfaz de inicio de sesión para la navegación navegable de DRF.
    path('api-auth/', include('rest_framework.urls')),
    
    # EL PORQUÉ: Ruta para ver la documentación interactiva Swagger.
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
]