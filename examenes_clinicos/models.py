from django.db import models
from users.models import Consulta

class Examen(models.Model):
    id_examen = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, on_delete=models.CASCADE, null=True, blank=True, db_column='id_consulta', related_name='examenes_clinicos')
    nombre_tipo_examen = models.CharField(max_length=255, null=True, blank=True)
    solicitado = models.BooleanField(default=True, null=True, blank=True)
    descripcion_hallazgos = models.TextField(null=True, blank=True)
    resultado = models.TextField(null=True, blank=True)
    ruta_archivo_resultado = models.CharField(max_length=500, null=True, blank=True)
    fecha_realizacion = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    estado = models.CharField(max_length=50, default='activo', null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'examen'


class ProcedimientoRealizado(models.Model):
    id_procedimiento = models.AutoField(primary_key=True)
    id_hospitalizacion = models.IntegerField(null=True, blank=True)
    descripcion = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'procedimiento_realizado'