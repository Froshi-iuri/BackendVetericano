from django.db import models


# Modelo activo en los endpoints de DRF
class Medicamento(models.Model):
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True, null=True)
    cantidad_ml = models.CharField(max_length=50)
    tipo = models.CharField(max_length=100)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'medicamentos_medicamento'

    def __str__(self):
        return self.nombre


# Modelo para la tabla medicamentos
class Medicamentos(models.Model):
    id_medicamento = models.AutoField(primary_key=True)
    id_salida = models.ForeignKey('inventario.Salidas', models.DO_NOTHING, db_column='id_salida', blank=True, null=True)
    nombre = models.CharField(max_length=150)
    principio_activo = models.CharField(max_length=150, blank=True, null=True)
    presentacion = models.CharField(max_length=100, blank=True, null=True)
    concentracion = models.CharField(max_length=50, blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    cantidad_ml = models.CharField(max_length=50, blank=True, null=True)
    tipo = models.CharField(max_length=50, blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'medicamentos'

    def __str__(self):
        return self.nombre


class AdministracionMedicamento(models.Model):
    id_administracion = models.AutoField(primary_key=True)
    id_medicamento = models.ForeignKey(Medicamentos, models.DO_NOTHING, db_column='id_medicamento')
    id_seguimiento_hosp = models.ForeignKey('clinica.SeguimientoHospitalario', models.DO_NOTHING, db_column='id_seguimiento_hosp')
    dosis_administrada = models.CharField(max_length=100, blank=True, null=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'administracion_medicamento'

    def __str__(self):
        return f"Administración #{self.id_administracion}"


class TratamientoMedicamentos(models.Model):
    id_tratamiento_medicamento = models.AutoField(primary_key=True)
    id_tratamiento = models.ForeignKey('clinica.Tratamientos', models.DO_NOTHING, db_column='id_tratamiento')
    id_medicamento = models.ForeignKey(Medicamentos, models.DO_NOTHING, db_column='id_medicamento')
    dosis = models.CharField(max_length=100, blank=True, null=True)
    fecha_aplicacion = models.DateField(blank=True, null=True)
    via = models.CharField(max_length=50, blank=True, null=True)
    hora_administrada = models.TimeField(blank=True, null=True)
    responsable_admin = models.CharField(max_length=150, blank=True, null=True)
    reaccion_comentarios = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tratamiento_medicamentos'

    def __str__(self):
        return f"Tratamiento #{self.id_tratamiento_id} - Medicamento #{self.id_medicamento_id}"
