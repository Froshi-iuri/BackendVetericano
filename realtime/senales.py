"""
Eventos que se derivan de cambios de modelo (post_save / post_delete), sin tocar las vistas:

- AS de catálogos y vistas (MED-01, CLI-01, CLI-02, VOL-03, ADM-01, INV-01, KIT-01 parte AS, PET-08)
- AO PET-09 (seguimiento/acta de visita creada)
- USR-01 (gestión de usuarios) y SEG-01 (revocación al inactivar o cambiar de rol)

Todo se emite DESPUÉS del COMMIT (realtime.publicar.al_confirmar) y, dentro de una misma
transacción, las AS se coalescen (una importación de 500 medicamentos = 1 evento).
Limitación conocida: `QuerySet.update()`/`bulk_create` no disparan señales; las vistas que los usan
se cubren con la reconciliación REST periódica de los clientes.
"""
import logging

from django.apps import apps
from django.conf import settings
from django.db.models.signals import post_delete, post_save, pre_save

from realtime import fcm, notificar, publicar

logger = logging.getLogger('realtime')

ROLES_GESTION = (1, 2)
ROLES_CLINICA = (1, 3)
ROLES_STAFF = (1, 2, 3)
ROLES_VOLUNTARIADO = (1, 2, 3, 4)

# (modelo, event_type, roles)
REGISTRO_AS = [
    ('medicamentos.Medicamento', 'catalogo.medicamentos_actualizado', ROLES_STAFF),
    ('medicamentos.Medicamentos', 'catalogo.medicamentos_actualizado', ROLES_STAFF),
    ('clinica.HistoriaClinica', 'clinica.atencion_registrada', ROLES_CLINICA),
    ('clinica.Consulta', 'clinica.atencion_registrada', ROLES_CLINICA),
    ('clinica.HospitalizacionSeresSintientes', 'clinica.atencion_registrada', ROLES_CLINICA),
    ('clinica.SeguimientosClinicos', 'clinica.atencion_registrada', ROLES_CLINICA),
    ('especies.Especie', 'catalogo.clinico_actualizado', ROLES_CLINICA),
    ('especies.Raza', 'catalogo.clinico_actualizado', ROLES_CLINICA),
    ('examenes_clinicos.Examen', 'catalogo.clinico_actualizado', ROLES_CLINICA),
    ('patologias.Patologia', 'catalogo.clinico_actualizado', ROLES_CLINICA),
    ('voluntariado.EventoVoluntariado', 'voluntariado.evento_actualizado', ROLES_VOLUNTARIADO),
    ('voluntariado.Eventos', 'voluntariado.evento_actualizado', ROLES_VOLUNTARIADO),
    ('users.Sede', 'admin.sede_actualizada', (5,)),
    ('inventario.Compra', 'inventario.movimiento_bodega', ROLES_GESTION),
    ('inventario.DetalleCompra', 'inventario.movimiento_bodega', ROLES_GESTION),
    ('inventario.Salidas', 'inventario.movimiento_bodega', ROLES_GESTION),
    ('inventario.DetalleSalida', 'inventario.movimiento_bodega', ROLES_GESTION),
    ('inventario.Inventarios', 'inventario.movimiento_bodega', ROLES_GESTION),
]

_registrado = False


def _accion(created=None, eliminado=False):
    if eliminado:
        return 'eliminado'
    return 'creado' if created else 'actualizado'


def _referencia(instancia):
    return {'modelo': instancia._meta.model_name, 'id': instancia.pk}


def _hacer_receptor_as(event_type, roles):
    def receptor_guardado(sender, instance, created=False, raw=False, **kwargs):
        if raw:
            return
        notificar.actualizacion_silenciosa(
            event_type, accion=_accion(created), roles=roles, referencia=_referencia(instance),
        )

    def receptor_borrado(sender, instance, **kwargs):
        notificar.actualizacion_silenciosa(
            event_type, accion=_accion(eliminado=True), roles=roles, referencia=_referencia(instance),
        )

    return receptor_guardado, receptor_borrado


