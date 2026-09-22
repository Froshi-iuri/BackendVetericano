from django.contrib import admin
from .models import (
    Medicamento,
    Medicamentos,
    AdministracionMedicamento,
    TratamientoMedicamentos,
)


@admin.register(Medicamento)
class MedicamentoAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'tipo', 'cantidad_ml', 'estado')
    search_fields = ('nombre', 'tipo')
    list_filter = ('estado', 'tipo')


@admin.register(Medicamentos)
class MedicamentosAdmin(admin.ModelAdmin):
    list_display = ('id_medicamento', 'nombre', 'principio_activo', 'presentacion', 'concentracion', 'activo')
    search_fields = ('nombre', 'principio_activo')
    list_filter = ('activo',)


@admin.register(AdministracionMedicamento)
class AdministracionMedicamentoAdmin(admin.ModelAdmin):
    list_display = ('id_administracion', 'id_medicamento', 'id_seguimiento_hosp', 'fecha_hora')


@admin.register(TratamientoMedicamentos)
class TratamientoMedicamentosAdmin(admin.ModelAdmin):
    list_display = ('id_tratamiento_medicamento', 'id_tratamiento', 'id_medicamento', 'fecha_aplicacion')
