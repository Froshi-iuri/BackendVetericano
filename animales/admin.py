from django.contrib import admin
from .models import Responsables, Animal, EstadosAnimal, AnimalResponsable


@admin.register(Responsables)
class ResponsablesAdmin(admin.ModelAdmin):
    list_display = ('id_responsable', 'nombre', 'documento', 'telefono', 'correo', 'activo')
    search_fields = ('nombre', 'documento')
    list_filter = ('activo',)


@admin.register(Animal)
class AnimalAdmin(admin.ModelAdmin):
    list_display = ('id_animal', 'nombre', 'id_raza', 'sexo', 'color', 'activo')
    search_fields = ('nombre',)
    list_filter = ('sexo', 'activo', 'esterilizado')


@admin.register(EstadosAnimal)
class EstadosAnimalAdmin(admin.ModelAdmin):
    list_display = ('id_estado_animal', 'id_animal', 'nombre_estado', 'fecha_registro')
    search_fields = ('nombre_estado',)


@admin.register(AnimalResponsable)
class AnimalResponsableAdmin(admin.ModelAdmin):
    list_display = ('id_animal_responsable', 'id_animal', 'id_responsable', 'tipo_responsabilidad', 'fecha_inicio')
    search_fields = ('tipo_responsabilidad',)
