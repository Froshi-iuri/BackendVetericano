from django.db import models


class Responsables(models.Model):
    id_responsable = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    documento = models.CharField(max_length=30, blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    correo = models.CharField(max_length=150, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'responsables'

    def __str__(self):
        return self.nombre


class Animal(models.Model):
    id_animal = models.AutoField(primary_key=True)
    id_raza = models.ForeignKey('especies.Raza', models.DO_NOTHING, db_column='id_raza')
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

    class Meta:
        managed = False
        db_table = 'animal'

    def __str__(self):
        return self.nombre


class EstadosAnimal(models.Model):
    id_estado_animal = models.AutoField(primary_key=True)
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal')
    nombre_estado = models.CharField(max_length=100)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    observacion = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'estados_animal'

    def __str__(self):
        return f"{self.nombre_estado} (Animal #{self.id_animal_id})"


class AnimalResponsable(models.Model):
    id_animal_responsable = models.AutoField(primary_key=True)
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal')
    id_responsable = models.ForeignKey(Responsables, models.DO_NOTHING, db_column='id_responsable')
    tipo_responsabilidad = models.CharField(max_length=50, blank=True, null=True)
    fecha_inicio = models.DateField(blank=True, null=True)
    fecha_fin = models.DateField(blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'animal_responsable'

    def __str__(self):
        return f"Animal #{self.id_animal_id} - Responsable #{self.id_responsable_id}"
