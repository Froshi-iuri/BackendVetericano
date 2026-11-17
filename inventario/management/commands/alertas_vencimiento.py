"""
KIT-02 (NP) — Barrido diario de vencimientos en kits veterinarios y bodega general.

    python manage.py alertas_vencimiento            # crea las NP pendientes
    python manage.py alertas_vencimiento --dry-run  # solo informa qué crearía (no escribe nada)

Pensado para un cron diario de Railway (no se configura aquí).

- Recorre los lotes con existencias (cantidad_actual > 0) vencidos o que vencen en ≤30 días
  (VENTANA_DIAS_VENCIMIENTO) en kit_veterinario_lotes y en v_inventario_general_lotes.
- Crea NP `kit.alerta_stock_vencimiento` (notificaciones_sistema):
    * lote de kit   -> vet titular (si está activo) + usuarios activos rol 1 y 2;
    * lote de bodega -> usuarios activos rol 1 y 2.
- Idempotente: clave_dedup estable `venc:<kit|bod>:<id_lote>:<umbral>:<fecha_vencimiento>` por usuario;
  volver a ejecutarlo el mismo día (o cualquier otro) no repite la NP. Cuando el lote pasa de
  'proximo_a_vencer' a 'vencido' cambia el umbral y se notifica una única vez más.
- Requiere NOTIFICACIONES_SISTEMA_HABILITADAS=True para persistir; con el flag apagado no escribe nada.
- La salida solo muestra ids y conteos (sin nombres ni datos personales).
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from inventario import notificaciones_realtime as notif_rt
from inventario.constants import ROL_VETERINARIO
from realtime import notificar

TAMANO_LOTE_CLAVES = 500


class Command(BaseCommand):
    help = (
        'KIT-02: crea notificaciones persistentes kit.alerta_stock_vencimiento para lotes vencidos o '
        'por vencer (<=30 dias) en kits y bodega, sin repetirlas (clave_dedup). Usa --dry-run para previsualizar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true', dest='dry_run',
            help='No escribe nada: solo informa cuántas notificaciones se crearían.',
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get('dry_run'))
        habilitadas = bool(getattr(settings, 'NOTIFICACIONES_SISTEMA_HABILITADAS', False))

        candidatos = notif_rt.candidatos_vencimiento()
        en_kit = sum(1 for c in candidatos if c['ambito'] == 'kit')
        self.stdout.write(
            f"Lotes candidatos: {len(candidatos)} (kit={en_kit}, bodega={len(candidatos) - en_kit})."
        )
        if not candidatos:
            return

        if not dry_run and not habilitadas:
            self.stdout.write(self.style.WARNING(
                'NOTIFICACIONES_SISTEMA_HABILITADAS=False: no se persiste ninguna notificación. '
                'Usa --dry-run para previsualizar.'
            ))
            return

        gestores = _ids_usuarios(notificar.usuarios_activos_por_rol(notif_rt.ROLES_GESTION))
        vets_activos = self._veterinarios_activos({c['id_veterinario'] for c in candidatos if c['id_veterinario']})
        claves = {notif_rt.clave_vencimiento(c) for c in candidatos}
        existentes = self._existentes(claves)

        previstas = creadas = lotes_omitidos = 0
        for c in candidatos:
            clave = notif_rt.clave_vencimiento(c)
            destinatarios = list(gestores)
            if c['ambito'] == 'kit' and c['id_veterinario'] in vets_activos and c['id_veterinario'] not in destinatarios:
                destinatarios.append(c['id_veterinario'])
            pendientes = [u for u in destinatarios if existentes is None or (u, clave) not in existentes]
            if not pendientes:
                lotes_omitidos += 1
                continue
            previstas += len(pendientes)
            if dry_run:
                self.stdout.write(
                    f"[dry-run] {c['ambito']} lote #{c['id_lote']} (inventario #{c['id_inventario']}) "
                    f"umbral={c['umbral']} vence={c['fecha_vencimiento']} destinatarios={len(pendientes)}"
                )
                continue
            titulo, mensaje = notif_rt.texto_vencimiento(c)
            resultado = notificar.notificar_usuarios(
                notif_rt.EV_KIT_ALERTA,
                pendientes,
                titulo,
                mensaje,
                tipo='VENCIMIENTO_LOTE',
                referencia=notif_rt.referencia_vencimiento(c),
                clave_dedup=clave,
                raise_on_error=False,
            )
            creadas += len(resultado or [])

        if dry_run:
            verificacion = '' if existentes is not None else ' (deduplicación no verificable: tabla no disponible)'
            self.stdout.write(self.style.SUCCESS(
                f"[dry-run] Se crearían {previstas} notificación(es); lotes ya notificados: {lotes_omitidos}."
                f"{verificacion}"
            ))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Notificaciones creadas: {creadas} de {previstas} previstas; lotes ya notificados: {lotes_omitidos}."
        ))

    def _veterinarios_activos(self, ids):
        if not ids:
            return set()
        from users.models import Usuarios

        return set(
            Usuarios.objects.filter(pk__in=list(ids), activo=True, id_rol_id=ROL_VETERINARIO)
            .values_list('id_usuario', flat=True)
        )

    def _existentes(self, claves):
        """Pares (id_usuario, clave_dedup) ya creados. None si no se puede verificar (p. ej. sin migración)."""
        try:
            from realtime.models import NotificacionSistema

            existentes = set()
            claves = sorted(claves)
            for i in range(0, len(claves), TAMANO_LOTE_CLAVES):
                existentes.update(
                    NotificacionSistema.objects.filter(clave_dedup__in=claves[i:i + TAMANO_LOTE_CLAVES])
                    .values_list('id_usuario_destino_id', 'clave_dedup')
                )
            return existentes
        except Exception as exc:
            self.stderr.write(f"No se pudo verificar la deduplicación previa ({type(exc).__name__}).")
            return None


def _ids_usuarios(usuarios):
    ids = []
    for u in usuarios or ():
        uid = u if isinstance(u, int) else getattr(u, 'id_usuario', None) or getattr(u, 'pk', None)
        if uid and uid not in ids:
            ids.append(uid)
    return ids
