"""
Script para eliminar los registros de prueba de patologías humanas
(Hipertensión arterial, Aterosclerosis, Diabetes mellitus, Asma, EPOC, Depresión, Enfermedad de Alzheimer)
previo a la carga del catálogo clínico veterinario para caninos y felinos.
"""
import os
import sys
import django

# Asegurar path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vetericano.settings')
django.setup()

from django.db import transaction
from patologias.models import Patologia
from clinica.models import DiagnosticoPatologia

NOMBRES_HUMANAS = [
    'Hipertensión arterial',
    'Aterosclerosis',
    'Diabetes mellitus',
    'Asma',
    'EPOC',
    'Depresión',
    'Enfermedad de Alzheimer'
]

def limpiar_patologias_humanas():
    with transaction.atomic():
        diag_links = DiagnosticoPatologia.objects.filter(id_patologia__nombre__in=NOMBRES_HUMANAS)
        d_count = diag_links.count()
        diag_links.delete()

        pats = Patologia.objects.filter(nombre__in=NOMBRES_HUMANAS)
        p_count = pats.count()
        pats.delete()

        print(f"Éxito: Se eliminaron {d_count} referencias en diagnósticos y {p_count} patologías de prueba humanas.")

if __name__ == '__main__':
    limpiar_patologias_humanas()
