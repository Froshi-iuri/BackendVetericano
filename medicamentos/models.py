from django.db import models

# AnaC

class Medicamento(models.Model):
    # Llave primaria personalizada según Supabase
    id_medicamento = models.AutoField(primary_key=True)
    
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True, null=True)
    cantidad_ml = models.CharField(max_length=50) 
    tipo = models.CharField(max_length=100)
    
    # Cambiado a 'activo' para coincidir con Supabase y Angular
    activo = models.BooleanField(default=True)

    # Campos adicionales de la tabla de Supabase (opcionales)
    principio_activo = models.CharField(max_length=150, blank=True, null=True)
    presentacion = models.CharField(max_length=100, blank=True, null=True)
    concentracion = models.CharField(max_length=100, blank=True, null=True)
    id_salida = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False  # <-- ¡CLAVE! Igual que en Especies/Responsables para evitar que Django modifique la BD
        db_table = 'medicamentos'  # Conecta directamente con la tabla de Supabase

    def __str__(self):
        return self.nombre