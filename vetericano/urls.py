from django.contrib import admin
from django.urls import path, include
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

schema_view = get_schema_view(
    openapi.Info(
        title="Vetericano API",
        default_version='v1',
        description="API para la aplicación Vetericano",
    ),
    public=True
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # Swagger Documentation
    path(
        'swagger/',
        schema_view.with_ui('swagger', cache_timeout=0),
        name='schema-swagger-ui'
    ),

    # Endpoints de Usuarios
    path('api/usuarios/', include('users.api.urls')),

    # Endpoints de Especies y Razas
    path('api/especies/', include('especies.api.urls')),

    # Endpoints de Exámenes Clínicos y Procedimientos
    path('api/examenes-clinicos/', include('examenes_clinicos.api.urls')),

    # Endpoints de Patologías
    path('api/patologias/', include('patologias.api.urls')),

    # Endpoints de Medicamentos
    path('api/', include('medicamentos.api.urls')),

    # Endpoints de Adopciones
    path('api/', include('adopciones.api.urls')),

    # Endpoints de Voluntariado
    path('api/', include('voluntariado.api.urls')),

    # Endpoints de Dashboard
    path('api/dashboard/', include('dashboard.api.urls')),

    # Endpoints de Peticiones
    path('api/peticiones/', include('peticiones.api.urls')),

    # Endpoints de Animales
    path('api/animales/', include('animales.api.urls')),

    # Endpoints de Clínica e Historias Clínicas
    path('api/clinica/', include('clinica.api.urls')),

    # Endpoints de Inventario y Compras
    path('api/inventario/', include('inventario.api.urls')),
]