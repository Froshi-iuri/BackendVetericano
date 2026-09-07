from django.contrib import admin
from .models import ProcedimientosCatalogo, ProcedimientosRealizados


@admin.register(ProcedimientosCatalogo)
class ProcedimientosCatalogoAdmin(admin.ModelAdmin):
    list_display = ('id_procedimiento_catalogo', 'nombre_tipo')
    search_fields = ('nombre_tipo',)


@admin.register(ProcedimientosRealizados)
class ProcedimientosRealizadosAdmin(admin.ModelAdmin):
    list_display = ('id_procedimiento_realizado', 'id_consulta', 'id_procedimiento_catalogo', 'resultado_anexo_url')
    list_filter = ('id_procedimiento_catalogo',)
    search_fields = ('resultado_anexo_url',)

