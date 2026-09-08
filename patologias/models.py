from django.db import models

class Patologia(models.Model):
    id_patologia = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150, unique=True)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = 'patologia'
        verbose_name = 'Patología'
        verbose_name_plural = 'Patologías'

    def __str__(self):
        return self.nombre