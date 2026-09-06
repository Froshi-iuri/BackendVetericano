from django.db import models

class Especie(models.Model):
    id_especie = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'especie'

class Raza(models.Model):
    id_raza = models.AutoField(primary_key=True)
    id_especie = models.ForeignKey('Especie', models.DO_NOTHING, db_column='id_especie')
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'raza'