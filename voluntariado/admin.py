from django.contrib import admin
from .models import (
    EventoVoluntariado,
    PostulacionVoluntariado,
    TiposEventos,
    Eventos,
    VoluntarioEvento,
)


@admin.register(EventoVoluntariado)
class EventoVoluntariadoAdmin(admin.ModelAdmin):
    list_display = ('id', 'titulo', 'fecha')
    search_fields = ('titulo',)


@admin.register(PostulacionVoluntariado)
class PostulacionVoluntariadoAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre_completo', 'correo', 'telefono', 'evento', 'fecha_postulacion')
    search_fields = ('nombre_completo', 'correo', 'identificacion')


@admin.register(TiposEventos)
class TiposEventosAdmin(admin.ModelAdmin):
    list_display = ('id_tipo_evento', 'nombre', 'activo')
    search_fields = ('nombre',)


@admin.register(Eventos)
class EventosAdmin(admin.ModelAdmin):
    list_display = ('id_evento', 'nombre', 'id_tipo_evento', 'fecha')
    search_fields = ('nombre',)


@admin.register(VoluntarioEvento)
class VoluntarioEventoAdmin(admin.ModelAdmin):
    list_display = ('id_voluntario_evento', 'id_usuario', 'id_evento', 'fecha')
