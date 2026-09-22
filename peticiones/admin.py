from django.contrib import admin
from .models import (
    TipoPeticion,
    EstadoPeticiones,
    Ubicaciones,
    Peticiones,
    EvidenciaPeticiones,
    SeguimientoPeticionesVisita,
    VisitaAnimal,
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
    list_display = ('id_peticion', 'id_tipo', 'id_estado', 'responsable', 'prioridad', 'fecha')
    list_filter = ('id_tipo', 'id_estado', 'prioridad')
    search_fields = ('descripcion', 'direccion')


@admin.register(EvidenciaPeticiones)
class EvidenciaPeticionesAdmin(admin.ModelAdmin):
    list_display = ('id_evidencia', 'id_peticion', 'descripcion')


@admin.register(SeguimientoPeticionesVisita)
class SeguimientoPeticionesVisitaAdmin(admin.ModelAdmin):
    list_display = ('id_seguimiento', 'id_peticion', 'id_veterinario', 'fecha')


@admin.register(VisitaAnimal)
class VisitaAnimalAdmin(admin.ModelAdmin):
    list_display = ('id_visita', 'id_animal', 'fecha', 'nombre_notificado')
