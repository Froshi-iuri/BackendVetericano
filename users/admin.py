from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuarios, Rol


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre_rol')


@admin.register(Usuarios)
class UsuarioAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Información de Rol', {'fields': ('rol', 'activo')}),
    )
    list_display = ('username', 'email', 'rol', 'activo', 'is_active')
