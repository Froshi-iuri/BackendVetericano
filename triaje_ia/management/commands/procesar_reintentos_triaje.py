from django.core.management.base import BaseCommand

from triaje_ia.services import procesar_reintentos_pendientes


class Command(BaseCommand):
    help = 'Procesa los envíos pendientes o con fallo temporal de triaje hacia n8n.'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=20, help='Máximo de registros a procesar.')

    def handle(self, *args, **options):
        resultado = procesar_reintentos_pendientes(limite=options['limite'])
        self.stdout.write(str(resultado))
