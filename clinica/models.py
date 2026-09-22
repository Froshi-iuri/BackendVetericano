from django.db import models


class HistoriaClinica(models.Model):
    id_historia = models.AutoField(primary_key=True)
    id_animal = models.OneToOneField('animales.Animal', models.DO_NOTHING, db_column='id_animal', unique=True)
    numero_historia = models.CharField(max_length=50, blank=True, null=True)
    fecha_apertura = models.DateTimeField(auto_now_add=True)
    estado_general = models.CharField(max_length=50, blank=True, null=True)
    fecha_ultima_desparasitacion = models.DateField(blank=True, null=True)
    producto_desparasitacion = models.CharField(max_length=150, blank=True, null=True)
    vacunas = models.TextField(blank=True, null=True)
    enfermedades_anteriores = models.TextField(blank=True, null=True)
    tratamientos_anteriores = models.TextField(blank=True, null=True)
    evolucion_previa = models.TextField(blank=True, null=True)
    alimentacion = models.CharField(max_length=255, blank=True, null=True)
    estado_reproductivo = models.CharField(max_length=30, blank=True, null=True)
    fecha_ultimo_celo = models.DateField(blank=True, null=True)
    fecha_ultimo_parto = models.DateField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'historia_clinica'

    def __str__(self):
        return f"Historia Clínica #{self.id_historia} (Animal #{self.id_animal_id})"


class Consulta(models.Model):
    id_consulta = models.AutoField(primary_key=True)
    id_historia = models.ForeignKey(HistoriaClinica, models.DO_NOTHING, db_column='id_historia')
    fecha_hora = models.DateTimeField(auto_now_add=True)
    prioridad = models.CharField(max_length=20, blank=True, null=True)
    motivo_consulta = models.CharField(max_length=255, blank=True, null=True)
    anamnesis = models.TextField(blank=True, null=True)
    hallazgos_examen = models.TextField(blank=True, null=True)
    resultado_estado = models.CharField(max_length=100, blank=True, null=True)
    frecuencia_respiratoria = models.IntegerField(blank=True, null=True)
    frecuencia_cardiaca = models.IntegerField(blank=True, null=True)
    temperatura = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    pulso = models.CharField(max_length=50, blank=True, null=True)
    tllc = models.CharField(max_length=50, blank=True, null=True)
    ganglios_linfaticos = models.CharField(max_length=100, blank=True, null=True)
    mucosas = models.CharField(max_length=100, blank=True, null=True)
    actitud_temperamento = models.CharField(max_length=50, blank=True, null=True)
    evaluacion_sistemas = models.JSONField(blank=True, null=True)
    observaciones_examen = models.TextField(blank=True, null=True)
    materiales_utilizados = models.TextField(blank=True, null=True)
    ingresa_cba = models.BooleanField(default=False)

    class Meta:
        managed = False
        db_table = 'consulta'

    def __str__(self):
        return f"Consulta #{self.id_consulta} (Historia #{self.id_historia_id})"


class HospitalizacionSeresSintientes(models.Model):
    id_hospitalizacion = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    descripcion_estado = models.TextField(blank=True, null=True)
    fecha_ingreso = models.DateTimeField(auto_now_add=True)
    diagnostico_presuntivo = models.TextField(blank=True, null=True)
    responsable_clinico = models.CharField(max_length=150, blank=True, null=True)
    hoja_numero = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'hospitalizacion_seres_sintientes'

    def __str__(self):
        return f"Hospitalización #{self.id_hospitalizacion}"


class Tratamientos(models.Model):
    id_tratamiento = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    descripcion = models.CharField(max_length=255, blank=True, null=True)
    producto_base = models.CharField(max_length=150, blank=True, null=True)
    dosis_basica = models.CharField(max_length=100, blank=True, null=True)
    presentacion = models.CharField(max_length=100, blank=True, null=True)
    via_administracion = models.CharField(max_length=100, blank=True, null=True)
    frecuencia_duracion = models.CharField(max_length=100, blank=True, null=True)
    materiales_utilizados = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tratamientos'

    def __str__(self):
        return f"Tratamiento #{self.id_tratamiento}"


class Diagnostico(models.Model):
    id_diagnostico = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    descripcion = models.TextField(blank=True, null=True)
    tipo_diagnostico = models.CharField(max_length=50, blank=True, null=True)
    lista_problemas = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'diagnostico'

    def __str__(self):
        return f"Diagnóstico #{self.id_diagnostico}"


class SeguimientosClinicos(models.Model):
    id_seguimiento = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    evolucion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)
    temperatura = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    frecuencia_cardiaca = models.IntegerField(blank=True, null=True)
    frecuencia_respiratoria = models.IntegerField(blank=True, null=True)
    peso = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    plan_tratamiento = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'seguimientos_clinicos'

    def __str__(self):
        return f"Seguimiento Clínico #{self.id_seguimiento}"


class DiagnosticoPatologia(models.Model):
    id_diagnostico_patologia = models.AutoField(primary_key=True)
    id_diagnostico = models.ForeignKey(Diagnostico, models.DO_NOTHING, db_column='id_diagnostico')
    id_patologia = models.ForeignKey('patologias.Patologia', models.DO_NOTHING, db_column='id_patologia')
    hallazgos_pruebas = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'diagnostico_patologia'

    def __str__(self):
        return f"Diagnóstico {self.id_diagnostico_id} - Patología {self.id_patologia_id}"


class SeguimientoHospitalario(models.Model):
    id_seguimiento_hosp = models.AutoField(primary_key=True)
    id_hospitalizacion = models.ForeignKey(HospitalizacionSeresSintientes, models.DO_NOTHING, db_column='id_hospitalizacion')
    evolucion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'seguimiento_hospitalario'

    def __str__(self):
        return f"Seguimiento Hosp #{self.id_seguimiento_hosp}"
