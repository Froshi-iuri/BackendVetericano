from django.apps import AppConfig


class DiagnosticoIaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'diagnostico_ia'
    verbose_name = 'Módulo de Apoyo Diagnóstico IA'

    def ready(self):
        # Inicialización / precarga ligera del modelo en arranque
        try:
            from diagnostico_ia.ml.engine import obtener_motor_diagnostico
            # Obtiene o genera el modelo si no existe
            obtener_motor_diagnostico()
        except Exception as e:
            # En migraciones o comandos de gestión no debe romper
            pass
