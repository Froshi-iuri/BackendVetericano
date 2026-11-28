import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from peticiones.models import Peticiones
from triaje_ia.models import TriajePeticion
from triaje_ia.services import (
    HEADER_DISPATCH_TOKEN,
    HEADER_IDEMPOTENCY_KEY,
    HEADER_TRIAJE_WEBHOOK_SECRET,
    MARCADOR_PRUEBA_TRIAJE,
    _ahora_local,
    construir_payload_peticion_triaje,
    evaluar_privacidad_para_gemini,
    sanitizar_texto_pii_para_ia,
)


class Command(BaseCommand):
    help = (
        'Prepara un registro TriajePeticion de prueba con despacho_token válido para probar '
        'el webhook real de n8n sin activar TRIAJE_N8N_ACTIVO y bloqueando peticiones reales.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--id-peticion', type=int, required=True, help='ID de la petición ficticia de prueba.')

    def handle(self, *args, **options):
        id_peticion = int(options['id_peticion'])
        peticion = (
            Peticiones.objects.select_related('id_tipo', 'id_estado', 'id_ubicacion', 'responsable')
            .prefetch_related('evidenciapeticiones_set')
            .filter(pk=id_peticion)
            .first()
        )
        if not peticion:
            raise CommandError(f'La petición #{id_peticion} no existe.')

        descripcion = str(peticion.descripcion or '').strip()
        if not descripcion.upper().startswith(MARCADOR_PRUEBA_TRIAJE):
            raise CommandError(
                'Seguridad: la descripción de la petición debe iniciar con [PRUEBA-TRIAJE] '
                'para evitar modificar peticiones ciudadanas reales.'
            )

        if peticion.asignado_a_id is not None:
            raise CommandError(
                'Seguridad: la petición ya tiene veterinario asignado; no se modifica una denuncia operativa.'
            )

        es_seguro, motivos = evaluar_privacidad_para_gemini(
            descripcion,
            sanitizar_texto_pii_para_ia(descripcion),
            permitir_denuncias_reales=False,
            prueba_ficticia_autorizada=True,
        )
        if not es_seguro:
            raise CommandError(
                f'Seguridad: la petición contiene datos confidenciales o PII residual ({", ".join(motivos)}).'
            )

        triaje_existente = TriajePeticion.objects.filter(peticion_id=id_peticion).first()
        if triaje_existente and (
            triaje_existente.estado_analisis == TriajePeticion.ANALISIS_COMPLETADO
            or triaje_existente.decision_prioridad != TriajePeticion.DECISION_PENDIENTE
            or triaje_existente.decision_veterinario != TriajePeticion.DECISION_PENDIENTE
        ):
            raise CommandError(
                f'La petición #{id_peticion} ya tiene un triaje completado o con decisión humana; no se sobrescribe.'
            )

        ahora = _ahora_local() or timezone.now()
        token = uuid.uuid4().hex
        clave_idem = f'triaje-pet-{id_peticion}'

        triaje, _ = TriajePeticion.objects.update_or_create(
            peticion_id=id_peticion,
            defaults={
                'clave_idempotencia': clave_idem,
                'despacho_token': token,
                'estado_envio': TriajePeticion.ENVIO_ENVIADO,
                'estado_analisis': TriajePeticion.ANALISIS_EN_PROCESO,
                'enviado_en': ahora,
                'lease_expira_en': None,
                'proximo_reintento_en': None,
                'prioridad_original': peticion.prioridad,
                'resultado_raw': {
                    'prueba_ficticia_autorizada': True,
                    'autorizada_por_comando': 'preparar_prueba_webhook_triaje',
                },
            },
        )

        payload = construir_payload_peticion_triaje(
            peticion,
            clave_idempotencia=triaje.clave_idempotencia,
            despacho_token=token,
        )

        salida = {
            'id_peticion': id_peticion,
            'id_triaje': triaje.id_triaje,
            'clave_idempotencia': triaje.clave_idempotencia,
            'despacho_token': token,
            'headers_requeridos': {
                'Content-Type': 'application/json',
                HEADER_TRIAJE_WEBHOOK_SECRET: '<TRIAJE_N8N_WEBHOOK_SECRET>',
                HEADER_IDEMPOTENCY_KEY: triaje.clave_idempotencia,
                HEADER_DISPATCH_TOKEN: token,
            },
            'payload_webhook': payload,
        }
        self.stdout.write(json.dumps(salida, ensure_ascii=False, indent=2))
