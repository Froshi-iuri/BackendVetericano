from django.apps import AppConfig


class RealtimeConfig(AppConfig):
    default_auto_field = 'django.db.models.AutoField'
    name = 'realtime'
    verbose_name = 'Notificaciones en tiempo real'

    def ready(self):
        # Conecta las señales que emiten las actualizaciones silenciosas (AS),
        # la gestión de usuarios (USR-01) y la revocación de sesiones (SEG-01).
        from realtime import senales

        senales.registrar()
