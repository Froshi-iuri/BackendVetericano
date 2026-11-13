from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


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
    numero_radicado = models.CharField(max_length=50, unique=True, blank=True, null=True)
    id_tipo = models.ForeignKey(TipoPeticion, models.DO_NOTHING, db_column='id_tipo')
    id_ubicacion = models.ForeignKey(Ubicaciones, models.DO_NOTHING, db_column='id_ubicacion', blank=True, null=True)
    id_estado = models.ForeignKey(EstadoPeticiones, models.DO_NOTHING, db_column='id_estado')
    responsable = models.ForeignKey('users.Usuarios', models.DO_NOTHING, db_column='responsable_id', blank=True, null=True)
    asignado_a = models.ForeignKey('users.Usuarios', models.DO_NOTHING, db_column='asignado_a_id', blank=True, null=True, related_name='peticiones_asignadas')
    asignado_por = models.ForeignKey('users.Usuarios', models.DO_NOTHING, db_column='asignado_por_id', blank=True, null=True, related_name='peticiones_que_asigno')
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
    # Lugar de atención como ubicación editable (puede diferir de la ubicación de la petición original)
    id_ubicacion_visita = models.ForeignKey(Ubicaciones, models.SET_NULL, db_column='id_ubicacion_visita', blank=True, null=True, related_name='visitas')
    # Campos adicionales del propietario/tutor/responsable en campo
    propietario_barrio = models.CharField(max_length=100, blank=True, null=True)
    propietario_direccion = models.CharField(max_length=255, blank=True, null=True)
    # Campos del notificador (funcionario que entrega el acta)
    notificador_nombre = models.CharField(max_length=150, blank=True, null=True)
    notificador_identificacion = models.CharField(max_length=50, blank=True, null=True)
    notificador_cargo = models.CharField(max_length=100, blank=True, null=True)
    # Firmas digitales (URL o base64 del trazo capturado en móvil)
    firma_notificador = models.TextField(blank=True, null=True)
    firma_notificado = models.TextField(blank=True, null=True)
    test_distemper = models.BooleanField(default=False)
    test_parvovirus = models.BooleanField(default=False)
    test_coronavirus = models.BooleanField(default=False)
    test_sida = models.BooleanField(default=False)
    test_leucemia = models.BooleanField(default=False)

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


class SeguimientoVisitaFuncionarios(models.Model):
    """
    Funcionarios que participaron en la visita de campo.
    Permite múltiples funcionarios (veterinarios, jurídicos, administradores)
    y también el registro de externos/pasantes con sus datos básicos.
    """
    id_seguimiento = models.ForeignKey(
        SeguimientoPeticionesVisita,
        on_delete=models.CASCADE,
        db_column='id_seguimiento',
        related_name='funcionarios'
    )
    # id_usuario es null cuando es externo/pasante no registrado en el sistema
    id_usuario = models.ForeignKey(
        'users.Usuarios',
        on_delete=models.SET_NULL,
        db_column='id_usuario',
        blank=True,
        null=True,
        related_name='visitas_como_funcionario'
    )
    nombre = models.CharField(max_length=150)
    cargo = models.CharField(max_length=100, blank=True, null=True)
    es_externo = models.BooleanField(default=False)
    es_principal = models.BooleanField(default=False)
    institucion = models.CharField(max_length=200, blank=True, null=True)
    documento_identidad = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'seguimiento_visita_funcionarios'

    def __str__(self):
        return f"{self.nombre} ({self.cargo}) - Seguimiento #{self.id_seguimiento_id}"


