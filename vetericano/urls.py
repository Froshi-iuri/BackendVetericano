from django.contrib import admin
from django.urls import path, include
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

schema_view = get_schema_view(
    openapi.Info(
        title="Vetericano API",
        default_version="v1",
        description="API del sistema Vetericano"
    ),
    public=True
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # Endpoints de Usuarios
    path('api/usuarios/', include('users.api.urls')),

    # Swagger Documentation
    path(
        'swagger/',
        schema_view.with_ui('swagger', cache_timeout=0),
        name='schema-swagger-ui'
    ),
]