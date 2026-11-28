import time
from django.core.management.base import BaseCommand

from triaje_ia.services import recuperar_trabajos_interrumpidos


class Command(BaseCommand):
    help = 'Recupera trabajos interrumpidos y procesa reintentos pendientes de triaje hacia n8n.'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=20, help='Máximo de registros a procesar por ciclo.')
        parser.add_argument('--continuo', action='store_true', help='Ejecuta el recuperador de forma periódica.')
        parser.add_argument('--intervalo', type=float, default=30.0, help='Segundos entre ciclos en modo --continuo.')

    def handle(self, *args, **options):
        limite = options['limite']
        if not options['continuo']:
            resultado = recuperar_trabajos_interrumpidos(limite=limite)
            self.stdout.write(str(resultado))
            return

        intervalo = max(5.0, float(options['intervalo']))
        while True:
            resultado = recuperar_trabajos_interrumpidos(limite=limite)
            self.stdout.write(str(resultado))
            time.sleep(intervalo)
