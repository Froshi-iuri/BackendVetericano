from django.db import models
from django.core.exceptions import ValidationError


# ==========================================
# 1. CATÁLOGOS BASE
# ==========================================

class Especies(models.Model):
    id_especie = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'especies'

    def __str__(self):
        return self.nombre


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


class Rol(models.Model):
    id_rol = models.AutoField(primary_key=True)
    nombre_rol = models.CharField(max_length=50)

    class Meta:
        managed = False
        db_table = 'rol'

    def __str__(self):
        return self.nombre_rol


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


class Ubicaciones(models.Model):
    id_ubicacion = models.AutoField(primary_key=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    barrio = models.CharField(max_length=100, blank=True, null=True)
    ciudad = models.CharField(max_length=100, blank=True, null=True)
    municipio = models.CharField(max_length=100, blank=True, null=True)
    latitud = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    longitud = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'ubicaciones'

    def __str__(self):
        return f"{self.direccion or 'Sin dirección'} - {self.ciudad or 'Sin ciudad'}"


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


class TiposEventos(models.Model):
    id_tipo_evento = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'tipos_eventos'

    def __str__(self):
        return self.nombre


class Proveedores(models.Model):
    id_proveedor = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    contacto = models.CharField(max_length=150, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'proveedores'

    def __str__(self):
        return self.nombre


class Patologia(models.Model):
    id_patologia = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    codigo_cie = models.CharField(max_length=20, blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'patologia'

    def __str__(self):
        return self.nombre


# ==========================================
# 2. MÓDULO ANIMAL Y USUARIOS
# ==========================================

class Raza(models.Model):
    id_raza = models.AutoField(primary_key=True)
    id_especie = models.ForeignKey(Especies, models.DO_NOTHING, db_column='id_especie')
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
    fecha_ingreso = models.DateField(blank=True, null=True)
    caracteristicas = models.TextField(blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    foto = models.CharField(max_length=255, blank=True, null=True)
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


class Usuarios(models.Model):
    id_usuario = models.AutoField(primary_key=True)
    id_rol = models.ForeignKey(Rol, models.DO_NOTHING, db_column='id_rol')
    nombre = models.CharField(max_length=150)
    apellido = models.CharField(max_length=100, blank=True, default='')
    identificacion = models.CharField(unique=True, max_length=20, blank=True, null=True)
    email = models.EmailField(unique=True, max_length=150)
    password = models.CharField(max_length=255)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'usuarios'

    def save(self, *args, **kwargs):
        nombre_rol_actual = self.id_rol.nombre_rol.lower()
        if nombre_rol_actual in ['administrador', 'juridico']:
            existe = Usuarios.objects.filter(id_rol=self.id_rol).exclude(id_usuario=self.id_usuario).exists()
            if existe:
                raise ValidationError(f"Ya existe un usuario registrado con el rol de {self.id_rol.nombre_rol}. Solo se permite uno.")
        super().save(*args, **kwargs)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def __str__(self):
        return f"{self.nombre} ({self.email})"


# ==========================================
# 3. MÓDULO HISTORIA CLÍNICA Y ATENCIÓN
# ==========================================

class HistoriaClinica(models.Model):
    id_historia = models.AutoField(primary_key=True)
    id_animal = models.OneToOneField(Animal, models.DO_NOTHING, db_column='id_animal', unique=True)
    fecha_apertura = models.DateTimeField(auto_now_add=True)
    estado_general = models.CharField(max_length=50, blank=True, null=True)

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

    class Meta:
        managed = False
        db_table = 'hospitalizacion_seres_sintientes'

    def __str__(self):
        return f"Hospitalización #{self.id_hospitalizacion}"


class Tratamientos(models.Model):
    id_tratamiento = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    descripcion = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tratamientos'

    def __str__(self):
        return f"Tratamiento #{self.id_tratamiento}"


class Diagnostico(models.Model):
    id_diagnostico = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    descripcion = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'diagnostico'

    def __str__(self):
        return f"Diagnóstico #{self.id_diagnostico}"


class Examen(models.Model):
    id_examen = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    resultado = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'examen'

    def __str__(self):
        return f"Examen #{self.id_examen}"


class SeguimientosClinicos(models.Model):
    id_seguimiento = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey(Consulta, models.DO_NOTHING, db_column='id_consulta')
    evolucion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'seguimientos_clinicos'

    def __str__(self):
        return f"Seguimiento Clínico #{self.id_seguimiento}"


class DiagnosticoPatologia(models.Model):
    id_diagnostico_patologia = models.AutoField(primary_key=True)
    id_diagnostico = models.ForeignKey(Diagnostico, models.DO_NOTHING, db_column='id_diagnostico')
    id_patologia = models.ForeignKey(Patologia, models.DO_NOTHING, db_column='id_patologia')

    class Meta:
        managed = False
        db_table = 'diagnostico_patologia'

    def __str__(self):
        return f"Diagnóstico {self.id_diagnostico_id} - Patología {self.id_patologia_id}"


class ProcedimientoRealizado(models.Model):
    id_procedimiento = models.AutoField(primary_key=True)
    id_hospitalizacion = models.ForeignKey(HospitalizacionSeresSintientes, models.DO_NOTHING, db_column='id_hospitalizacion')
    descripcion = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'procedimiento_realizado'

    def __str__(self):
        return f"Procedimiento #{self.id_procedimiento}"


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


# ==========================================
# 4. MÓDULO MEDICAMENTOS / COMPRAS / INVENTARIO
# ==========================================

class Compra(models.Model):
    id_compra = models.AutoField(primary_key=True)
    id_proveedor = models.ForeignKey(Proveedores, models.DO_NOTHING, db_column='id_proveedor')
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'compra'

    def __str__(self):
        return f"Compra #{self.id_compra}"


class Salidas(models.Model):
    id_salida = models.AutoField(primary_key=True)
    fecha = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'salidas'

    def __str__(self):
        return f"Salida #{self.id_salida}"


class Medicamentos(models.Model):
    id_medicamento = models.AutoField(primary_key=True)
    id_salida = models.ForeignKey(Salidas, models.DO_NOTHING, db_column='id_salida', blank=True, null=True)
    nombre = models.CharField(max_length=150)
    principio_activo = models.CharField(max_length=150, blank=True, null=True)
    presentacion = models.CharField(max_length=100, blank=True, null=True)
    concentracion = models.CharField(max_length=50, blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'medicamentos'

    def __str__(self):
        return self.nombre


class DetalleCompra(models.Model):
    id_detalle_compra = models.AutoField(primary_key=True)
    id_compra = models.ForeignKey(Compra, models.DO_NOTHING, db_column='id_compra')
    id_medicamento = models.ForeignKey(Medicamentos, models.DO_NOTHING, db_column='id_medicamento')
    cantidad = models.IntegerField(blank=True, null=True)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'detalle_compra'

    def __str__(self):
        return f"Detalle Compra #{self.id_detalle_compra}"


class AdministracionMedicamento(models.Model):
    id_administracion = models.AutoField(primary_key=True)
    id_medicamento = models.ForeignKey(Medicamentos, models.DO_NOTHING, db_column='id_medicamento')
    id_seguimiento_hosp = models.ForeignKey(SeguimientoHospitalario, models.DO_NOTHING, db_column='id_seguimiento_hosp')
    dosis_administrada = models.CharField(max_length=100, blank=True, null=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'administracion_medicamento'

    def __str__(self):
        return f"Administración #{self.id_administracion}"


class DetalleSalida(models.Model):
    id_detalle_salida = models.AutoField(primary_key=True)
    id_usuario = models.ForeignKey(Usuarios, models.DO_NOTHING, db_column='id_usuario')
    id_administracion = models.ForeignKey(AdministracionMedicamento, models.DO_NOTHING, db_column='id_administracion')
    cantidad = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'detalle_salida'

    def __str__(self):
        return f"Detalle Salida #{self.id_detalle_salida}"


class Inventarios(models.Model):
    id_inventario = models.AutoField(primary_key=True)
    id_detalle_salida = models.ForeignKey(DetalleSalida, models.DO_NOTHING, db_column='id_detalle_salida', blank=True, null=True)
    id_detalle_compra = models.ForeignKey(DetalleCompra, models.DO_NOTHING, db_column='id_detalle_compra', blank=True, null=True)
    cantidad_actual = models.IntegerField(default=0)

    class Meta:
        managed = False
        db_table = 'inventarios'

    def __str__(self):
        return f"Inventario #{self.id_inventario} (Stock: {self.cantidad_actual})"


class TratamientoMedicamentos(models.Model):
    id_tratamiento_medicamento = models.AutoField(primary_key=True)
    id_tratamiento = models.ForeignKey(Tratamientos, models.DO_NOTHING, db_column='id_tratamiento')
    id_medicamento = models.ForeignKey(Medicamentos, models.DO_NOTHING, db_column='id_medicamento')
    dosis = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tratamiento_medicamentos'

    def __str__(self):
        return f"Tratamiento #{self.id_tratamiento_id} - Medicamento #{self.id_medicamento_id}"


# ==========================================
# 5. MÓDULO PETICIONES Y VISITAS
# ==========================================

class Peticiones(models.Model):
    id_peticion = models.AutoField(primary_key=True)
    id_tipo = models.ForeignKey(TipoPeticion, models.DO_NOTHING, db_column='id_tipo')
    id_ubicacion = models.ForeignKey(Ubicaciones, models.DO_NOTHING, db_column='id_ubicacion', blank=True, null=True)
    id_estado = models.ForeignKey(EstadoPeticiones, models.DO_NOTHING, db_column='id_estado')
    responsable = models.ForeignKey(Usuarios, models.DO_NOTHING, db_column='responsable_id', blank=True, null=True)
    ciudadano_id = models.IntegerField(blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    prioridad = models.CharField(max_length=20, blank=True, null=True)
    fecha_creacion = models.DateTimeField(blank=True, null=True)
    fecha_asignacion = models.DateTimeField(blank=True, null=True)
    fecha_respuesta = models.DateTimeField(blank=True, null=True)
    fecha_cierre = models.DateTimeField(blank=True, null=True)
    respuesta = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'peticiones'

    def __str__(self):
        return f"Petición #{self.id_peticion}"


class EvidenciaPeticiones(models.Model):
    id_evidencia = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(Peticiones, models.DO_NOTHING, db_column='id_peticion')
    ruta_archivo = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'evidencia_peticiones'

    def __str__(self):
        return f"Evidencia #{self.id_evidencia} (Petición #{self.id_peticion_id})"


class SeguimientoPeticionesVisita(models.Model):
    id_seguimiento = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(Peticiones, models.DO_NOTHING, db_column='id_peticion')
    id_veterinario = models.ForeignKey(Usuarios, models.DO_NOTHING, db_column='id_veterinario')
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal', blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'seguimiento_peticiones_visita'

    def __str__(self):
        return f"Seguimiento Visita #{self.id_seguimiento} (Petición #{self.id_peticion_id})"


class VisitaAnimal(models.Model):
    id_visita = models.AutoField(primary_key=True)
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal')
    id_seguimiento = models.ForeignKey(SeguimientoPeticionesVisita, models.DO_NOTHING, db_column='id_seguimiento', blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'visita_animal'

    def __str__(self):
        return f"Visita #{self.id_visita} (Animal #{self.id_animal_id})"


# ==========================================
# 6. MÓDULO EVENTOS Y VOLUNTARIADO
# ==========================================

class Eventos(models.Model):
    id_evento = models.AutoField(primary_key=True)
    id_tipo_evento = models.ForeignKey(TiposEventos, models.DO_NOTHING, db_column='id_tipo_evento')
    nombre = models.CharField(max_length=150, blank=True, null=True)
    fecha = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'eventos'

    def __str__(self):
        return self.nombre or f"Evento #{self.id_evento}"


class VoluntarioEvento(models.Model):
    id_voluntario_evento = models.AutoField(primary_key=True)
    id_usuario = models.ForeignKey(Usuarios, models.DO_NOTHING, db_column='id_usuario')
    id_evento = models.ForeignKey(Eventos, models.DO_NOTHING, db_column='id_evento')
    id_animal = models.ForeignKey(Animal, models.DO_NOTHING, db_column='id_animal', blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'voluntario_evento'

    def __str__(self):
        return f"Voluntario #{self.id_usuario_id} - Evento #{self.id_evento_id}"


# ==========================================
# ALIASES DE COMPATIBILIDAD HACIA ATRÁS
# ==========================================
Especie = Especies
TiposPeticion = TipoPeticion
EstadosPeticion = EstadoPeticiones
EstadoDePeticiones = EstadoPeticiones
Consultas = Consulta
Patologias = Patologia
Inventario = Inventarios
SeguimientoPeticiones = SeguimientoPeticionesVisita
