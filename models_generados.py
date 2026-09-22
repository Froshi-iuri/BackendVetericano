# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models


class AdministracionMedicamento(models.Model):
    id_administracion = models.AutoField(primary_key=True)
    id_medicamento = models.ForeignKey('Medicamentos', models.DO_NOTHING, db_column='id_medicamento')
    id_seguimiento_hosp = models.ForeignKey('SeguimientoHospitalario', models.DO_NOTHING, db_column='id_seguimiento_hosp')
    dosis_administrada = models.CharField(max_length=100, blank=True, null=True)
    fecha_hora = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'administracion_medicamento'


class AdopcionesAdopcionanimal(models.Model):
    id = models.BigAutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    raza = models.CharField(max_length=100)
    descripcion = models.TextField()
    imagen = models.CharField(max_length=100)
    disponible = models.BooleanField()
    fecha_creacion = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'adopciones_adopcionanimal'


class AdopcionesSolicitudadopcion(models.Model):
    id = models.BigAutoField(primary_key=True)
    nombre_adoptante = models.CharField(max_length=150)
    cedula = models.CharField(max_length=20)
    correo_adoptante = models.CharField(max_length=254)
    telefono = models.CharField(max_length=20)
    mensaje = models.TextField(blank=True, null=True)
    fecha_solicitud = models.DateTimeField()
    animal = models.ForeignKey(AdopcionesAdopcionanimal, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'adopciones_solicitudadopcion'


class Animal(models.Model):
    id_animal = models.AutoField(primary_key=True)
    id_raza = models.ForeignKey('Raza', models.DO_NOTHING, db_column='id_raza')
    nombre = models.CharField(max_length=100)
    sexo = models.CharField(max_length=10, blank=True, null=True)
    fecha_ingreso = models.DateField(blank=True, null=True)
    caracteristicas = models.TextField(blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)
    foto_url = models.CharField(max_length=255, blank=True, null=True)
    activo = models.BooleanField(blank=True, null=True)
    color = models.CharField(max_length=50, blank=True, null=True)
    fecha_nacimiento = models.DateField(blank=True, null=True)
    peso = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    esterilizado = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'animal'


class AnimalResponsable(models.Model):
    id_animal_responsable = models.AutoField(primary_key=True)
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal')
    id_responsable = models.ForeignKey('Responsables', models.DO_NOTHING, db_column='id_responsable')
    tipo_responsabilidad = models.CharField(max_length=50, blank=True, null=True)
    fecha_inicio = models.DateField(blank=True, null=True)
    fecha_fin = models.DateField(blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'animal_responsable'


class AuthGroup(models.Model):
    name = models.CharField(unique=True, max_length=150)

    class Meta:
        managed = False
        db_table = 'auth_group'


class AuthGroupPermissions(models.Model):
    id = models.BigAutoField(primary_key=True)
    group = models.ForeignKey(AuthGroup, models.DO_NOTHING)
    permission = models.ForeignKey('AuthPermission', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'auth_group_permissions'
        unique_together = (('group', 'permission'),)


class AuthPermission(models.Model):
    name = models.CharField(max_length=255)
    content_type = models.ForeignKey('DjangoContentType', models.DO_NOTHING)
    codename = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'auth_permission'
        unique_together = (('content_type', 'codename'),)


class AuthUser(models.Model):
    password = models.CharField(max_length=128)
    last_login = models.DateTimeField(blank=True, null=True)
    is_superuser = models.BooleanField()
    username = models.CharField(unique=True, max_length=150)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.CharField(max_length=254)
    is_staff = models.BooleanField()
    is_active = models.BooleanField()
    date_joined = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'auth_user'


class AuthUserGroups(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)
    group = models.ForeignKey(AuthGroup, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'auth_user_groups'
        unique_together = (('user', 'group'),)


class AuthUserUserPermissions(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)
    permission = models.ForeignKey(AuthPermission, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'auth_user_user_permissions'
        unique_together = (('user', 'permission'),)


class AuthtokenToken(models.Model):
    key = models.CharField(primary_key=True, max_length=40)
    created = models.DateTimeField()
    user = models.OneToOneField(AuthUser, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'authtoken_token'


class Compra(models.Model):
    id_compra = models.AutoField(primary_key=True)
    id_proveedor = models.ForeignKey('Proveedores', models.DO_NOTHING, db_column='id_proveedor')
    fecha = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'compra'


class Consulta(models.Model):
    id_consulta = models.AutoField(primary_key=True)
    id_historia = models.ForeignKey('HistoriaClinica', models.DO_NOTHING, db_column='id_historia')
    fecha_hora = models.DateTimeField(blank=True, null=True)
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
    ingresa_cba = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'consulta'


class DetalleCompra(models.Model):
    id_detalle_compra = models.AutoField(primary_key=True)
    id_compra = models.ForeignKey(Compra, models.DO_NOTHING, db_column='id_compra')
    id_medicamento = models.ForeignKey('Medicamentos', models.DO_NOTHING, db_column='id_medicamento')
    cantidad = models.IntegerField(blank=True, null=True)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'detalle_compra'


class DetalleSalida(models.Model):
    id_detalle_salida = models.AutoField(primary_key=True)
    id_usuario = models.ForeignKey('Usuarios', models.DO_NOTHING, db_column='id_usuario')
    id_administracion = models.ForeignKey(AdministracionMedicamento, models.DO_NOTHING, db_column='id_administracion')
    cantidad = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'detalle_salida'


class Diagnostico(models.Model):
    id_diagnostico = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    descripcion = models.TextField(blank=True, null=True)
    tipo_diagnostico = models.CharField(max_length=50, blank=True, null=True)
    lista_problemas = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'diagnostico'


class DiagnosticoPatologia(models.Model):
    id_diagnostico_patologia = models.AutoField(primary_key=True)
    id_diagnostico = models.ForeignKey(Diagnostico, models.DO_NOTHING, db_column='id_diagnostico')
    id_patologia = models.ForeignKey('Patologia', models.DO_NOTHING, db_column='id_patologia')
    hallazgos_pruebas = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'diagnostico_patologia'

        
class DjangoAdminLog(models.Model):
    action_time = models.DateTimeField()
    object_id = models.TextField(blank=True, null=True)
    object_repr = models.CharField(max_length=200)
    action_flag = models.SmallIntegerField()
    change_message = models.TextField()
    content_type = models.ForeignKey('DjangoContentType', models.DO_NOTHING, blank=True, null=True)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'django_admin_log'


class DjangoContentType(models.Model):
    app_label = models.CharField(max_length=100)
    model = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'django_content_type'
        unique_together = (('app_label', 'model'),)


class DjangoMigrations(models.Model):
    id = models.BigAutoField(primary_key=True)
    app = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    applied = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'django_migrations'


class DjangoSession(models.Model):
    session_key = models.CharField(primary_key=True, max_length=40)
    session_data = models.TextField()
    expire_date = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'django_session'


class Especies(models.Model):
    id_especie = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'especies'


class EstadoPeticiones(models.Model):
    id_estado = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    orden = models.IntegerField(blank=True, null=True)
    activo = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'estado_peticiones'


class EstadosAnimal(models.Model):
    id_estado_animal = models.AutoField(primary_key=True)
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal')
    nombre_estado = models.CharField(max_length=100)
    fecha_registro = models.DateTimeField()
    observacion = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'estados_animal'


class Eventos(models.Model):
    id_evento = models.AutoField(primary_key=True)
    id_tipo_evento = models.ForeignKey('TiposEventos', models.DO_NOTHING, db_column='id_tipo_evento')
    nombre = models.CharField(max_length=150, blank=True, null=True)
    fecha = models.DateTimeField(blank=True, null=True)
    descripcion = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'eventos'


class EvidenciaPeticiones(models.Model):
    id_evidencia = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey('Peticiones', models.DO_NOTHING, db_column='id_peticion')
    ruta_archivo = models.CharField(max_length=500, blank=True, null=True)
    descripcion = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'evidencia_peticiones'


class Examen(models.Model):
    id_examen = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta', blank=True, null=True)
    resultado = models.TextField(blank=True, null=True)
    nombre_tipo_examen = models.CharField(max_length=100, blank=True, null=True)
    solicitado = models.BooleanField(blank=True, null=True)
    descripcion_hallazgos = models.TextField(blank=True, null=True)
    ruta_archivo_resultado = models.CharField(max_length=500, blank=True, null=True)
    fecha_realizacion = models.DateTimeField(blank=True, null=True)
    estado = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'examen'


class HistoriaClinica(models.Model):
    id_historia = models.AutoField(primary_key=True)
    id_animal = models.OneToOneField(Animal, models.DO_NOTHING, db_column='id_animal')
    fecha_apertura = models.DateTimeField(blank=True, null=True)
    estado_general = models.CharField(max_length=50, blank=True, null=True)
    numero_historia = models.CharField(max_length=50, blank=True, null=True)
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


class HospitalizacionSeresSintientes(models.Model):
    id_hospitalizacion = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    descripcion_estado = models.TextField(blank=True, null=True)
    fecha_ingreso = models.DateTimeField(blank=True, null=True)
    diagnostico_presuntivo = models.TextField(blank=True, null=True)
    responsable_clinico = models.CharField(max_length=150, blank=True, null=True)
    hoja_numero = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'hospitalizacion_seres_sintientes'


class Inventarios(models.Model):
    id_inventario = models.AutoField(primary_key=True)
    id_detalle_salida = models.ForeignKey(DetalleSalida, models.DO_NOTHING, db_column='id_detalle_salida', blank=True, null=True)
    id_detalle_compra = models.ForeignKey(DetalleCompra, models.DO_NOTHING, db_column='id_detalle_compra', blank=True, null=True)
    cantidad_actual = models.IntegerField()
    fecha_vencimiento = models.DateField(blank=True, null=True)
    estado = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'inventarios'


class Medicamentos(models.Model):
    id_medicamento = models.AutoField(primary_key=True)
    id_salida = models.ForeignKey('Salidas', models.DO_NOTHING, db_column='id_salida', blank=True, null=True)
    nombre = models.CharField(max_length=150)
    principio_activo = models.CharField(max_length=150, blank=True, null=True)
    presentacion = models.CharField(max_length=100, blank=True, null=True)
    concentracion = models.CharField(max_length=50, blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(blank=True, null=True)
    cantidad_ml = models.CharField(max_length=50, blank=True, null=True)
    tipo = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'medicamentos'


class MedicamentosMedicamento(models.Model):
    id = models.BigAutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True, null=True)
    cantidad_ml = models.CharField(max_length=50)
    tipo = models.CharField(max_length=100)
    estado = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'medicamentos_medicamento'


class Patologia(models.Model):
    id_patologia = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'patologia'


class Peticiones(models.Model):
    id_peticion = models.AutoField(primary_key=True)
    id_tipo = models.ForeignKey('TipoPeticion', models.DO_NOTHING, db_column='id_tipo')
    id_ubicacion = models.ForeignKey('Ubicaciones', models.DO_NOTHING, db_column='id_ubicacion', blank=True, null=True)
    id_estado = models.ForeignKey(EstadoPeticiones, models.DO_NOTHING, db_column='id_estado')
    responsable = models.ForeignKey('Usuarios', models.DO_NOTHING)
    descripcion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(blank=True, null=True)
    prioridad = models.CharField(max_length=20, blank=True, null=True)
    fecha_asignacion = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'peticiones'


class ProcedimientoRealizado(models.Model):
    id_procedimiento = models.AutoField(primary_key=True)
    id_hospitalizacion = models.ForeignKey(HospitalizacionSeresSintientes, models.DO_NOTHING, db_column='id_hospitalizacion')
    descripcion = models.TextField(blank=True, null=True)
    fecha_procedimiento = models.DateField(blank=True, null=True)
    hora_procedimiento = models.TimeField(blank=True, null=True)
    medico_responsable = models.CharField(max_length=150, blank=True, null=True)
    tipo_procedimiento = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'procedimiento_realizado'


class Proveedores(models.Model):
    id_proveedor = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    activo = models.BooleanField(blank=True, null=True)
    email = models.CharField(max_length=150, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'proveedores'


class Raza(models.Model):
    id_raza = models.AutoField(primary_key=True)
    id_especie = models.ForeignKey(Especies, models.DO_NOTHING, db_column='id_especie')
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'raza'


class Responsables(models.Model):
    id_responsable = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    documento = models.CharField(max_length=30, blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    correo = models.CharField(max_length=150, blank=True, null=True)
    activo = models.BooleanField(blank=True, null=True)

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
    fecha = models.DateTimeField(blank=True, null=True)
    motivo = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'salidas'


class SeguimientoHospitalario(models.Model):
    id_seguimiento_hosp = models.AutoField(primary_key=True)
    id_hospitalizacion = models.ForeignKey(HospitalizacionSeresSintientes, models.DO_NOTHING, db_column='id_hospitalizacion')
    evolucion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'seguimiento_hospitalario'


class SeguimientoPeticionesVisita(models.Model):
    id_seguimiento = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(Peticiones, models.DO_NOTHING, db_column='id_peticion')
    id_veterinario = models.ForeignKey('Usuarios', models.DO_NOTHING, db_column='id_veterinario')
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal', blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(blank=True, null=True)
    numero_radicado = models.CharField(max_length=50, blank=True, null=True)
    fecha_atencion = models.DateField(blank=True, null=True)
    quien_reporta_otro = models.CharField(max_length=100, blank=True, null=True)
    nro_animales_atendidos = models.IntegerField(blank=True, null=True)
    nombre_paciente = models.CharField(max_length=50, blank=True, null=True)
    peso_paciente = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    esterilizacion_paciente = models.BooleanField(blank=True, null=True)
    descripcion_paciente = models.TextField(blank=True, null=True)
    desparasitacion = models.BooleanField(blank=True, null=True)
    pruebas_complementarias = models.CharField(max_length=150, blank=True, null=True)
    resultado_pruebas = models.TextField(blank=True, null=True)
    compromisos = models.TextField(blank=True, null=True)
    fundamento_legal = models.TextField(blank=True, null=True)
    plazo_dias_cumplimiento = models.IntegerField(blank=True, null=True)
    nombre_funcionario = models.CharField(max_length=150, blank=True, null=True)
    funcionario_cargo = models.CharField(max_length=100, blank=True, null=True)
    notificado_nombre = models.CharField(max_length=150, blank=True, null=True)
    notificaciones_identificacion = models.CharField(max_length=20, blank=True, null=True)
    id_ubicacion = models.ForeignKey('Ubicaciones', models.DO_NOTHING, db_column='id_ubicacion', blank=True, null=True)
    quien_reporta = models.CharField(max_length=30, blank=True, null=True)
    solicitud_atencion_por = models.CharField(max_length=255, blank=True, null=True)
    propietario_nombre = models.CharField(max_length=150, blank=True, null=True)
    propietario_cedula = models.CharField(max_length=20, blank=True, null=True)
    propietario_telefono = models.CharField(max_length=20, blank=True, null=True)
    propietario_email = models.CharField(max_length=100, blank=True, null=True)
    paciente_especie = models.CharField(max_length=50, blank=True, null=True)
    paciente_sexo = models.CharField(max_length=10, blank=True, null=True)
    paciente_color = models.CharField(max_length=50, blank=True, null=True)
    paciente_raza = models.CharField(max_length=50, blank=True, null=True)
    paciente_edad = models.CharField(max_length=50, blank=True, null=True)
    anamnesis_descripcion_queja = models.TextField(blank=True, null=True)
    tratamiento_realizado = models.TextField(blank=True, null=True)
    fecha_notificacion = models.DateField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'seguimiento_peticiones_visita'


class SeguimientosClinicos(models.Model):
    id_seguimiento = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    evolucion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(blank=True, null=True)
    temperatura = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    frecuencia_cardiaca = models.IntegerField(blank=True, null=True)
    frecuencia_respiratoria = models.IntegerField(blank=True, null=True)
    peso = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    plan_tratamiento = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'seguimientos_clinicos'


class TipoPeticion(models.Model):
    id_tipo = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tipo_peticion'


class TiposEventos(models.Model):
    id_tipo_evento = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tipos_eventos'


class TratamientoMedicamentos(models.Model):
    id_tratamiento_medicamento = models.AutoField(primary_key=True)
    id_tratamiento = models.ForeignKey('Tratamientos', models.DO_NOTHING, db_column='id_tratamiento')
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


class Ubicaciones(models.Model):
    id_ubicacion = models.AutoField(primary_key=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    barrio = models.CharField(max_length=100, blank=True, null=True)
    ciudad = models.CharField(max_length=100, blank=True, null=True)
    vereda = models.CharField(max_length=100, blank=True, null=True)
    latitud = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    longitud = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'ubicaciones'


class Usuarios(models.Model):
    id_usuario = models.AutoField(primary_key=True)
    email = models.CharField(unique=True, max_length=150)
    password = models.CharField(max_length=128)
    nombre = models.CharField(max_length=150)
    apellido = models.CharField(max_length=100)
    id_rol = models.ForeignKey(Rol, models.DO_NOTHING, db_column='id_rol')
    activo = models.BooleanField()
    identificacion = models.CharField(max_length=20, blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'usuarios'


class VisitaAnimal(models.Model):
    id_visita = models.AutoField(primary_key=True)
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal')
    id_seguimiento = models.ForeignKey(SeguimientoPeticionesVisita, models.DO_NOTHING, db_column='id_seguimiento', blank=True, null=True)
    fecha = models.DateTimeField(blank=True, null=True)
    nro_radicado_atencion = models.CharField(max_length=50, blank=True, null=True)
    quien_reporta = models.CharField(max_length=50, blank=True, null=True)
    lugar_atencion_direccion = models.CharField(max_length=255, blank=True, null=True)
    anamnesis_queja = models.TextField(blank=True, null=True)
    atencion_tratamiento_campo = models.TextField(blank=True, null=True)
    desparasitacion_campo = models.BooleanField(blank=True, null=True)
    pruebas_rapidas_resultado = models.TextField(blank=True, null=True)
    compromisos = models.TextField(blank=True, null=True)
    plazo_dias = models.IntegerField(blank=True, null=True)
    nombre_notificado = models.CharField(max_length=150, blank=True, null=True)
    documento_notificado = models.CharField(max_length=30, blank=True, null=True)
    fecha_notificacion = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'visita_animal'


class VoluntariadoEventovoluntariado(models.Model):
    id = models.BigAutoField(primary_key=True)
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField()
    imagen = models.CharField(max_length=100)
    fecha = models.DateField()

    class Meta:
        managed = False
        db_table = 'voluntariado_eventovoluntariado'


class VoluntariadoPostulacionvoluntariado(models.Model):
    id = models.BigAutoField(primary_key=True)
    correo = models.CharField(max_length=254)
    identificacion = models.CharField(max_length=50)
    nombre_completo = models.CharField(max_length=150)
    edad = models.IntegerField()
    telefono = models.CharField(max_length=20)
    fecha_postulacion = models.DateTimeField()
    evento = models.ForeignKey(VoluntariadoEventovoluntariado, models.DO_NOTHING, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'voluntariado_postulacionvoluntariado'


class VoluntarioEvento(models.Model):
    id_voluntario_evento = models.AutoField(primary_key=True)
    id_usuario = models.ForeignKey(Usuarios, models.DO_NOTHING, db_column='id_usuario')
    id_evento = models.ForeignKey(Eventos, models.DO_NOTHING, db_column='id_evento')
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal', blank=True, null=True)
    fecha = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'voluntario_evento'
