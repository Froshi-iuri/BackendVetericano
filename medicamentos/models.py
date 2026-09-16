from django.db import models

# AnaC

class Medicamento(models.Model):
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True, null=True)
    cantidad_ml = models.CharField(max_length=50) # Usamos CharField por si el usuario escribe "20ml" en texto
    tipo = models.CharField(max_length=100)
    estado = models.BooleanField(default=True) # True = Activo, False = Inactivo

    def __str__(self):
        return self.nombre

# Create your models here.
