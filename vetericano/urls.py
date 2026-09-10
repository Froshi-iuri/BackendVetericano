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

    # Endpoints de Usuarios
    path('api/usuarios/', include('users.api.urls')),

    # Swagger Documentation
    path(
        'swagger/',
        schema_view.with_ui('swagger', cache_timeout=0),
        name='schema-swagger-ui'
    ),
    path('api/especies/', include('especies.apiespecies.urls')),

    # Endpoints de Exámenes Clínicos
    path('api/examenes-clinicos/', include('examenes_clinicos.api.urls')),


    #  rutas de patologías 
    path('api/patologias/', include('patologias.apipatologias.urls')),

    # AnaC
    path('api/', include('medicamentos.apimedicamentos.urls')),

    path('api/', include('adopciones.apiadopciones.urls')),

    path('api/', include('voluntariado.apivoluntariado.urls')),

    # AnaC

    #Jube 
    path('api/dashboard/', include('dashboard.api.urls')),

    #jdqa
    path('api/peticiones/', include('peticiones.urls')),


]