class EvidenciaSeguimientoVisita(models.Model):
    """
    Evidencias multimedia (fotos y videos) recolectadas durante la visita de campo
    del acta de seres sintientes.
    """
    id_evidencia = models.AutoField(primary_key=True)
    id_seguimiento = models.ForeignKey(
        SeguimientoPeticionesVisita,
        models.CASCADE,
        db_column='id_seguimiento',
        related_name='evidencias'
    )
    ruta_archivo = models.CharField(max_length=500)
    tipo_archivo = models.CharField(max_length=20, default='imagen', blank=True, null=True)  # 'imagen', 'video'
    descripcion = models.CharField(max_length=255, blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'evidencia_seguimiento_visita'

    def __str__(self):
        return f"Evidencia #{self.id_evidencia} (Visita #{self.id_seguimiento_id}) - {self.tipo_archivo}"



class HistorialAsignacionPeticion(models.Model):
    id_historial = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(
        Peticiones,
        on_delete=models.CASCADE,
        db_column='id_peticion',
        related_name='historial_asignaciones'
    )
    veterinario_anterior = models.ForeignKey(
        'users.Usuarios',
        on_delete=models.SET_NULL,
        db_column='veterinario_anterior_id',
        null=True,
        blank=True,
        related_name='historial_reasignaciones_anteriores'
    )
    veterinario_nuevo = models.ForeignKey(
        'users.Usuarios',
        on_delete=models.CASCADE,
        db_column='veterinario_nuevo_id',
        related_name='historial_reasignaciones_nuevas'
    )
    motivo = models.TextField(blank=True, null=True)
    asignado_por = models.ForeignKey(
        'users.Usuarios',
        on_delete=models.SET_NULL,
        db_column='asignado_por_id',
        null=True,
        blank=True,
        related_name='historial_asignaciones_realizadas'
    )
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'historial_asignacion_peticion'
        ordering = ['-fecha']

    def __str__(self):
        vet_ant = self.veterinario_anterior.nombre if self.veterinario_anterior else 'Sin asignar'
        vet_nvo = self.veterinario_nuevo.nombre if self.veterinario_nuevo else 'N/A'
        return f"Reasignación Petición #{self.id_peticion_id}: {vet_ant} -> {vet_nvo}"


class NotificacionJuridico(models.Model):
    id_notificacion = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(
        Peticiones,
        on_delete=models.CASCADE,
        db_column='id_peticion',
        related_name='notificaciones_juridico'
    )
    tipo = models.CharField(max_length=50)  # 'NUEVA_URGENTE', 'PLAZO_VENCIDO', 'ACTA_COMPLETADA'
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'notificacion_juridico'
        ordering = ['-fecha_creacion']

    def __str__(self):
        return f"Notificación #{self.id_notificacion} [{self.tipo}] - Petición #{self.id_peticion_id}"



class HistorialPeticiones(models.Model):
    """
    Historial de cambios de estado y asignaciones/reasignaciones de cada petición.
    """
    id_historial = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(
        Peticiones,
        models.CASCADE,
        db_column='id_peticion',
        related_name='historial_cambios'
    )
    tipo_cambio = models.CharField(max_length=30)  # 'CREACION', 'ASIGNACION', 'REASIGNACION', 'CAMBIO_ESTADO'
    id_estado_anterior = models.ForeignKey(
        EstadoPeticiones,
        models.SET_NULL,
        db_column='id_estado_anterior',
        blank=True,
        null=True,
        related_name='+'
    )
    id_estado_nuevo = models.ForeignKey(
        EstadoPeticiones,
        models.SET_NULL,
        db_column='id_estado_nuevo',
        blank=True,
        null=True,
        related_name='+'
    )
    id_veterinario_anterior = models.ForeignKey(
        'users.Usuarios',
        models.SET_NULL,
        db_column='id_veterinario_anterior',
        blank=True,
        null=True,
        related_name='+'
    )
    id_veterinario_nuevo = models.ForeignKey(
        'users.Usuarios',
        models.SET_NULL,
        db_column='id_veterinario_nuevo',
        blank=True,
        null=True,
        related_name='+'
    )
    id_usuario_accion = models.ForeignKey(
        'users.Usuarios',
        models.SET_NULL,
        db_column='id_usuario_accion',
        blank=True,
        null=True,
        related_name='+'
    )
    observacion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'historial_peticiones'

    def __str__(self):
        return f"Historial #{self.id_historial} ({self.tipo_cambio}) - Petición #{self.id_peticion_id}"


class NotificacionPeticion(models.Model):
    """
    Notificaciones dentro de la aplicación web cuando llega o se asigna una petición.
    """
    id_notificacion = models.AutoField(primary_key=True)
    id_peticion = models.ForeignKey(
        Peticiones,
        models.CASCADE,
        db_column='id_peticion',
        related_name='notificaciones'
    )
    id_usuario_destino = models.ForeignKey(
        'users.Usuarios',
        models.CASCADE,
        db_column='id_usuario_destino',
        blank=True,
        null=True,
        related_name='notificaciones_peticiones'
    )
    tipo = models.CharField(max_length=50, default='NUEVA_PETICION')
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_lectura = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'notificaciones_peticiones'

    def __str__(self):
        return f"Notificación #{self.id_notificacion} - {self.titulo}"


class PeticionPosibleDuplicado(models.Model):
    """Registra pares de peticiones candidatas a duplicidad y su decisión humana.

    Conserva siempre ambas peticiones originales mediante relaciones sin eliminación
    en cascada y garantiza unicidad del par usando IDs ordenados
    (id_peticion_menor < id_peticion_mayor).
    """

    ESTADO_PENDIENTE = 'PENDIENTE'
    ESTADO_CONFIRMADO = 'CONFIRMADO'
    ESTADO_DESCARTADO = 'DESCARTADO'
    ESTADOS_REVISION_CHOICES = [
        (ESTADO_PENDIENTE, 'Pendiente de revisión'),
        (ESTADO_CONFIRMADO, 'Duplicado confirmado'),
        (ESTADO_DESCARTADO, 'Duplicado descartado'),
    ]

    ORIGEN_REGISTRO = 'REGISTRO_PETICION'
    ORIGEN_ENDPOINT_MANUAL = 'ENDPOINT_MANUAL'
    ORIGEN_REANALISIS = 'REANALISIS'
    ORIGENES_DETECCION_CHOICES = [
        (ORIGEN_REGISTRO, 'Registro de petición'),
        (ORIGEN_ENDPOINT_MANUAL, 'Consulta manual del detector'),
        (ORIGEN_REANALISIS, 'Reanálisis posterior'),
    ]

    id_alerta = models.AutoField(primary_key=True)
    peticion_menor = models.ForeignKey(
        Peticiones,
        on_delete=models.PROTECT,
        db_column='id_peticion_menor',
        related_name='alertas_duplicado_como_menor',
    )
    peticion_mayor = models.ForeignKey(
        Peticiones,
        on_delete=models.PROTECT,
        db_column='id_peticion_mayor',
        related_name='alertas_duplicado_como_mayor',
    )
    peticion_origen = models.ForeignKey(
        Peticiones,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='id_peticion_origen',
        related_name='alertas_duplicado_originadas',
    )
    estado_revision = models.CharField(
        max_length=20,
        choices=ESTADOS_REVISION_CHOICES,
        default=ESTADO_PENDIENTE,
    )
    peticion_principal = models.ForeignKey(
        Peticiones,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_column='id_peticion_principal',
        related_name='duplicados_confirmados_como_principal',
    )

    similitud_texto = models.FloatField(null=True, blank=True)
    coincidencia_texto = models.BooleanField(default=False)
    distancia_metros = models.FloatField(null=True, blank=True)
    coincidencia_gps = models.BooleanField(default=False)
    distancia_hamming = models.SmallIntegerField(null=True, blank=True)
    similitud_imagen = models.FloatField(null=True, blank=True)
    coincidencia_imagen = models.BooleanField(default=False)
    score_combinado = models.FloatField(default=0.0)
    criterios_coincidencia = models.JSONField(default=list, blank=True)
    origen_deteccion = models.CharField(
        max_length=40,
        choices=ORIGENES_DETECCION_CHOICES,
        default=ORIGEN_ENDPOINT_MANUAL,
    )

    revisado_por = models.ForeignKey(
        'users.Usuarios',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='revisado_por_id',
        related_name='revisiones_duplicados_realizadas',
    )
    observacion_revision = models.TextField(blank=True, null=True)
    fecha_revision = models.DateTimeField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'peticion_posible_duplicado'
        ordering = ['-fecha_creacion', '-id_alerta']
        constraints = [
            models.CheckConstraint(
                condition=Q(peticion_menor__lt=F('peticion_mayor')),
                name='chk_pet_dup_par_ordenado',
            ),
            models.UniqueConstraint(
                fields=['peticion_menor', 'peticion_mayor'],
                name='uq_pet_dup_par',
            ),
            models.CheckConstraint(
                condition=Q(
                    estado_revision__in=['PENDIENTE', 'CONFIRMADO', 'DESCARTADO']
                ),
                name='chk_pet_dup_estado_valido',
            ),
            models.CheckConstraint(
                condition=(
                    Q(peticion_principal__isnull=True)
                    | Q(peticion_principal=F('peticion_menor'))
                    | Q(peticion_principal=F('peticion_mayor'))
                ),
                name='chk_pet_dup_principal_par',
            ),
            models.CheckConstraint(
                condition=(
                    Q(peticion_origen__isnull=True)
                    | Q(peticion_origen=F('peticion_menor'))
                    | Q(peticion_origen=F('peticion_mayor'))
                ),
                name='chk_pet_dup_origen_par',
            ),
        ]
        indexes = [
            models.Index(
                fields=['estado_revision', '-fecha_creacion'],
                name='idx_pet_dup_est_fecha',
            ),
            models.Index(
                fields=['peticion_menor', 'estado_revision'],
                name='idx_pet_dup_menor_est',
            ),
            models.Index(
                fields=['peticion_mayor', 'estado_revision'],
                name='idx_pet_dup_mayor_est',
            ),
            models.Index(
                fields=['peticion_principal', 'estado_revision'],
                name='idx_pet_dup_princ_est',
            ),
        ]

    @staticmethod
    def normalizar_par_ids(peticion_a_id, peticion_b_id):
        """Retorna la tupla ordenada (id_menor, id_mayor) y rechaza autorreferencias."""
        if (
            peticion_a_id is None
            or peticion_b_id is None
            or isinstance(peticion_a_id, bool)
            or isinstance(peticion_b_id, bool)
        ):
            raise ValidationError('Ambos identificadores de petición son obligatorios.')
        try:
            id_a = int(peticion_a_id)
            id_b = int(peticion_b_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError('Los identificadores de petición deben ser enteros válidos.') from exc
        if id_a <= 0 or id_b <= 0:
            raise ValidationError('Los identificadores de petición deben ser positivos.')
        if id_a == id_b:
            raise ValidationError('Una petición no puede relacionarse consigo misma como duplicado.')
        return (id_a, id_b) if id_a < id_b else (id_b, id_a)

    @classmethod
    def normalizar_par_peticiones(cls, peticion_a, peticion_b):
        """Retorna las instancias ordenadas (peticion_menor, peticion_mayor)."""
        if peticion_a is None or peticion_b is None:
            raise ValidationError('Ambas instancias de petición son obligatorias.')
        id_menor, id_mayor = cls.normalizar_par_ids(peticion_a.pk, peticion_b.pk)
        if peticion_a.pk == id_menor and peticion_b.pk == id_mayor:
            return (peticion_a, peticion_b)
        return (peticion_b, peticion_a)

    def clean(self):
        errores = {}
        estados_validos = {
            self.ESTADO_PENDIENTE,
            self.ESTADO_CONFIRMADO,
            self.ESTADO_DESCARTADO,
        }
        if self.estado_revision not in estados_validos:
            errores['estado_revision'] = 'Estado de revisión inválido.'

        if self.peticion_menor_id is not None and self.peticion_mayor_id is not None:
            if self.peticion_menor_id == self.peticion_mayor_id:
                errores['peticion_mayor'] = (
                    'Una petición no puede relacionarse consigo misma.'
                )
            elif self.peticion_menor_id > self.peticion_mayor_id:
                errores['peticion_menor'] = (
                    'El par debe almacenarse ordenado (id_peticion_menor < id_peticion_mayor).'
                )

        if self.peticion_principal_id is not None:
            ids_par = {self.peticion_menor_id, self.peticion_mayor_id} - {None}
            if self.peticion_principal_id not in ids_par:
                errores['peticion_principal'] = (
                    'La petición principal debe corresponder a una de las dos peticiones de la alerta.'
                )

        if self.peticion_origen_id is not None:
            ids_par = {self.peticion_menor_id, self.peticion_mayor_id} - {None}
            if self.peticion_origen_id not in ids_par:
                errores['peticion_origen'] = (
                    'La petición origen debe corresponder a una de las dos peticiones de la alerta.'
                )

        if errores:
            raise ValidationError(errores)

    def __str__(self):
        return (
            f"Alerta duplicado #{self.id_alerta}: "
            f"peticiones ({self.peticion_menor_id}, {self.peticion_mayor_id}) [{self.estado_revision}]"
        )
