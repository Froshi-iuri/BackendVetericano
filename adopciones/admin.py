from django.contrib import admin
from .models import AdopcionAnimal


@admin.register(AdopcionAnimal)
class AdopcionAnimalAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'raza', 'disponible', 'fecha_creacion')
    search_fields = ('nombre', 'raza')
    list_filter = ('disponible',)
