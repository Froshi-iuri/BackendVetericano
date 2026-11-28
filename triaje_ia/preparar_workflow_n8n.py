"""Genera el cuerpo de actualización de un workflow existente a partir de su exportación."""

import argparse
import json
from pathlib import Path


PLANTILLA = Path(__file__).with_name('n8n_workflow_triaje_vetericano.sanitized.json')
TIPO_WEBHOOK = 'n8n-nodes-base.webhook'
PREFIJO_MARCADOR = 'REPLACE_WITH_'


def preparar_actualizacion(exportado, id_workflow):
    if isinstance(exportado, list) and len(exportado) == 1:
        exportado = exportado[0]
    if not isinstance(exportado, dict) or exportado.get('id') != id_workflow:
        raise ValueError('La exportación debe contener únicamente el workflow del ID indicado.')

    plantilla = json.loads(PLANTILLA.read_text(encoding='utf-8'))
    nodos = {nodo['name']: nodo for nodo in exportado['nodes']}
    webhooks = [nodo for nodo in exportado['nodes'] if nodo['type'] == TIPO_WEBHOOK]
    webhook_nuevo, = (nodo for nodo in plantilla['nodes'] if nodo['type'] == TIPO_WEBHOOK)
    if len(webhooks) != 1 or webhooks[0]['name'] != webhook_nuevo['name']:
        raise ValueError('La exportación debe tener un único nodo Complaint Webhook.')
    webhook = webhooks[0]
    for campo in ('path', 'httpMethod', 'authentication'):
        if webhook['parameters'].get(campo) != webhook_nuevo['parameters'][campo]:
            raise ValueError(f'El webhook publicado no coincide en {campo}.')
    webhook_id = webhook.get('webhookId')
    if not webhook_id or webhook_id.startswith(PREFIJO_MARCADOR):
        raise ValueError('El webhook exportado debe incluir su webhookId real.')
    webhook_nuevo['id'] = webhook['id']
    webhook_nuevo['webhookId'] = webhook_id

    referencias = {}
    for nodo in plantilla['nodes']:
        credenciales_previas = nodos.get(nodo['name'], {}).get('credentials', {})
        for tipo, marcador in nodo.get('credentials', {}).items():
            anterior = credenciales_previas.get(tipo)
            if not anterior:
                continue
            credencial_id = anterior.get('id')
            if not credencial_id or str(credencial_id).startswith(PREFIJO_MARCADOR):
                raise ValueError(f'La credencial de {nodo["name"]} no tiene un ID real.')
            referencia = {'id': credencial_id, 'name': anterior['name']}
            if referencias.setdefault(marcador['id'], referencia) != referencia:
                raise ValueError(f'Los nodos usan referencias inconsistentes para {marcador["name"]}.')

    for nodo in plantilla['nodes']:
        for tipo, marcador in nodo.get('credentials', {}).items():
            if marcador['id'] not in referencias:
                raise ValueError(f'Falta la referencia existente de {marcador["name"]}.')
            nodo['credentials'][tipo] = referencias[marcador['id']]

    return {
        'name': exportado['name'],
        'nodes': plantilla['nodes'],
        'connections': plantilla['connections'],
        'settings': {**(exportado.get('settings') or {}), **plantilla['settings']},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exportado', type=Path, required=True, help='JSON del workflow actual, sin exportar credenciales.')
    parser.add_argument('--id-workflow', required=True, help='ID del workflow que se actualizará en n8n.')
    parser.add_argument('--salida', type=Path, required=True, help='Archivo JSON nuevo para el PUT de n8n.')
    args = parser.parse_args()
    try:
        exportado = json.loads(args.exportado.read_text(encoding='utf-8-sig'))
        body = preparar_actualizacion(exportado, args.id_workflow)
        with args.salida.open('x', encoding='utf-8') as archivo:
            json.dump(body, archivo, ensure_ascii=False, indent=2)
            archivo.write('\n')
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.exit(1, f'No se preparó el JSON: {exc}\n')


if __name__ == '__main__':
    main()
