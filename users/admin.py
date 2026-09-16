from django.contrib import admin
from .models import Usuarios, Rol


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ('id_rol', 'nombre_rol')


@admin.register(Usuarios)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('id_usuario', 'email', 'nombre', 'apellido', 'id_rol', 'activo')
    list_filter = ('id_rol', 'activo')
    search_fields = ('email', 'nombre', 'apellido')

