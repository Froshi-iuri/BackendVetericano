from django.db import models


class TipoPeticion(models.Model):
    id_tipo = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'tipo_peticion'

    def __str__(self):
        return self.nombre


class EstadoPeticiones(models.Model):
    id_estado = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    orden = models.IntegerField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'estado_peticiones'

    def __str__(self):
        return self.nombre


class Ubicaciones(models.Model):
    id_ubicacion = models.AutoField(primary_key=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    latitud = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    longitud = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'ubicaciones'

    def __str__(self):
        return self.direccion or f"Ubicación #{self.id_ubicacion}"


class Peticiones(models.Model):
    id_peticion = models.AutoField(primary_key=True)
    id_tipo = models.ForeignKey(TipoPeticion, models.DO_NOTHING, db_column='id_tipo')
    id_ubicacion = models.ForeignKey(Ubicaciones, models.DO_NOTHING, db_column='id_ubicacion', blank=True, null=True)
    id_estado = models.ForeignKey(EstadoPeticiones, models.DO_NOTHING, db_column='id_estado')
    responsable = models.ForeignKey('users.Usuarios', models.DO_NOTHING, db_column='responsable_id', blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)
    prioridad = models.CharField(max_length=20, blank=True, null=True)
    fecha_asignacion = models.DateTimeField(blank=True, null=True)
    foto = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'peticiones'

    def __str__(self):
        return f"Petición #{self.id_peticion}"


class EvidenciaPeticiones(models.Model):
    id_evidencia = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(Peticiones, models.DO_NOTHING, db_column='id_peticion')
    ruta_archivo = models.CharField(max_length=500, blank=True, null=True)
    descripcion = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'evidencia_peticiones'

    def __str__(self):
        return f"Evidencia #{self.id_evidencia} (Petición #{self.id_peticion_id})"


class SeguimientoPeticionesVisita(models.Model):
    id_seguimiento = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(Peticiones, models.DO_NOTHING, db_column='id_peticion')
    id_veterinario = models.ForeignKey('users.Usuarios', models.DO_NOTHING, db_column='id_veterinario')
    id_animal = models.ForeignKey('animales.Animal', models.DO_NOTHING, db_column='id_animal', blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)
    numero_radicado = models.CharField(max_length=100, blank=True, null=True)
    fecha_atencion = models.DateField(blank=True, null=True)
    quien_reporta = models.CharField(max_length=30, blank=True, null=True)
    quien_reporta_otro = models.CharField(max_length=255, blank=True, null=True)
    solicitud_atencion_por = models.CharField(max_length=255, blank=True, null=True)
    propietario_nombre = models.CharField(max_length=150, blank=True, null=True)
    propietario_cedula = models.CharField(max_length=20, blank=True, null=True)
    propietario_telefono = models.CharField(max_length=20, blank=True, null=True)
    propietario_email = models.CharField(max_length=100, blank=True, null=True)
    nro_animales_atendidos = models.IntegerField(blank=True, null=True)
    nombre_paciente = models.CharField(max_length=150, blank=True, null=True)
    paciente_especie = models.CharField(max_length=50, blank=True, null=True)
    paciente_sexo = models.CharField(max_length=10, blank=True, null=True)
    paciente_color = models.CharField(max_length=50, blank=True, null=True)
    paciente_raza = models.CharField(max_length=50, blank=True, null=True)
    paciente_edad = models.CharField(max_length=50, blank=True, null=True)
    peso_paciente = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    esterilizacion_paciente = models.BooleanField(blank=True, null=True)
    descripcion_paciente = models.TextField(blank=True, null=True)
    desparasitacion = models.BooleanField(blank=True, null=True)
    anamnesis_descripcion_queja = models.TextField(blank=True, null=True)
    tratamiento_realizado = models.TextField(blank=True, null=True)
    pruebas_complementarias = models.CharField(max_length=255, blank=True, null=True)
    resultado_pruebas = models.TextField(blank=True, null=True)
    compromisos = models.TextField(blank=True, null=True)
    fundamento_legal = models.TextField(blank=True, null=True)
    plazo_dias_cumplimiento = models.IntegerField(blank=True, null=True)
    nombre_funcionario = models.CharField(max_length=150, blank=True, null=True)
    funcionario_cargo = models.CharField(max_length=150, blank=True, null=True)
    fecha_notificacion = models.DateField(blank=True, null=True)
    notificado_nombre = models.CharField(max_length=150, blank=True, null=True)
    notificaciones_identificacion = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'seguimiento_peticiones_visita'

    def __str__(self):
        return f"Seguimiento Visita #{self.id_seguimiento} (Petición #{self.id_peticion_id})"


class VisitaAnimal(models.Model):
    id_visita = models.AutoField(primary_key=True)
    id_animal = models.ForeignKey('animales.Animal', models.DO_NOTHING, db_column='id_animal')
    id_seguimiento = models.ForeignKey(SeguimientoPeticionesVisita, models.DO_NOTHING, db_column='id_seguimiento', blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)
    nro_radicado_atencion = models.CharField(max_length=100, blank=True, null=True)
    quien_reporta = models.CharField(max_length=255, blank=True, null=True)
    lugar_atencion_direccion = models.CharField(max_length=255, blank=True, null=True)
    anamnesis_queja = models.TextField(blank=True, null=True)
    atencion_tratamiento_campo = models.TextField(blank=True, null=True)
    desparasitacion_campo = models.BooleanField(blank=True, null=True)
    pruebas_rapidas_resultado = models.TextField(blank=True, null=True)
    compromisos = models.TextField(blank=True, null=True)
    plazo_dias = models.IntegerField(blank=True, null=True)
    nombre_notificado = models.CharField(max_length=150, blank=True, null=True)
    documento_notificado = models.CharField(max_length=50, blank=True, null=True)
    fecha_notificacion = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'visita_animal'

    def __str__(self):
        return f"Visita #{self.id_visita} (Animal #{self.id_animal_id})"
