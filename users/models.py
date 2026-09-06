# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models
from django.contrib.auth.models import AbstractUser


class ActividadVoluntario(models.Model):
    id_actividad_asignada = models.AutoField(primary_key=True)
    id_voluntario = models.ForeignKey('Voluntarios', models.DO_NOTHING, db_column='id_voluntario')
    id_tipo_actividad = models.ForeignKey('TiposActividad', models.DO_NOTHING, db_column='id_tipo_actividad')
    id_animal = models.ForeignKey('Animal', models.DO_NOTHING, db_column='id_animal', blank=True, null=True)
    fecha = models.DateField()
    hora = models.TimeField(blank=True, null=True)
    estado = models.CharField(max_length=30)

    class Meta:
        managed = False
        db_table = 'actividad_voluntario'


class Alertas(models.Model):
    id_alerta = models.AutoField(primary_key=True)
    tipo_alerta = models.CharField(max_length=50)
    entidad_tipo = models.CharField(max_length=50)
    entidad_id = models.IntegerField()
    mensaje = models.TextField(blank=True, null=True)
    fecha_generada = models.DateTimeField()
    atendida = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'alertas'


class Animal(models.Model):
    id_animal = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    id_especie = models.ForeignKey('Especie', models.DO_NOTHING, db_column='id_especie')
    id_raza = models.ForeignKey('Raza', models.DO_NOTHING, db_column='id_raza', blank=True, null=True)
    id_procedencia = models.ForeignKey('Procedencias', models.DO_NOTHING, db_column='id_procedencia', blank=True, null=True)
    id_estado = models.ForeignKey('EstadosAnimal', models.DO_NOTHING, db_column='id_estado')
    sexo = models.CharField(max_length=10, blank=True, null=True)
    fecha_ingreso = models.DateField(blank=True, null=True)
    caracteristicas = models.TextField(blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    foto = models.CharField(max_length=255, blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'animal'


class AnimalResponsable(models.Model):
    id_animal_responsable = models.AutoField(primary_key=True)
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal')
    id_responsable = models.ForeignKey('Responsables', models.DO_NOTHING, db_column='id_responsable')
    tipo_responsabilidad = models.CharField(max_length=50)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'animal_responsable'


class ConsultaDiagnosticoPatologia(models.Model):
    id_consulta_patologia = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey('Consultas', models.DO_NOTHING, db_column='id_consulta')
    id_patologia = models.ForeignKey('Patologias', models.DO_NOTHING, db_column='id_patologia')
    diagnostico = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'consulta_diagnostico_patologia'


class Consultas(models.Model):
    id_consulta = models.AutoField(primary_key=True)
    id_historia = models.ForeignKey('HistoriaClinica', models.DO_NOTHING, db_column='id_historia')
    id_veterinario = models.ForeignKey('Veterinarios', models.DO_NOTHING, db_column='id_veterinario')
    fecha_hora = models.DateTimeField()
    motivo_consulta = models.CharField(max_length=255, blank=True, null=True)
    anamesis = models.TextField(blank=True, null=True)
    hallazgos_examen = models.TextField(blank=True, null=True)
    resultado_estado = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'consultas'


class Entradas(models.Model):
    id_entrada = models.AutoField(primary_key=True)
    id_medicamento = models.ForeignKey('Medicamentos', models.DO_NOTHING, db_column='id_medicamento')
    id_proveedor = models.ForeignKey('Proveedores', models.DO_NOTHING, db_column='id_proveedor')
    cantidad = models.IntegerField()
    fecha_entrada = models.DateTimeField()
    lote = models.CharField(max_length=50, blank=True, null=True)
    fecha_vencimiento = models.DateField(blank=True, null=True)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'entradas'


class Especie(models.Model):
    id_especie = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'especie'


class EstadosAnimal(models.Model):
    id_estado = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'estados_animal'


class EstadosPeticion(models.Model):
    id_estado = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    orden = models.IntegerField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'estados_peticion'


class EvidenciasPeticion(models.Model):
    id_evidencia = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey('Peticiones', models.DO_NOTHING, db_column='id_peticion')
    nombre_archivo = models.CharField(max_length=255, blank=True, null=True)
    ruta_archivo = models.CharField(max_length=500, blank=True, null=True)
    tipo_archivo = models.CharField(max_length=50, blank=True, null=True)
    fecha_subida = models.DateTimeField()
    id_usuario = models.ForeignKey('Usuarios', models.DO_NOTHING, db_column='id_usuario', blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'evidencias_peticion'


class FormatoSeresSintientes(models.Model):
    id_formato = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey('Peticiones', models.DO_NOTHING, db_column='id_peticion')
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal', blank=True, null=True)
    id_veterinario = models.ForeignKey('Veterinarios', models.DO_NOTHING, db_column='id_veterinario')
    fecha_atencion = models.DateTimeField()
    descripcion_estado = models.TextField(blank=True, null=True)
    diagnostico_breve = models.TextField(blank=True, null=True)
    acciones_realizadas = models.TextField(blank=True, null=True)
    recomendaciones = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'formato_seres_sintientes'


class HistoriaClinica(models.Model):
    id_historia = models.AutoField(primary_key=True)
    id_animal = models.OneToOneField(Animal, models.DO_NOTHING, db_column='id_animal')
    id_veterinario = models.ForeignKey('Veterinarios', models.DO_NOTHING, db_column='id_veterinario')
    fecha_apertura = models.DateTimeField()
    estado_general = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'historia_clinica'


class HistorialEstadoAnimal(models.Model):
    id_historial = models.AutoField(primary_key=True)
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal')
    id_estado = models.ForeignKey(EstadosAnimal, models.DO_NOTHING, db_column='id_estado')
    fecha_inicio = models.DateTimeField()
    fecha_fin = models.DateTimeField(blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'historial_estado_animal'


class Inventario(models.Model):
    id_inventario = models.AutoField(primary_key=True)
    id_medicamento = models.OneToOneField('Medicamentos', models.DB_CASCADE, db_column='id_medicamento')
    cantidad_disponible = models.IntegerField()
    stock_minimo = models.IntegerField()
    stock_maximo = models.IntegerField(blank=True, null=True)
    ubicacion = models.CharField(max_length=150, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'inventario'


class Medicamentos(models.Model):
    id_medicamento = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    principio_activo = models.CharField(max_length=150, blank=True, null=True)
    presentacion = models.CharField(max_length=100, blank=True, null=True)
    concentracion = models.CharField(max_length=50, blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'medicamentos'


class Patologias(models.Model):
    id_patologia = models.AutoField(primary_key=True)
    codigo_cie = models.CharField(max_length=20, blank=True, null=True)
    nombre = models.CharField(max_length=150)

    class Meta:
        managed = False
        db_table = 'patologias'


class Peticiones(models.Model):
    id_peticion = models.AutoField(primary_key=True)
    ciudadano_id = models.IntegerField(blank=True, null=True)
    id_tipo = models.ForeignKey('TiposPeticion', models.DO_NOTHING, db_column='id_tipo')
    id_estado = models.ForeignKey(EstadosPeticion, models.DO_NOTHING, db_column='id_estado')
    id_ubicacion = models.ForeignKey('Ubicaciones', models.DO_NOTHING, db_column='id_ubicacion', blank=True, null=True)
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal', blank=True, null=True)
    responsable = models.ForeignKey('Usuarios', models.DO_NOTHING, blank=True, null=True)
    fecha_creacion = models.DateTimeField()
    descripcion = models.TextField(blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    prioridad = models.CharField(max_length=20, blank=True, null=True)
    fecha_asignacion = models.DateTimeField(blank=True, null=True)
    fecha_respuesta = models.DateTimeField(blank=True, null=True)
    fecha_cierre = models.DateTimeField(blank=True, null=True)
    respuesta = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'peticiones'


class Procedencias(models.Model):
    id_procedencia = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'procedencias'


class ProcedimientosCatalogo(models.Model):
    id_procedimiento_catalogo = models.AutoField(primary_key=True)
    nombre_tipo = models.CharField(max_length=150)

    class Meta:
        managed = False
        db_table = 'procedimientos_catalogo'


class ProcedimientosRealizados(models.Model):
    id_procedimiento_realizado = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consultas, models.DO_NOTHING, db_column='id_consulta')
    id_procedimiento_catalogo = models.ForeignKey(ProcedimientosCatalogo, models.DO_NOTHING, db_column='id_procedimiento_catalogo')
    resultado_anexo_url = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'procedimientos_realizados'


class Proveedores(models.Model):
    id_proveedor = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    contacto = models.CharField(max_length=150, blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'proveedores'


class Raza(models.Model):
    id_raza = models.AutoField(primary_key=True)
    id_especie = models.ForeignKey(Especie, models.DO_NOTHING, db_column='id_especie')
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'raza'


class Responsables(models.Model):
    id_responsable = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    documento = models.CharField(unique=True, max_length=30)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    correo = models.CharField(max_length=150, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'responsables'


class Rol(models.Model):
    id_rol = models.AutoField(primary_key=True)
    nombre_rol = models.CharField(unique=True, max_length=50)

    class Meta:
        managed = False
        db_table = 'rol'


class Salidas(models.Model):
    id_salida = models.AutoField(primary_key=True)
    id_medicamento = models.ForeignKey(Medicamentos, models.DO_NOTHING, db_column='id_medicamento')
    id_tratamiento_aplicado = models.ForeignKey('TratamientosAplicados', models.DO_NOTHING, db_column='id_tratamiento_aplicado', blank=True, null=True)
    cantidad = models.IntegerField()
    fecha_salida = models.DateTimeField()
    tipo_salida = models.CharField(max_length=30)
    usuario_responsable = models.ForeignKey('Usuarios', models.DO_NOTHING, db_column='usuario_responsable', blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'salidas'


class SeguimientoPeticiones(models.Model):
    id_seguimiento = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(Peticiones, models.DO_NOTHING, db_column='id_peticion')
    id_usuario = models.ForeignKey('Usuarios', models.DO_NOTHING, db_column='id_usuario')
    id_estado = models.ForeignKey(EstadosPeticion, models.DO_NOTHING, db_column='id_estado')
    fecha = models.DateTimeField()
    observacion = models.TextField(blank=True, null=True)
    accion_realizada = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'seguimiento_peticiones'


class SeguimientosClinicos(models.Model):
    id_seguimiento = models.AutoField(primary_key=True)
    id_historia = models.ForeignKey(HistoriaClinica, models.DO_NOTHING, db_column='id_historia')
    id_consulta_origen = models.ForeignKey(Consultas, models.DO_NOTHING, db_column='id_consulta_origen', blank=True, null=True)
    id_veterinario = models.ForeignKey('Veterinarios', models.DO_NOTHING, db_column='id_veterinario')
    fecha_hora = models.DateTimeField()
    evolucion = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'seguimientos_clinicos'


class TiposActividad(models.Model):
    id_tipo_actividad = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tipos_actividad'


class TiposPeticion(models.Model):
    id_tipo = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'tipos_peticion'


class TratamientosAplicados(models.Model):
    id_tratamiento_aplicado = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consultas, models.DO_NOTHING, db_column='id_consulta')
    id_tratamiento_catalogo = models.ForeignKey('TratamientosCatalogo', models.DO_NOTHING, db_column='id_tratamiento_catalogo')
    dosis_frecuencia_duracion = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tratamientos_aplicados'


class TratamientosCatalogo(models.Model):
    id_tratamiento_catalogo = models.AutoField(primary_key=True)
    nombre_tipo = models.CharField(max_length=150)

    class Meta:
        managed = False
        db_table = 'tratamientos_catalogo'


class Ubicaciones(models.Model):
    id_ubicacion = models.AutoField(primary_key=True)
    ciudad = models.CharField(max_length=100, blank=True, null=True)
    municipio = models.CharField(max_length=100, blank=True, null=True)
    barrio = models.CharField(max_length=100, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    latitud = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    longitud = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'ubicaciones'


class Usuarios(models.Model):
    id_usuario = models.AutoField(primary_key=True)
    email = models.CharField(unique=True, max_length=150)
    password = models.CharField(max_length=255)
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    id_rol = models.ForeignKey(Rol, models.DO_NOTHING, db_column='id_rol')
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'usuarios'

    def save(self, *args, **kwargs):
        # Normalizamos el nombre del rol a minúsculas para evitar errores (Ej: 'Administrador' vs 'administrador')
        nombre_rol_actual = self.id_rol.nombre_rol.lower()

        # Si el rol es crítico, verificamos si ya existe otro usuario con ese mismo rol
        if nombre_rol_actual in ['administrador', 'juridico']:
            # Buscamos si existe alguien con este rol, excluyendo al usuario actual (por si estamos actualizando sus datos)
            existe = Usuarios.objects.filter(id_rol=self.id_rol).exclude(id_usuario=self.id_usuario).exists()
            
            if existe:
                raise ValidationError(f"Ya existe un usuario registrado con el rol de {self.id_rol.nombre_rol}. Solo se permite uno.")
        
        super().save(*args, **kwargs)


class Veterinarios(models.Model):
    id_veterinario = models.AutoField(primary_key=True)
    id_usuario = models.OneToOneField(Usuarios, models.DO_NOTHING, db_column='id_usuario', blank=True, null=True)
    nombre = models.CharField(max_length=150)
    tarjeta_profesional = models.CharField(unique=True, max_length=50)

    class Meta:
        managed = False
        db_table = 'veterinarios'


class Voluntarios(models.Model):
    id_voluntario = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    documento = models.CharField(unique=True, max_length=30)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    email = models.CharField(max_length=150, blank=True, null=True)
    fecha_postulacion = models.DateField()
    estado = models.CharField(max_length=30)

    class Meta:
        managed = False
        db_table = 'voluntarios'

