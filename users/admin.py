from django.contrib import admin
from .models import Usuarios, Rol, Sede


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ('id_rol', 'nombre_rol')


@admin.register(Sede)
class SedeAdmin(admin.ModelAdmin):
    list_display = ('id_sede', 'nombre', 'codigo', 'ciudad', 'telefono', 'activo', 'fecha_creacion')
    list_filter = ('activo', 'ciudad')
    search_fields = ('nombre', 'codigo', 'ciudad')


@admin.register(Usuarios)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('id_usuario', 'email', 'nombre', 'apellido', 'id_rol', 'id_sede', 'activo')
    list_filter = ('id_rol', 'id_sede', 'activo')
    search_fields = ('email', 'nombre', 'apellido')

