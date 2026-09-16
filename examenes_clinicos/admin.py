from django.contrib import admin
from .models import Examen, ProcedimientoRealizado

# Register your models here.

@admin.register(Examen)
class ExamenAdmin(admin.ModelAdmin):
    list_display = ('id_examen', 'id_consulta', 'resultado')
    search_fields = ('resultado',)


@admin.register(ProcedimientoRealizado)
class ProcedimientoRealizadoAdmin(admin.ModelAdmin):
    list_display = ('id_procedimiento', 'id_hospitalizacion', 'descripcion')
    search_fields = ('descripcion',)
