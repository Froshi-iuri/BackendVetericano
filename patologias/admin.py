from django.contrib import admin
from .models import Patologia

# Register your models here.

@admin.register(Patologia)
class PatologiaAdmin(admin.ModelAdmin):
    list_display = ('id_patologia', 'nombre', 'activo')
    search_fields = ('nombre',)
    list_filter = ('activo',)