# ── KIT-01 (parte AS): el vet titular y los gestores refrescan el kit ─────────────────────
def _kit_guardado(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    notificar.actualizacion_silenciosa(
        'kit.stock_actualizado',
        accion=_accion(created),
        usuarios=[getattr(instance, 'id_veterinario_id', None)],
        roles=ROLES_GESTION,
        referencia={'modelo': 'kit_veterinario_lotes', 'id_veterinario': getattr(instance, 'id_veterinario_id', None)},
    )


# ── PET-08 / PET-09 ─────────────────────────────────────────────────────────────────────
def _duplicado_guardado(sender, instance, created=False, raw=False, **kwargs):
    if raw or created:
        return  # la creación la notifica PET-07 (peticion.posible_duplicado)
    notificar.actualizacion_silenciosa(
        'peticion.duplicado_resuelto', roles=ROLES_GESTION, referencia=_referencia(instance),
    )


def _seguimiento_guardado(sender, instance, created=False, raw=False, **kwargs):
    if raw or not created:
        return
    id_peticion = getattr(instance, 'id_peticion_id', None)
    radicado = None
    try:
        radicado = getattr(instance.id_peticion, 'numero_radicado', None)
    except Exception:
        pass
    notificar.alerta_operativa(
        'peticion.seguimiento_creado',
        'Nuevo seguimiento de visita',
        f"Se registró un seguimiento/acta de visita para la petición {radicado or f'#{id_peticion}'}.",
        nivel='info',
        roles=ROLES_GESTION,
        referencia={'id_peticion': id_peticion, 'numero_radicado': radicado, 'id_seguimiento': instance.pk},
    )


# ── USR-01 / SEG-01 ─────────────────────────────────────────────────────────────────────
def _usuario_antes(sender, instance, raw=False, **kwargs):
    instance._realtime_previo = None
    if raw or not instance.pk:
        return
    # Auditoría B2: sin publicación WS ni tablas realtime no hay nada que revocar; se evita el SELECT extra.
    if not (getattr(settings, 'WS_PUBLICAR_EVENTOS', False)
            or getattr(settings, 'NOTIFICACIONES_SISTEMA_HABILITADAS', False)):
        return
    try:
        instance._realtime_previo = (
            sender.objects.filter(pk=instance.pk).values_list('activo', 'id_rol_id').first()
        )
    except Exception:
        instance._realtime_previo = None


def _usuario_guardado(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    notificar.actualizacion_silenciosa(
        'admin.usuario_gestionado', accion=_accion(created), roles=(1, 5),
        referencia={'id_usuario': instance.pk},
    )
    previo = getattr(instance, '_realtime_previo', None)
    if not previo:
        return
    activo_antes, rol_antes = previo
    uid = instance.pk
    if activo_antes and not instance.activo:
        def revocar_inactivo():
            publicar.revocar_sesion(uid, 'inactivado')
            fcm.desactivar_tokens_usuario(uid)

        publicar.al_confirmar(revocar_inactivo)
    elif rol_antes != instance.id_rol_id:
        publicar.al_confirmar(lambda: publicar.revocar_sesion(uid, 'rol_cambiado'))


def _modelo(etiqueta):
    try:
        return apps.get_model(etiqueta)
    except (LookupError, ValueError):
        logger.warning('realtime: modelo %s no encontrado; se omite su evento', etiqueta)
        return None


def registrar():
    global _registrado
    if _registrado:
        return
    _registrado = True
    for etiqueta, event_type, roles in REGISTRO_AS:
        modelo = _modelo(etiqueta)
        if modelo is None:
            continue
        guardado, borrado = _hacer_receptor_as(event_type, roles)
        post_save.connect(guardado, sender=modelo, weak=False, dispatch_uid=f'rt_as_save_{etiqueta}')
        post_delete.connect(borrado, sender=modelo, weak=False, dispatch_uid=f'rt_as_del_{etiqueta}')

    especiales = [
        ('inventario.KitVeterinarioLotes', post_save, _kit_guardado),
        ('peticiones.PeticionPosibleDuplicado', post_save, _duplicado_guardado),
        ('peticiones.SeguimientoPeticionesVisita', post_save, _seguimiento_guardado),
        ('users.Usuarios', pre_save, _usuario_antes),
        ('users.Usuarios', post_save, _usuario_guardado),
    ]
    for etiqueta, senal, receptor in especiales:
        modelo = _modelo(etiqueta)
        if modelo is not None:
            senal.connect(receptor, sender=modelo, weak=False,
                          dispatch_uid=f'rt_{senal is pre_save}_{etiqueta}_{receptor.__name__}')
