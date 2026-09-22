from django.db import models


class Examen(models.Model):
    id_examen = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey('clinica.Consulta', models.DO_NOTHING, db_column='id_consulta')
    nombre_tipo_examen = models.CharField(max_length=100, blank=True, null=True)
    solicitado = models.BooleanField(default=True)
    descripcion_hallazgos = models.TextField(blank=True, null=True)
    resultado = models.TextField(blank=True, null=True)
    ruta_archivo_resultado = models.CharField(max_length=500, blank=True, null=True)
    fecha_realizacion = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=50, default='activo', blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'examen'

    def __str__(self):
        return f"Examen #{self.id_examen}"


class ProcedimientoRealizado(models.Model):
    id_procedimiento = models.AutoField(primary_key=True)
    id_hospitalizacion = models.ForeignKey('clinica.HospitalizacionSeresSintientes', models.DO_NOTHING, db_column='id_hospitalizacion')
    descripcion = models.TextField(blank=True, null=True)
    fecha_procedimiento = models.DateField(blank=True, null=True)
    hora_procedimiento = models.TimeField(blank=True, null=True)
    medico_responsable = models.CharField(max_length=150, blank=True, null=True)
    tipo_procedimiento = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'procedimiento_realizado'

    def __str__(self):
        return f"Procedimiento #{self.id_procedimiento}"
