from django.contrib import admin
from .models import (
    TipoPeticion,
    EstadoPeticiones,
    Ubicaciones,
    Peticiones,
    EvidenciaPeticiones,
    SeguimientoPeticionesVisita,
    VisitaAnimal,
    SeguimientoVisitaFuncionarios,
    EvidenciaSeguimientoVisita,
)



@admin.register(TipoPeticion)
class TipoPeticionAdmin(admin.ModelAdmin):
    list_display = ('id_tipo', 'nombre', 'activo')
    search_fields = ('nombre',)


@admin.register(EstadoPeticiones)
class EstadoPeticionesAdmin(admin.ModelAdmin):
    list_display = ('id_estado', 'nombre', 'orden', 'activo')
    search_fields = ('nombre',)


@admin.register(Ubicaciones)
class UbicacionesAdmin(admin.ModelAdmin):
    list_display = ('id_ubicacion', 'direccion', 'latitud', 'longitud')
    search_fields = ('direccion',)


@admin.register(Peticiones)
class PeticionesAdmin(admin.ModelAdmin):
    list_display = ('id_peticion', 'id_tipo', 'id_estado', 'responsable', 'prioridad', 'fecha', 'foto')
    list_filter = ('id_tipo', 'id_estado', 'prioridad')
    search_fields = ('descripcion', 'id_ubicacion__direccion', 'foto')


@admin.register(EvidenciaPeticiones)
class EvidenciaPeticionesAdmin(admin.ModelAdmin):
    list_display = ('id_evidencia', 'id_peticion', 'descripcion')


@admin.register(SeguimientoPeticionesVisita)
class SeguimientoPeticionesVisitaAdmin(admin.ModelAdmin):
    list_display = ('id_seguimiento', 'id_peticion', 'id_veterinario', 'fecha')


@admin.register(VisitaAnimal)
class VisitaAnimalAdmin(admin.ModelAdmin):
    list_display = ('id_visita', 'id_animal', 'fecha', 'nombre_notificado')


@admin.register(SeguimientoVisitaFuncionarios)
class SeguimientoVisitaFuncionariosAdmin(admin.ModelAdmin):
    list_display = ('id', 'id_seguimiento', 'nombre', 'cargo', 'es_externo', 'es_principal')
    list_filter = ('es_externo', 'es_principal')
    search_fields = ('nombre', 'cargo', 'institucion')


@admin.register(EvidenciaSeguimientoVisita)
class EvidenciaSeguimientoVisitaAdmin(admin.ModelAdmin):
    list_display = ('id_evidencia', 'id_seguimiento', 'tipo_archivo', 'descripcion', 'fecha')
    list_filter = ('tipo_archivo', 'fecha')
    search_fields = ('descripcion', 'ruta_archivo')




from .models import HistorialAsignacionPeticion, NotificacionJuridico


@admin.register(HistorialAsignacionPeticion)
class HistorialAsignacionPeticionAdmin(admin.ModelAdmin):
    list_display = ('id_historial', 'id_peticion', 'veterinario_anterior', 'veterinario_nuevo', 'asignado_por', 'fecha')
    list_filter = ('fecha',)
    search_fields = ('motivo', 'veterinario_anterior__nombre', 'veterinario_nuevo__nombre')


@admin.register(NotificacionJuridico)
class NotificacionJuridicoAdmin(admin.ModelAdmin):
    list_display = ('id_notificacion', 'id_peticion', 'tipo', 'leida', 'fecha_creacion')
    list_filter = ('tipo', 'leida', 'fecha_creacion')
    search_fields = ('mensaje',)
