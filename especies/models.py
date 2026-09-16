from django.db import models


class Especie(models.Model):
    id_especie = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'especies'

    def __str__(self):
        return self.nombre


class Raza(models.Model):
    id_raza = models.AutoField(primary_key=True)
    id_especie = models.ForeignKey('Especie', models.DO_NOTHING, db_column='id_especie', related_name='razas')
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'raza'

    def __str__(self):
        return self.nombre


class Animal(models.Model):
    id_animal = models.AutoField(primary_key=True)
    id_raza = models.ForeignKey(Raza, models.DO_NOTHING, db_column='id_raza')
    nombre = models.CharField(max_length=100)
    sexo = models.CharField(max_length=10, blank=True, null=True)
    color = models.CharField(max_length=50, blank=True, null=True)
    fecha_nacimiento = models.DateField(blank=True, null=True)
    fecha_ingreso = models.DateField(blank=True, null=True)
    peso = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    esterilizado = models.BooleanField(default=False)
    caracteristicas = models.TextField(blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)
    foto_url = models.CharField(max_length=255, blank=True, null=True)
    activo = models.BooleanField(default=True)

    # --- AGREGA ESTO ---
    class Meta:
        managed = False
        db_table = 'animal'

    def __str__(self):
        return self.nombre