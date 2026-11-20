"""
Rutas URL para el módulo de Apoyo Diagnóstico IA.
"""

from django.urls import path
from diagnostico_ia.views import DiagnosticoIAPredictView, DiagnosticoIAInfoView

urlpatterns = [
    path('sugerir/', DiagnosticoIAPredictView.as_view(), name='diagnostico-ia-sugerir'),
    path('predecir/', DiagnosticoIAPredictView.as_view(), name='diagnostico-ia-predecir'),
    path('info/', DiagnosticoIAInfoView.as_view(), name='diagnostico-ia-info'),
]
