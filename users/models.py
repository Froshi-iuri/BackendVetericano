from django.db import models
from django.contrib.auth.models import AbstractUser


class Rol(models.Model):
    nombre_rol = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.nombre_rol


class Usuario(AbstractUser):
    email = models.EmailField(unique=True)
    
    # Campo nuevo para el teléfono
    telefono = models.CharField(max_length=20, null=True, blank=True)
    
    # Relación con Rol y campo activo
    rol = models.ForeignKey(Rol, on_delete=models.SET_NULL, null=True, blank=True)
    activo = models.BooleanField(default=True)

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']

    def __str__(self):
        return f"{self.username} ({self.rol.nombre_rol if self.rol else 'Sin Rol'})"
