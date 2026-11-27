from django.db import models


class TriajePeticion(models.Model):
    """
    Almacena el estado de envío hacia n8n y el resultado del triaje inteligente
    para una petición ciudadana, sin sobrescribir la prioridad ni asignar
    automáticamente un veterinario.
    """

    ENVIO_PENDIENTE = 'PENDIENTE'
    ENVIO_ENVIADO = 'ENVIADO'
    ENVIO_FALLO_TEMPORAL = 'FALLO_TEMPORAL'
    ENVIO_FALLO_DEFINITIVO = 'FALLO_DEFINITIVO'
    ENVIO_OMITIDO = 'OMITIDO'

    ESTADOS_ENVIO_CHOICES = [
        (ENVIO_PENDIENTE, 'Pendiente de envío'),
        (ENVIO_ENVIADO, 'Enviado a n8n'),
        (ENVIO_FALLO_TEMPORAL, 'Fallo temporal (reintento pendiente)'),
        (ENVIO_FALLO_DEFINITIVO, 'Fallo definitivo (reintentos agotados)'),
        (ENVIO_OMITIDO, 'Omitido'),
    ]

    ANALISIS_PENDIENTE = 'PENDIENTE'
    ANALISIS_EN_PROCESO = 'EN_PROCESO'
    ANALISIS_COMPLETADO = 'COMPLETADO'
    ANALISIS_PARCIAL = 'PARCIAL'
    ANALISIS_FALLIDO = 'FALLIDO'

    ESTADOS_ANALISIS_CHOICES = [
        (ANALISIS_PENDIENTE, 'Pendiente'),
        (ANALISIS_EN_PROCESO, 'En proceso'),
        (ANALISIS_COMPLETADO, 'Completado'),
        (ANALISIS_PARCIAL, 'Completado parcialmente (degradado)'),
        (ANALISIS_FALLIDO, 'Fallido'),
    ]

    PRIORIDAD_URGENTE = 'URGENTE'
    PRIORIDAD_ALTA = 'ALTA'
    PRIORIDAD_MEDIA = 'MEDIA'
    PRIORIDAD_BAJA = 'BAJA'

    PRIORIDADES_SUGERIDAS_CHOICES = [
        (PRIORIDAD_URGENTE, 'Urgente'),
        (PRIORIDAD_ALTA, 'Alta'),
        (PRIORIDAD_MEDIA, 'Media'),
        (PRIORIDAD_BAJA, 'Baja'),
    ]

    id_triaje = models.BigAutoField(primary_key=True)
    peticion = models.OneToOneField(
        'peticiones.Peticiones',
        on_delete=models.CASCADE,
        db_column='id_peticion',
        related_name='triaje_ia',
    )
    clave_idempotencia = models.CharField(max_length=120, unique=True)

    estado_envio = models.CharField(
        max_length=25,
        choices=ESTADOS_ENVIO_CHOICES,
        default=ENVIO_PENDIENTE,
    )
    estado_analisis = models.CharField(
        max_length=25,
        choices=ESTADOS_ANALISIS_CHOICES,
        default=ANALISIS_PENDIENTE,
    )

    intentos_envio = models.PositiveSmallIntegerField(default=0)
    max_intentos = models.PositiveSmallIntegerField(default=3)
    proximo_reintento_en = models.DateTimeField(null=True, blank=True)
    ultimo_codigo_http = models.PositiveSmallIntegerField(null=True, blank=True)
    ultimo_error_envio = models.TextField(blank=True, default='')
    enviado_en = models.DateTimeField(null=True, blank=True)
    completado_en = models.DateTimeField(null=True, blank=True)
    n8n_execution_id = models.CharField(max_length=120, blank=True, default='')

    prioridad_original = models.CharField(max_length=20, null=True, blank=True)
    prioridad_sugerida = models.CharField(
        max_length=20,
        choices=PRIORIDADES_SUGERIDAS_CHOICES,
        null=True,
        blank=True,
    )
    nivel_confianza = models.FloatField(null=True, blank=True)
    justificacion = models.TextField(blank=True, default='')
    resumen_texto = models.TextField(blank=True, default='')
    resumen_vision = models.TextField(blank=True, default='')
    signos_detectados = models.JSONField(default=list, blank=True)

    posibles_duplicados = models.JSONField(default=list, blank=True)
    total_duplicados_detectados = models.PositiveIntegerField(default=0)

    veterinario_recomendado = models.ForeignKey(
        'users.Usuarios',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='id_veterinario_recomendado',
        related_name='triajes_recomendados',
    )
    justificacion_recomendacion = models.TextField(blank=True, default='')
    carga_veterinarios_snapshot = models.JSONField(default=list, blank=True)

    modelo_texto_usado = models.CharField(max_length=100, blank=True, default='')
    modelo_vision_usado = models.CharField(max_length=100, blank=True, default='')
    resultado_raw = models.JSONField(default=dict, blank=True)

    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'triaje_peticion'
        ordering = ['-fecha_creacion', '-id_triaje']
        indexes = [
            models.Index(
                fields=['estado_envio', 'proximo_reintento_en'],
                name='idx_triaje_envio_reint',
            ),
            models.Index(
                fields=['estado_analisis', '-fecha_creacion'],
                name='idx_triaje_analisis_fecha',
            ),
        ]

    def __str__(self):
        return (
            f"Triaje #{self.id_triaje} (Petición #{self.peticion_id}) "
            f"[{self.estado_envio}/{self.estado_analisis}]"
        )
