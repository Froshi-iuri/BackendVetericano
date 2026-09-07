from django.contrib import admin
from .models import Especie, Raza


@admin.register(Especie)
class EspecieAdmin(admin.ModelAdmin):
    list_display = ('id_especie', 'nombre', 'activo')
    search_fields = ('nombre',)
    list_filter = ('activo',)


@admin.register(Raza)
class RazaAdmin(admin.ModelAdmin):
    list_display = ('id_raza', 'nombre', 'id_especie', 'activo')
    search_fields = ('nombre',)
    list_filter = ('activo', 'id_especie')

