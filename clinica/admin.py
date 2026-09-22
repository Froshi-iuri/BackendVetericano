from django.contrib import admin
from .models import (
    HistoriaClinica,
    Consulta,
    HospitalizacionSeresSintientes,
    Tratamientos,
    Diagnostico,
    SeguimientosClinicos,
    DiagnosticoPatologia,
    SeguimientoHospitalario,
)


@admin.register(HistoriaClinica)
class HistoriaClinicaAdmin(admin.ModelAdmin):
    list_display = ('id_historia', 'id_animal', 'numero_historia', 'fecha_apertura', 'estado_general')
    search_fields = ('numero_historia',)


@admin.register(Consulta)
class ConsultaAdmin(admin.ModelAdmin):
    list_display = ('id_consulta', 'id_historia', 'fecha_hora', 'prioridad', 'motivo_consulta')
    search_fields = ('motivo_consulta',)
    list_filter = ('prioridad',)


@admin.register(HospitalizacionSeresSintientes)
class HospitalizacionAdmin(admin.ModelAdmin):
    list_display = ('id_hospitalizacion', 'id_consulta', 'fecha_ingreso', 'responsable_clinico')
    search_fields = ('responsable_clinico',)


@admin.register(Tratamientos)
class TratamientosAdmin(admin.ModelAdmin):
    list_display = ('id_tratamiento', 'id_consulta', 'producto_base', 'dosis_basica')
    search_fields = ('producto_base',)


@admin.register(Diagnostico)
class DiagnosticoAdmin(admin.ModelAdmin):
    list_display = ('id_diagnostico', 'id_consulta', 'tipo_diagnostico')
    search_fields = ('tipo_diagnostico',)


@admin.register(SeguimientosClinicos)
class SeguimientosClinicosAdmin(admin.ModelAdmin):
    list_display = ('id_seguimiento', 'id_consulta', 'fecha', 'temperatura', 'peso')


@admin.register(DiagnosticoPatologia)
class DiagnosticoPatologiaAdmin(admin.ModelAdmin):
    list_display = ('id_diagnostico_patologia', 'id_diagnostico', 'id_patologia')


@admin.register(SeguimientoHospitalario)
class SeguimientoHospitalarioAdmin(admin.ModelAdmin):
    list_display = ('id_seguimiento_hosp', 'id_hospitalizacion', 'fecha')
