"""
API de dominio para que los módulos emitan eventos del catálogo (contrato v1).

Uso desde una vista o servicio, DENTRO de su transacción de negocio:

    from realtime import notificar

    # NP persistente (tabla notificaciones_sistema) + WS + FCM tras el COMMIT
    notificar.notificar_usuarios('kit.solicitud_creada', notificar.usuarios_activos_por_rol([1, 2]),
                                 titulo='Nueva solicitud de kit', mensaje='...',
                                 referencia={'id_solicitud': 5}, raise_on_error=True)

    # NP de peticiones ya creadas en notificaciones_peticiones (utils.py existentes)
    notificar.entregar_notificaciones_peticion(lista_de_NotificacionPeticion)

    # Alerta operativa (solo WS) y actualización silenciosa (solo WS, sin texto)
    notificar.alerta_operativa('inventario.alerta_bodega', 'Stock bajo', '...', roles=[1, 2])
    notificar.actualizacion_silenciosa('kit.solicitud_cancelada', roles=[1, 2], referencia={...})

Garantías:
- La persistencia (NP) ocurre en la transacción del llamador: con raise_on_error=True un fallo
  revierte la operación de negocio; con False se registra y la operación sigue (SAVEPOINT).
- Sin NOTIFICACIONES_SISTEMA_HABILITADAS (por defecto False) no persiste nada en notificaciones_sistema
  (despliegue en oscuro antes de aplicar la migración de la app realtime).
- WS y FCM salen SOLO después del COMMIT y nunca lanzan hacia la vista.
"""
import logging

from django.conf import settings
from django.db import transaction

from realtime import eventos, fcm, publicar
from realtime.serializacion import item_peticion, item_sistema

logger = logging.getLogger('realtime')


def usuarios_activos_por_rol(roles):
    from users.models import Usuarios

    return list(Usuarios.objects.filter(id_rol_id__in=list(roles), activo=True).only('id_usuario'))


def _ids_usuarios(usuarios, excluir=()):
    excluidos = {_id_de(u) for u in (excluir or ()) if u is not None}
    vistos = []
    for u in usuarios or ():
        uid = _id_de(u)
        if uid and uid not in excluidos and uid not in vistos:
            vistos.append(uid)
    return vistos


def _id_de(usuario):
    if usuario is None:
        return None
    if isinstance(usuario, int):
        return usuario
    return getattr(usuario, 'id_usuario', None) or getattr(usuario, 'pk', None)


def _entregar(pares):
    """pares: [(id_usuario, item)]. Se ejecuta después del COMMIT."""
    for uid, item in pares:
        publicar.publicar_evento(item['event_type'], eventos.NP, item, usuarios=[uid])
    fcm.enviar_items(pares)


def notificar_usuarios(
    event_type,
    usuarios,
    titulo,
    mensaje,
    *,
    tipo=None,
    prioridad=None,
    referencia=None,
    clave_dedup=None,
    excluir=(),
    raise_on_error=False,
):
    """Crea una NP individual por usuario en notificaciones_sistema y programa la entrega."""
    try:
        modulo = eventos.modulo_de(event_type)
        if not getattr(settings, 'NOTIFICACIONES_SISTEMA_HABILITADAS', False):
            return []
        ids = _ids_usuarios(usuarios, excluir)
        if not ids:
            return []
        from realtime.models import NotificacionSistema

        creadas = []
        with transaction.atomic():
            for uid in ids:
                if clave_dedup and NotificacionSistema.objects.filter(
                    id_usuario_destino_id=uid, clave_dedup=clave_dedup,
                ).exists():
                    continue
                creadas.append(NotificacionSistema.objects.create(
                    id_usuario_destino_id=uid,
                    event_type=event_type,
                    modulo=modulo,
                    tipo=(tipo or event_type.split('.', 1)[-1].upper())[:50],
                    titulo=str(titulo)[:200],
                    mensaje=str(mensaje),
                    prioridad=prioridad,
                    referencia=referencia or {},
                    clave_dedup=clave_dedup,
                ))
        pares = [(n.id_usuario_destino_id, item_sistema(n)) for n in creadas]
        if pares:
            publicar.al_confirmar(lambda: _entregar(pares))
        return creadas
    except Exception as exc:
        logger.warning('No se pudo crear la notificación %s: %s', event_type, type(exc).__name__)
        if raise_on_error:
            raise
        return []


def entregar_notificaciones_peticion(notificaciones):
    """
    Programa WS + FCM (tras el COMMIT) para filas de notificaciones_peticiones ya creadas.
    Ignora las que no tienen destinatario (id_usuario_destino NULL).
    """
    try:
        pares = []
        for n in notificaciones or ():
            if n is None or not getattr(n, 'id_usuario_destino_id', None):
                continue
            pares.append((n.id_usuario_destino_id, item_peticion(n)))
        if pares:
            publicar.al_confirmar(lambda: _entregar(pares))
        return len(pares)
    except Exception as exc:
        logger.warning('No se pudo programar la entrega de notificaciones de petición: %s', type(exc).__name__)
        return 0


def alerta_operativa(event_type, titulo, mensaje, *, nivel='warning', roles=(), usuarios=(),
                     referencia=None, clave_unica=None):
    """AO: solo WS, tras el COMMIT. `clave_unica` evita repetirla dentro de la misma transacción."""
    try:
        eventos.modulo_de(event_type)
        if nivel not in eventos.NIVELES_AO:
            nivel = 'warning'
        data = {'titulo': str(titulo)[:200], 'mensaje': str(mensaje)[:1000], 'nivel': nivel,
                'referencia': referencia or {}}
        ids = _ids_usuarios(usuarios)
        roles = list(roles or ())
        publicar.al_confirmar(
            lambda: publicar.publicar_evento(event_type, eventos.AO, data, usuarios=ids, roles=roles),
            clave_unica=clave_unica,
        )
    except Exception as exc:
        logger.warning('Alerta operativa %s no programada: %s', event_type, type(exc).__name__)


def actualizacion_silenciosa(event_type, *, accion='actualizado', roles=(), usuarios=(), referencia=None):
    """AS: solo WS, tras el COMMIT, coalescida por (evento, destinatarios) dentro de la transacción."""
    try:
        eventos.modulo_de(event_type)
        ids = _ids_usuarios(usuarios)
        roles = sorted(set(roles or ()))
        data = {'accion': accion, 'referencia': referencia or {}}
        clave = ('AS', event_type, tuple(ids), tuple(roles))
        publicar.al_confirmar(
            lambda: publicar.publicar_evento(event_type, eventos.AS, data, usuarios=ids, roles=roles),
            clave_unica=clave,
        )
    except Exception as exc:
        logger.warning('Actualización silenciosa %s no programada: %s', event_type, type(exc).__name__)


def emitir_lectura(id_usuario, clave=None, modulo=None, todas=False):
    """SYNC-01: sincroniza las otras pestañas/dispositivos del mismo usuario tras el COMMIT."""
    if todas:
        event_type, data = 'notificacion.todas_leidas', {'modulo': modulo}
    else:
        event_type, data = 'notificacion.leida', {'clave': clave}
    publicar.al_confirmar(
        lambda: publicar.publicar_evento(event_type, eventos.AS, data, usuarios=[id_usuario]),
    )
