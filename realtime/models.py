"""
Tablas nuevas (aditivas). Su migración incluye ENABLE RLS + REVOKE a anon/authenticated
(ver realtime/migrations/0002_endurecimiento_rls.py): nacen sin exposición por la Data API.
"""
from django.db import models
from django.utils import timezone


class NotificacionSistema(models.Model):
    """
    Notificaciones persistentes individuales que no son de peticiones
    (inventario/kit, voluntariado...). Las de peticiones siguen en `notificaciones_peticiones`.
    """
    id_notificacion = models.AutoField(primary_key=True)
    id_usuario_destino = models.ForeignKey(
        'users.Usuarios',
        on_delete=models.CASCADE,
        db_column='id_usuario_destino',
        related_name='notificaciones_sistema',
    )
    event_type = models.CharField(max_length=80)
    modulo = models.CharField(max_length=30)
    tipo = models.CharField(max_length=50)
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    prioridad = models.CharField(max_length=20, blank=True, null=True)
    referencia = models.JSONField(default=dict, blank=True)
    clave_dedup = models.CharField(max_length=150, blank=True, null=True)
    leida = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(default=timezone.now)
    fecha_lectura = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'notificaciones_sistema'
        indexes = [
            models.Index(
                fields=['id_usuario_destino', 'leida', '-fecha_creacion'],
                name='notif_sis_usuario_leida_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['id_usuario_destino', 'clave_dedup'],
                condition=models.Q(clave_dedup__isnull=False),
                name='notif_sis_dedup_uniq',
            ),
        ]

    def __str__(self):
        return f'NotificacionSistema #{self.id_notificacion} [{self.event_type}]'


class DispositivoFCM(models.Model):
    """Token FCM por dispositivo. El envío es siempre por token individual (nunca topics)."""
    id_dispositivo = models.AutoField(primary_key=True)
    id_usuario = models.ForeignKey(
        'users.Usuarios',
        on_delete=models.CASCADE,
        db_column='id_usuario',
        related_name='dispositivos_fcm',
    )
    token = models.CharField(max_length=512, unique=True)
    plataforma = models.CharField(max_length=20, default='android')
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(default=timezone.now)
    actualizado_en = models.DateTimeField(default=timezone.now)
    ultimo_error = models.CharField(max_length=200, blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'dispositivos_fcm'
        indexes = [
            models.Index(fields=['id_usuario', 'activo'], name='disp_fcm_usuario_activo_idx'),
        ]

    def __str__(self):
        return f'DispositivoFCM #{self.id_dispositivo} usuario={self.id_usuario_id}'
