from django.contrib import admin

from triaje_ia.models import TriajePeticion


@admin.register(TriajePeticion)
class TriajePeticionAdmin(admin.ModelAdmin):
    list_display = (
        'id_triaje',
        'peticion',
        'estado_envio',
        'estado_analisis',
        'prioridad_original',
        'prioridad_sugerida',
        'veterinario_recomendado',
        'total_duplicados_detectados',
        'intentos_envio',
        'fecha_actualizacion',
    )
    list_filter = ('estado_envio', 'estado_analisis', 'prioridad_sugerida')
    search_fields = ('clave_idempotencia', 'peticion__numero_radicado', 'justificacion')
    readonly_fields = ('fecha_creacion', 'fecha_actualizacion')
