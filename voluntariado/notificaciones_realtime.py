"""
Eventos en tiempo real del módulo de voluntariado (contrato v1, §4).

- VOL-01 NP `voluntariado.postulacion_creada`  usuarios activos rol 1 al postularse (ESTRICTA).
- VOL-02 NP `voluntariado.postulacion_resuelta` usuario postulante al aprobar/rechazar (ESTRICTA).
- VOL-03 AS `voluntariado.evento_actualizado` NO se emite aquí: sale de realtime/senales.py.

Las NP se crean dentro de la transacción de la vista (realtime.notificar, raise_on_error=True):
si la NP falla, la postulación/cambio de estado se revierte. WS/FCM salen tras el COMMIT.
Textos sin PII: nunca nombre, correo, identificación ni teléfono del postulante; referencia solo con ids.
"""
import logging

from realtime import notificar

logger = logging.getLogger('realtime')

ROL_ADMINISTRADOR = 1

EV_POSTULACION_CREADA = 'voluntariado.postulacion_creada'
EV_POSTULACION_RESUELTA = 'voluntariado.postulacion_resuelta'

RESULTADO_APROBADA = 'aprobada'
RESULTADO_RECHAZADA = 'rechazada'

# `estado` es texto libre (CharField, default 'PENDIENTE'); se aceptan variantes de género/número.
_PREFIJOS_APROBADA = ('APROB', 'ACEPT')
_PREFIJOS_RECHAZADA = ('RECHAZ',)


def clasificar_estado(estado):
    """'aprobada', 'rechazada' o None (PENDIENTE u otros estados que no resuelven la postulación)."""
    valor = str(estado or '').strip().upper()
    if valor.startswith(_PREFIJOS_APROBADA):
        return RESULTADO_APROBADA
    if valor.startswith(_PREFIJOS_RECHAZADA):
        return RESULTADO_RECHAZADA
    return None


def _titulo_evento(postulacion):
    try:
        evento = postulacion.evento
    except Exception:
        return None
    titulo = getattr(evento, 'titulo', None) if evento is not None else None
    return str(titulo)[:150] if titulo else None


def postulacion_creada(postulacion, actor=None):
    """VOL-01: NP ESTRICTA a usuarios activos rol 1 (excluye al actor)."""
    jornada = _titulo_evento(postulacion)
    mensaje = (
        f"Se registró una nueva postulación para la jornada «{jornada}»."
        if jornada else 'Se registró una nueva postulación a una jornada de voluntariado.'
    )
    return notificar.notificar_usuarios(
        EV_POSTULACION_CREADA,
        notificar.usuarios_activos_por_rol([ROL_ADMINISTRADOR]),
        'Nueva postulación de voluntariado',
        mensaje,
        tipo='POSTULACION_CREADA',
        referencia={'id_postulacion': postulacion.pk, 'id_evento': getattr(postulacion, 'evento_id', None)},
        excluir=[actor] if actor is not None else (),
        raise_on_error=True,
    )


def postulacion_resuelta(postulacion, estado_anterior, actor=None):
    """
    VOL-02: NP ESTRICTA al usuario postulante cuando la postulación pasa a aprobada o rechazada.
    No notifica si la clasificación no cambia (p. ej. guardar de nuevo la misma aprobación) ni si la
    postulación no tiene usuario vinculado (postulación solo por correo: no hay bandeja que notificar).
    """
    resultado = clasificar_estado(postulacion.estado)
    if resultado is None or resultado == clasificar_estado(estado_anterior):
        return []
    id_usuario = getattr(postulacion, 'usuario_id', None)
    if not id_usuario:
        logger.info('VOL-02 omitida: la postulación #%s no tiene usuario vinculado', postulacion.pk)
        return []
    jornada = _titulo_evento(postulacion)
    verbo = 'aprobada' if resultado == RESULTADO_APROBADA else 'rechazada'
    destino = f" a la jornada «{jornada}»" if jornada else ''
    return notificar.notificar_usuarios(
        EV_POSTULACION_RESUELTA,
        [id_usuario],
        f"Postulación {verbo}",
        f"Tu postulación{destino} fue {verbo}.",
        tipo=f'POSTULACION_{resultado.upper()}',
        referencia={
            'id_postulacion': postulacion.pk,
            'id_evento': getattr(postulacion, 'evento_id', None),
            'estado': str(postulacion.estado or '')[:20],
        },
        excluir=[actor] if actor is not None else (),
        raise_on_error=True,
    )
