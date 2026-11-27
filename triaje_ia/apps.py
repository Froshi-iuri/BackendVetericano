from django.apps import AppConfig


class TriajeIaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'triaje_ia'
    verbose_name = 'Triaje Inteligente IA (n8n)'

    def ready(self):
        from triaje_ia.recovery import iniciar_recuperador_automatico_si_aplica

        iniciar_recuperador_automatico_si_aplica()
