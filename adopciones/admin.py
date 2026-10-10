from django.contrib import admin
from .models import SolicitudAdopcion

@admin.register(SolicitudAdopcion)
class SolicitudAdopcionAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre_adoptante', 'animal', 'fecha_solicitud')
