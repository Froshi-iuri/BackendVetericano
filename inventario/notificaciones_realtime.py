"""
Eventos en tiempo real del inventario general (bodega CBA) y del kit veterinario.
Contrato: _plan_websockets/contrato/CONTRATO_NOTIFICACIONES_V1.md (§4).

Eventos que se emiten desde aquí (los servicios/vistas solo hacen llamadas de 1-3 líneas):
- KIT-01 parte NP  `kit.stock_actualizado`        asignación/retiro por gestor -> vet titular (TOLERANTE).
- KIT-02 AO        `kit.alerta_stock_vencimiento` tras un movimiento del kit que CRUZA un umbral.
- KIT-02 NP        `kit.alerta_stock_vencimiento` barrido diario (manage.py alertas_vencimiento).
- INV-02 AO        `inventario.alerta_bodega`     tras compras, bajas, salidas, ajustes y asignaciones a kit.
- KIT-03 NP        `kit.solicitud_creada`         gestores activos (rol 1 y 2) (ESTRICTA).
- KIT-04 NP + AS   `kit.solicitud_resuelta`       NP vet solicitante (ESTRICTA) + AS rol_1/rol_2.
- KIT-05 AS        `kit.solicitud_cancelada`      rol_1/rol_2.
- KIT-06 NP + AO   `kit.conflicto_sincronizacion` gestores activos + vet (ESTRICTA) + AO rol_1/rol_2.

INV-01 y la parte AS de KIT-01 NO se emiten aquí: salen de realtime/senales.py (post_save).

Garantías:
- Las NP se crean dentro de la transacción de negocio del llamador (realtime.notificar).
  ESTRICTA = raise_on_error=True: si la NP falla, la operación de negocio se revierte.
  TOLERANTE = raise_on_error=False y toda consulta auxiliar corre en un SAVEPOINT propio: un fallo
  nunca deja abortada la transacción de PostgreSQL del llamador ni cambia su respuesta.
- AO/AS: solo WS y después del COMMIT (realtime.notificar las programa con on_commit).
- Textos sin datos clínicos ni PII de terceros; `referencia` solo con ids y códigos.

Umbrales (inventario/constants.py, sin modificarlos):
- Kit (igual que consultar_alertas_kit): agotado = 0, crítico = < STOCK_UMBRAL_CRITICO (5),
  bajo = < STOCK_UMBRAL_BAJO (10); vencido = fecha < hoy; próximo = hoy <= fecha <= hoy + 30 días.
- Bodega (contrato INV-02): agotado = 0, bajo = <= STOCK_UMBRAL_BAJO (10). Ojo: el contrato usa "≤10"
  para bodega mientras que el kit usa "< 10" (constants: "menos de 10"); se respeta cada uno.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q

from inventario.constants import (
    INVENTARIO_GESTOR_ROLES,
    SOLICITUD_ESTADO_APROBADA,
    SOLICITUD_ESTADO_PARCIAL,
    SOLICITUD_ESTADO_RECHAZADA,
    SOLICITUD_TIPO_REPOSICION,
    STOCK_UMBRAL_BAJO,
    STOCK_UMBRAL_CRITICO,
    VENTANA_DIAS_VENCIMIENTO,
)
from inventario.models import KitVeterinarioLotes, VInventarioGeneralLotes
from realtime import notificar

logger = logging.getLogger('realtime')

ROLES_GESTION = tuple(INVENTARIO_GESTOR_ROLES)  # (1, 2)

EV_KIT_STOCK = 'kit.stock_actualizado'
EV_KIT_ALERTA = 'kit.alerta_stock_vencimiento'
EV_BODEGA_ALERTA = 'inventario.alerta_bodega'
EV_SOLICITUD_CREADA = 'kit.solicitud_creada'
EV_SOLICITUD_RESUELTA = 'kit.solicitud_resuelta'
EV_SOLICITUD_CANCELADA = 'kit.solicitud_cancelada'
EV_CONFLICTO = 'kit.conflicto_sincronizacion'

NIVEL_AGOTADO = 'agotado'
NIVEL_CRITICO = 'critico'
NIVEL_BAJO = 'bajo'
UMBRAL_VENCIDO = 'vencido'
UMBRAL_PROXIMO = 'proximo_a_vencer'

_SEVERIDAD = {None: 0, NIVEL_BAJO: 1, NIVEL_CRITICO: 2, NIVEL_AGOTADO: 3}


# ============================================================================
# Utilidades
# ============================================================================

def _hoy():
    # Import diferido: kit_service importa este módulo.
    from inventario.services.kit_service import _obtener_fecha_hoy

    return _obtener_fecha_hoy()


def _ids(valores):
    """Enteros > 0 únicos conservando el orden (acepta '12'; descarta None, bool y no enteros)."""
    vistos = []
    for v in valores or ():
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            continue
        if v not in vistos:
            vistos.append(v)
    return vistos


def _tolerante(etiqueta, func):
    """
    Ejecuta `func` en un SAVEPOINT propio. Si algo falla se registra (sin datos sensibles) y se
    devuelve None: la transacción del llamador sigue usable y su respuesta no cambia.
    """
    try:
        with transaction.atomic():
            return func()
    except Exception as exc:
        logger.warning('realtime inventario: %s omitido (%s)', etiqueta, type(exc).__name__)
        return None


def nivel_stock_kit(saldo_vigente):
    """Mismo criterio que kit_consultas.consultar_alertas_kit."""
    saldo = saldo_vigente or 0
    if saldo <= 0:
        return NIVEL_AGOTADO
    if saldo < STOCK_UMBRAL_CRITICO:
        return NIVEL_CRITICO
    if saldo < STOCK_UMBRAL_BAJO:
        return NIVEL_BAJO
    return None


def nivel_stock_bodega(total_disponible):
    """Contrato INV-02: 0 o ≤10 (STOCK_UMBRAL_BAJO, comparación inclusiva)."""
    total = total_disponible or 0
    if total <= 0:
        return NIVEL_AGOTADO
    if total <= STOCK_UMBRAL_BAJO:
        return NIVEL_BAJO
    return None


def umbral_vencimiento(fecha_vencimiento, hoy):
    """'vencido' (< hoy), 'proximo_a_vencer' (hoy..hoy+30, inclusivo) o None."""
    if fecha_vencimiento is None:
        return None
    if fecha_vencimiento < hoy:
        return UMBRAL_VENCIDO
    if fecha_vencimiento <= hoy + timedelta(days=VENTANA_DIAS_VENCIMIENTO):
        return UMBRAL_PROXIMO
    return None


# ============================================================================
# KIT-02 (AO tras movimiento): captura previa + evaluación posterior
# ============================================================================

def _filas_kit(id_veterinario, med_ids):
    return list(
        KitVeterinarioLotes.objects.filter(
            id_veterinario=id_veterinario,
            id_medicamento__in=list(med_ids),
        ).filter(
            Q(cantidad_referencia__gt=0) | Q(cantidad_actual__gt=0)
        ).values('id_kit_lote', 'id_medicamento', 'cantidad_actual', 'fecha_vencimiento')
    )


def estado_kit(filas, hoy):
    """
    filas: dicts con id_kit_lote, id_medicamento, cantidad_actual, fecha_vencimiento.
    Devuelve {'niveles': {id_med: nivel|None}, 'lotes': {id_kit_lote: (id_med, umbral)}}.
    """
    saldos = {}
    lotes = {}
    for f in filas:
        med = f['id_medicamento']
        cantidad = f['cantidad_actual'] or 0
        fecha = f['fecha_vencimiento']
        saldos.setdefault(med, 0)
        if fecha is None or fecha >= hoy:
            saldos[med] += cantidad
        if cantidad > 0:
            umbral = umbral_vencimiento(fecha, hoy)
            if umbral:
                lotes[f['id_kit_lote']] = (med, umbral)
    return {'niveles': {m: nivel_stock_kit(s) for m, s in saldos.items()}, 'lotes': lotes}


def capturar_estado_kit(id_veterinario, med_ids):
    """Foto del kit ANTES del movimiento (para detectar el cruce de umbral). Tolerante."""
    meds = _ids(med_ids)
    if not id_veterinario or not meds:
        return None

    def _capturar():
        estado = estado_kit(_filas_kit(id_veterinario, meds), _hoy())
        estado['med_ids'] = meds
        return estado

    return _tolerante('captura KIT-02', _capturar)


def cruces_kit(previo, posterior):
    """Medicamentos cuyo nivel empeoró y lotes que entraron (o cambiaron) de umbral de vencimiento."""
    niveles_previos = previo.get('niveles', {})
    lotes_previos = previo.get('lotes', {})
    stock = {
        med: nivel for med, nivel in posterior['niveles'].items()
        if nivel and _SEVERIDAD[nivel] > _SEVERIDAD.get(niveles_previos.get(med), 0)
    }
    vencimiento = {
        kl: (med, umbral) for kl, (med, umbral) in posterior['lotes'].items()
        if lotes_previos.get(kl, (None, None))[1] != umbral
    }
    return stock, vencimiento


def _texto_alerta_kit(id_veterinario, stock, vencimiento):
    cuenta = {n: sum(1 for v in stock.values() if v == n) for n in (NIVEL_AGOTADO, NIVEL_CRITICO, NIVEL_BAJO)}
    vencidos = sum(1 for _, u in vencimiento.values() if u == UMBRAL_VENCIDO)
    proximos = sum(1 for _, u in vencimiento.values() if u == UMBRAL_PROXIMO)
    partes = []
    if cuenta[NIVEL_AGOTADO]:
        partes.append(f"{cuenta[NIVEL_AGOTADO]} medicamento(s) agotado(s)")
    if cuenta[NIVEL_CRITICO]:
        partes.append(f"{cuenta[NIVEL_CRITICO]} en nivel crítico (<{STOCK_UMBRAL_CRITICO})")
    if cuenta[NIVEL_BAJO]:
        partes.append(f"{cuenta[NIVEL_BAJO]} con stock bajo (<{STOCK_UMBRAL_BAJO})")
    if vencidos:
        partes.append(f"{vencidos} lote(s) vencido(s)")
    if proximos:
        partes.append(f"{proximos} lote(s) por vencer en {VENTANA_DIAS_VENCIMIENTO} días o menos")
    nivel = 'danger' if (cuenta[NIVEL_AGOTADO] or cuenta[NIVEL_CRITICO] or vencidos) else 'warning'
    mensaje = f"Kit del veterinario #{id_veterinario}: " + ', '.join(partes) + '.'
    return 'Alerta de stock o vencimiento en kit', mensaje, nivel


def alertar_kit_tras_movimiento(id_veterinario, estado_previo):
    """KIT-02 AO a user_<vet> + rol_1/rol_2 si el kit cruzó un umbral. Tolerante; WS tras el COMMIT."""
    if not estado_previo or not id_veterinario:
        return None

    def _evaluar():
        posterior = estado_kit(_filas_kit(id_veterinario, estado_previo['med_ids']), _hoy())
        stock, vencimiento = cruces_kit(estado_previo, posterior)
        if not stock and not vencimiento:
            return False
        titulo, mensaje, nivel = _texto_alerta_kit(id_veterinario, stock, vencimiento)
        notificar.alerta_operativa(
            EV_KIT_ALERTA, titulo, mensaje,
            nivel=nivel,
            usuarios=[id_veterinario],
            roles=ROLES_GESTION,
            referencia={
                'id_veterinario': id_veterinario,
                'id_medicamentos': sorted(set(stock) | {m for m, _ in vencimiento.values()}),
                'id_kit_lotes': sorted(vencimiento),
            },
            clave_unica=('KIT-02', int(id_veterinario)),
        )
        return True

    return _tolerante('AO KIT-02', _evaluar)


# ============================================================================
# INV-02 (AO bodega)
# ============================================================================

def _medicamentos_de_lotes(inventario_ids):
    return _ids(
        VInventarioGeneralLotes.objects.filter(id_inventario__in=list(inventario_ids))
        .values_list('id_medicamento', flat=True)
    )


def medicamentos_de_lotes(inventario_ids):
    """Ids de medicamento de los lotes de bodega indicados (p. ej. antes de borrar un lote). Tolerante."""
    ids = _ids(inventario_ids)
    if not ids:
        return []
    return _tolerante('medicamentos de lotes INV-02', lambda: _medicamentos_de_lotes(ids)) or []


def _filas_bodega(med_ids):
    return list(
        VInventarioGeneralLotes.objects.filter(id_medicamento__in=list(med_ids)).values(
            'id_inventario', 'id_medicamento', 'cantidad_actual', 'fecha_vencimiento',
            'disponible_para_asignar',
        )
    )


def evaluar_bodega(filas, med_ids, inventario_ids, hoy):
    """
    Stock por medicamento afectado (suma de lotes `disponible_para_asignar`) y vencimiento por lote
    afectado (cantidad > 0). Devuelve ({id_med: nivel}, {id_inventario: (id_med, umbral)}).
    """
    totales = {m: 0 for m in med_ids}
    vencimiento = {}
    lotes_afectados = set(inventario_ids)
    for f in filas:
        med = f['id_medicamento']
        cantidad = f['cantidad_actual'] or 0
        if med in totales and f['disponible_para_asignar']:
            totales[med] += cantidad
        if f['id_inventario'] in lotes_afectados and cantidad > 0:
            umbral = umbral_vencimiento(f['fecha_vencimiento'], hoy)
            if umbral:
                vencimiento[f['id_inventario']] = (med, umbral)
    niveles = {m: n for m, n in ((m, nivel_stock_bodega(t)) for m, t in totales.items()) if n}
    return niveles, vencimiento


def alertar_bodega(med_ids=(), inventario_ids=()):
    """INV-02 AO a rol_1/rol_2 si los medicamentos/lotes afectados quedan en 0, ≤10 o vencen en ≤30 días."""
    meds_entrada = _ids(med_ids)
    lotes = _ids(inventario_ids)
    if not meds_entrada and not lotes:
        return None

    def _evaluar():
        meds = list(meds_entrada)
        for m in (_medicamentos_de_lotes(lotes) if lotes else []):
            if m not in meds:
                meds.append(m)
        if not meds:
            return False
        niveles, vencimiento = evaluar_bodega(_filas_bodega(meds), meds, lotes, _hoy())
        if not niveles and not vencimiento:
            return False
        agotados = sum(1 for n in niveles.values() if n == NIVEL_AGOTADO)
        bajos = sum(1 for n in niveles.values() if n == NIVEL_BAJO)
        vencidos = sum(1 for _, u in vencimiento.values() if u == UMBRAL_VENCIDO)
        proximos = sum(1 for _, u in vencimiento.values() if u == UMBRAL_PROXIMO)
        partes = []
        if agotados:
            partes.append(f"{agotados} medicamento(s) agotado(s)")
        if bajos:
            partes.append(f"{bajos} medicamento(s) con {STOCK_UMBRAL_BAJO} unidades o menos")
        if vencidos:
            partes.append(f"{vencidos} lote(s) vencido(s) con existencias")
        if proximos:
            partes.append(f"{proximos} lote(s) por vencer en {VENTANA_DIAS_VENCIMIENTO} días o menos")
        notificar.alerta_operativa(
            EV_BODEGA_ALERTA,
            'Alerta de bodega',
            'Bodega general: ' + ', '.join(partes) + '.',
            nivel='danger' if (agotados or vencidos) else 'warning',
            roles=ROLES_GESTION,
            referencia={
                'id_medicamentos': sorted(set(niveles) | {m for m, _ in vencimiento.values() if m}),
                'id_inventarios': sorted(vencimiento),
            },
            clave_unica=('INV-02', tuple(sorted(meds)), tuple(sorted(lotes))),
        )
        return True

    return _tolerante('AO INV-02', _evaluar)


# ============================================================================
# KIT-01 parte NP (gestor -> vet titular). TOLERANTE.
# ============================================================================

def _np_kit_por_gestor(id_gestor, id_veterinario, accion, med_ids, unidades):
    meds = sorted(set(_ids(med_ids)))
    if accion == 'asignacion':
        titulo, tipo = 'Stock asignado a tu kit', 'ASIGNACION_KIT'
        mensaje = f"Se asignaron {unidades} unidad(es) de {len(meds)} medicamento(s) a tu kit."
    else:
        titulo, tipo = 'Stock retirado de tu kit', 'RETIRO_KIT'
        mensaje = f"Se retiraron {unidades} unidad(es) de {len(meds)} medicamento(s) de tu kit."
    try:
        return notificar.notificar_usuarios(
            EV_KIT_STOCK, [id_veterinario], titulo, mensaje,
            tipo=tipo,
            referencia={'id_veterinario': id_veterinario, 'id_medicamentos': meds},
            excluir=[id_gestor],
            raise_on_error=False,
        )
    except Exception as exc:  # notificar ya es tolerante; doble red por seguridad
        logger.warning('realtime inventario: NP KIT-01 omitida (%s)', type(exc).__name__)
        return []


def tras_asignacion_kit(id_gestor, id_veterinario, resultado, estado_previo, id_solicitud=None):
    """
    Llamar al final de kit_service.asignar_stock_a_kit (dentro de su transacción).
    - KIT-01 NP al vet solo en la asignación directa del gestor: si viene de aprobar una solicitud
      (id_solicitud), el vet ya recibe KIT-04 `kit.solicitud_resuelta` y no se duplica la bandeja.
    - KIT-02 AO si el kit cruzó un umbral. INV-02 AO por los lotes de bodega descontados.
    """
    resultado = resultado or []
    if id_solicitud is None:
        _np_kit_por_gestor(
            id_gestor, id_veterinario, 'asignacion',
            [r.get('id_medicamento') for r in resultado],
            sum(r.get('cantidad_total_asignada') or 0 for r in resultado),
        )
    alertar_kit_tras_movimiento(id_veterinario, estado_previo)
    alertar_bodega(
        med_ids=[r.get('id_medicamento') for r in resultado],
        inventario_ids=[l.get('id_inventario') for r in resultado for l in (r.get('lotes_asignados') or [])],
    )


def tras_retiro_kit(id_gestor, id_veterinario, resultado, estado_previo):
    """Llamar al final de kit_service.retirar_stock_de_kit: KIT-01 NP (tolerante) + KIT-02 AO."""
    resultado = resultado or {}
    _np_kit_por_gestor(
        id_gestor, id_veterinario, 'retiro',
        [resultado.get('id_medicamento')], resultado.get('cantidad_total_retirada') or 0,
    )
    alertar_kit_tras_movimiento(id_veterinario, estado_previo)


# ============================================================================
# KIT-06 (consumo offline/clínico RECHAZADO_CONFLICTO). NP ESTRICTA + AO.
# ============================================================================

def conflicto_sincronizacion(id_veterinario, conflictos_nuevos):
    """
    NP a gestores activos (rol 1 y 2) y al vet titular, en la MISMA transacción que crea los registros
    RECHAZADO_CONFLICTO (estricta: si la NP falla, se revierte también el registro del conflicto).
    El vet se incluye aunque sea quien sincroniza: el contrato lo nombra destinatario y la
    sincronización offline corre en segundo plano (WorkManager), sin nadie mirando la respuesta 409.
    """
    conflictos_nuevos = list(conflictos_nuevos or ())
    if not conflictos_nuevos:
        return []
    ids_consumo = sorted(set(_ids([c.get('id_consumo') for c in conflictos_nuevos])))
    meds = sorted(set(_ids([c.get('id_medicamento') for c in conflictos_nuevos])))
    titulo = 'Conflicto de sincronización del kit'
    mensaje = (
        f"{len(conflictos_nuevos)} consumo(s) del kit del veterinario #{id_veterinario} quedaron "
        "rechazados por falta de stock vigente y requieren conciliación."
    )
    referencia = {'id_veterinario': id_veterinario, 'id_consumos': ids_consumo, 'id_medicamentos': meds}
    destinatarios = list(notificar.usuarios_activos_por_rol(ROLES_GESTION)) + [id_veterinario]
    creadas = notificar.notificar_usuarios(
        EV_CONFLICTO, destinatarios, titulo, mensaje,
        tipo='CONFLICTO_SINCRONIZACION',
        referencia=referencia,
        raise_on_error=True,
    )
    notificar.alerta_operativa(
        EV_CONFLICTO, titulo, mensaje,
        nivel='danger',
        roles=ROLES_GESTION,
        referencia=referencia,
        clave_unica=('KIT-06', int(id_veterinario)),
    )
    return creadas


def tras_consumo_kit(id_veterinario, conflictos, estado_previo):
    """
    Llamar al final de consumo_service.descontar_consumo_kit (dentro de su transacción).
    Solo los conflictos NUEVOS (idempotente=False) generan KIT-06; las repeticiones idempotentes no.
    """
    nuevos = [c for c in (conflictos or ()) if not c.get('idempotente')]
    conflicto_sincronizacion(id_veterinario, nuevos)
    alertar_kit_tras_movimiento(id_veterinario, estado_previo)


# ============================================================================
# KIT-03 / KIT-04 / KIT-05 (solicitudes de kit)
# ============================================================================

def _nombre_tipo(tipo_solicitud):
    return 'reposición' if tipo_solicitud == SOLICITUD_TIPO_REPOSICION else 'devolución'


def solicitud_kit_creada(id_solicitud, tipo_solicitud, id_veterinario, total_items):
    """KIT-03 NP ESTRICTA a usuarios activos rol 1 y 2 (excluye al vet que la crea)."""
    return notificar.notificar_usuarios(
        EV_SOLICITUD_CREADA,
        notificar.usuarios_activos_por_rol(ROLES_GESTION),
        f"Nueva solicitud de {_nombre_tipo(tipo_solicitud)} de kit",
        f"El veterinario #{id_veterinario} creó la solicitud #{id_solicitud} de "
        f"{_nombre_tipo(tipo_solicitud)} con {total_items} medicamento(s).",
        tipo=f'SOLICITUD_{tipo_solicitud}',
        referencia={'id_solicitud': id_solicitud, 'tipo_solicitud': tipo_solicitud, 'id_veterinario': id_veterinario},
        excluir=[id_veterinario],
        raise_on_error=True,
    )


_TEXTO_RESOLUCION = {
    SOLICITUD_ESTADO_APROBADA: ('Solicitud de kit aprobada', 'fue aprobada'),
    SOLICITUD_ESTADO_PARCIAL: ('Solicitud de kit aprobada parcialmente', 'fue aprobada parcialmente'),
    SOLICITUD_ESTADO_RECHAZADA: ('Solicitud de kit rechazada', 'fue rechazada'),
}


def solicitud_kit_resuelta(id_solicitud, tipo_solicitud, estado, id_veterinario, id_gestor):
    """KIT-04: NP ESTRICTA al vet solicitante (excluye al gestor que resuelve) + AS a rol_1/rol_2."""
    titulo, verbo = _TEXTO_RESOLUCION.get(estado, ('Solicitud de kit resuelta', 'fue resuelta'))
    creadas = notificar.notificar_usuarios(
        EV_SOLICITUD_RESUELTA,
        [id_veterinario],
        titulo,
        f"Tu solicitud #{id_solicitud} de {_nombre_tipo(tipo_solicitud)} de kit {verbo}.",
        tipo=f'SOLICITUD_{estado}',
        referencia={'id_solicitud': id_solicitud, 'tipo_solicitud': tipo_solicitud, 'estado': estado},
        excluir=[id_gestor],
        raise_on_error=True,
    )
    notificar.actualizacion_silenciosa(
        EV_SOLICITUD_RESUELTA,
        accion='actualizado',
        roles=ROLES_GESTION,
        referencia={'id_solicitud': id_solicitud, 'estado': estado},
    )
    return creadas


def solicitud_kit_cancelada(id_solicitud, id_veterinario):
    """KIT-05 AS a rol_1/rol_2 (sin texto de bandeja)."""
    notificar.actualizacion_silenciosa(
        EV_SOLICITUD_CANCELADA,
        accion='actualizado',
        roles=ROLES_GESTION,
        referencia={'id_solicitud': id_solicitud, 'id_veterinario': id_veterinario, 'estado': 'CANCELADA'},
    )


# ============================================================================
# KIT-02 NP: barrido diario de vencimientos (manage.py alertas_vencimiento)
# ============================================================================

def candidatos_vencimiento(hoy=None):
    """
    Lotes con existencias vencidos o por vencer (≤30 días) en kits y en bodega.
    Devuelve dicts: ambito ('kit'|'bodega'), id_lote, id_inventario, id_medicamento, nombre_medicamento,
    id_veterinario (solo kit), cantidad, fecha_vencimiento, umbral.
    La fecha "hoy" se calcula en Python (igual que el kit) y no con CURRENT_DATE de la vista.
    """
    hoy = hoy or _hoy()
    limite = hoy + timedelta(days=VENTANA_DIAS_VENCIMIENTO)
    candidatos = []
    kit_qs = KitVeterinarioLotes.objects.filter(
        cantidad_actual__gt=0,
        fecha_vencimiento__isnull=False,
        fecha_vencimiento__lte=limite,
    ).select_related('id_medicamento').order_by('fecha_vencimiento', 'id_kit_lote')
    for kl in kit_qs:
        candidatos.append({
            'ambito': 'kit',
            'id_lote': kl.id_kit_lote,
            'id_inventario': kl.id_inventario_id,
            'id_medicamento': kl.id_medicamento_id,
            'nombre_medicamento': getattr(kl.id_medicamento, 'nombre', None),
            'id_veterinario': kl.id_veterinario_id,
            'cantidad': kl.cantidad_actual,
            'fecha_vencimiento': kl.fecha_vencimiento,
            'umbral': umbral_vencimiento(kl.fecha_vencimiento, hoy),
        })
    bodega_qs = VInventarioGeneralLotes.objects.filter(
        cantidad_actual__gt=0,
        fecha_vencimiento__isnull=False,
        fecha_vencimiento__lte=limite,
    ).order_by('fecha_vencimiento', 'id_inventario')
    for lote in bodega_qs:
        candidatos.append({
            'ambito': 'bodega',
            'id_lote': lote.id_inventario,
            'id_inventario': lote.id_inventario,
            'id_medicamento': lote.id_medicamento_id,
            'nombre_medicamento': lote.nombre_medicamento,
            'id_veterinario': None,
            'cantidad': lote.cantidad_actual,
            'fecha_vencimiento': lote.fecha_vencimiento,
            'umbral': umbral_vencimiento(lote.fecha_vencimiento, hoy),
        })
    return [c for c in candidatos if c['umbral']]


def clave_vencimiento(candidato):
    """Clave de deduplicación estable por ámbito + lote + umbral + fecha de vencimiento (≤150)."""
    prefijo = 'kit' if candidato['ambito'] == 'kit' else 'bod'
    fecha = candidato['fecha_vencimiento'].isoformat() if candidato['fecha_vencimiento'] else 'sin_fecha'
    return f"venc:{prefijo}:{candidato['id_lote']}:{candidato['umbral']}:{fecha}"[:150]


def texto_vencimiento(candidato):
    nombre = candidato.get('nombre_medicamento') or f"medicamento #{candidato.get('id_medicamento')}"
    fecha = candidato['fecha_vencimiento'].isoformat() if candidato['fecha_vencimiento'] else '-'
    if candidato['ambito'] == 'kit':
        donde = f"del kit del veterinario #{candidato['id_veterinario']}"
    else:
        donde = 'de la bodega general'
    lote = f"Lote #{candidato['id_inventario']} de {nombre} ({candidato['cantidad']} uds) {donde}"
    if candidato['umbral'] == UMBRAL_VENCIDO:
        return 'Lote vencido', f"{lote} venció el {fecha}."
    return 'Lote próximo a vencer', f"{lote} vence el {fecha}."


def referencia_vencimiento(candidato):
    referencia = {
        'ambito': candidato['ambito'],
        'id_inventario': candidato['id_inventario'],
        'id_medicamento': candidato['id_medicamento'],
        'umbral': candidato['umbral'],
    }
    if candidato['ambito'] == 'kit':
        referencia['id_kit_lote'] = candidato['id_lote']
        referencia['id_veterinario'] = candidato['id_veterinario']
    return referencia
