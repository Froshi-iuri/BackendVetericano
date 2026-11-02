import os
import logging
import requests

logger = logging.getLogger(__name__)

CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', 'aefeig5y')
CLOUDINARY_UPLOAD_PRESET = os.environ.get('CLOUDINARY_UPLOAD_PRESET', 'preset_android')


def subir_o_asegurar_cloudinary(imagen_str: str) -> str:
    """
    Recibe una imagen que puede ser:
    1. Una URL existente (http:// o https://): se conserva directamente.
    2. Una cadena base64 o data URI: la sube a Cloudinary vía REST API y retorna la secure_url.
    3. None o cadena vacía: retorna None.
    
    Garantiza que la URL resultante nunca supere los 500 caracteres (límite de ruta_archivo en BD).
    """
    if not imagen_str:
        return None

    valor = str(imagen_str).strip()
    if not valor:
        return None

    # Si ya es una URL web válida, conservarla
    if valor.startswith('http://') or valor.startswith('https://'):
        return valor[:500]

    # Si es una imagen en base64 (o data URI), subir a Cloudinary
    try:
        url_api = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/auto/upload"
        payload = {
            'file': valor,
            'upload_preset': CLOUDINARY_UPLOAD_PRESET
        }
        resp = requests.post(url_api, data=payload, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            secure_url = data.get('secure_url') or data.get('url')
            if secure_url:
                return str(secure_url)[:500]
        else:
            logger.warning(f"Error en respuesta de Cloudinary (HTTP {resp.status_code}): {resp.text[:200]}")
    except Exception as exc:
        logger.error(f"Excepción al subir imagen a Cloudinary: {exc}")

    # Fallback defensivo si era un path relativo corto
    if len(valor) <= 500 and not valor.startswith('data:'):
        return valor

    return None


def detectar_tipo_archivo(url_o_str: str) -> str:
    """
    Detecta automáticamente si un recurso es 'video' o 'imagen' basándose en:
    1. Si contiene la ruta de Cloudinary para videos (/video/upload/).
    2. Si tiene extensión clásica de video (.mp4, .mov, etc.).
    3. Si contiene prefijos de datos de video (data:video/...).
    Por defecto retorna 'imagen'.
    """
    if not url_o_str:
        return 'imagen'

    val = str(url_o_str).strip().lower()
    if val.startswith('data:video/'):
        return 'video'
    if '/video/upload/' in val:
        return 'video'

    exts_video = ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp', '.m4v')
    url_limpia = val.split('?')[0].split('#')[0]
    if any(url_limpia.endswith(ext) for ext in exts_video):
        return 'video'

    return 'imagen'


def obtener_fotos_acta_con_fallback(seguimiento) -> list:
    """
    Retorna la lista de URLs de fotos para un acta de visita (SeguimientoPeticionesVisita).

    Orden de precedencia y fallback:
    1. Evidencias registradas en la nueva tabla dedicada EvidenciaSeguimientoVisita con tipo_archivo != 'video'.
    2. Fallback histórico a EvidenciaPeticiones donde descripcion comienza con 'Acta de visita #{id_seguimiento}'.
    3. Fallback a evidencias generales de la petición asociada (EvidenciaPeticiones).
    4. Fallback a id_peticion.foto (si existe).
    5. Fallback a id_animal.foto_url (si existe).
    6. Lista vacía [].
    """
    if not seguimiento:
        return []

    from peticiones.models import EvidenciaSeguimientoVisita, EvidenciaPeticiones

    id_seguimiento = getattr(seguimiento, 'id_seguimiento', None) or getattr(seguimiento, 'pk', None)

    # 1. Consultar la tabla dedicada EvidenciaSeguimientoVisita (aprovecha prefetch si existe)
    if id_seguimiento:
        evidencias_rel = getattr(seguimiento, 'evidencias', None)
        if evidencias_rel is not None and hasattr(evidencias_rel, 'all'):
            lista_evs = list(evidencias_rel.all())
        else:
            lista_evs = list(
                EvidenciaSeguimientoVisita.objects.filter(id_seguimiento_id=id_seguimiento)
                .order_by('id_evidencia')
            )

        fotos_nuevas = [
            e.ruta_archivo for e in lista_evs
            if e.ruta_archivo and (e.tipo_archivo or 'imagen').lower() != 'video'
            and detectar_tipo_archivo(e.ruta_archivo) != 'video'
        ]
        if fotos_nuevas:
            return fotos_nuevas

    peticion = getattr(seguimiento, 'id_peticion', None)
    peticion_pk = getattr(peticion, 'pk', None) or getattr(seguimiento, 'id_peticion_id', None)

    if not peticion_pk:
        return []

    prefijo_acta = f"Acta de visita #{id_seguimiento}"

    # Si evidenciapeticiones_set ya está en cache de prefetch:
    evidencias_cache = getattr(peticion, '_prefetched_objects_cache', {}).get('evidenciapeticiones_set')
    if evidencias_cache is not None:
        todas_evidencias = [e for e in evidencias_cache if e.ruta_archivo]
    else:
        todas_evidencias = list(
            EvidenciaPeticiones.objects.filter(id_peticion_id=peticion_pk)
            .exclude(ruta_archivo__isnull=True)
            .exclude(ruta_archivo='')
        )

    # 2. Fallback histórico a fotos del acta específica en EvidenciaPeticiones
    fotos_acta = [
        e.ruta_archivo for e in todas_evidencias
        if e.descripcion and e.descripcion.startswith(prefijo_acta)
        and detectar_tipo_archivo(e.ruta_archivo) != 'video'
    ]
    if fotos_acta:
        return [f for f in fotos_acta if f]

    # 3. Fallback a cualquier evidencia de la petición
    fotos_peticion = [e.ruta_archivo for e in todas_evidencias if detectar_tipo_archivo(e.ruta_archivo) != 'video']
    if fotos_peticion:
        return [f for f in fotos_peticion if f]

    # 4. Fallback a peticion.foto
    if peticion and getattr(peticion, 'foto', None):
        foto_str = str(peticion.foto).strip()
        if foto_str and detectar_tipo_archivo(foto_str) != 'video':
            return [foto_str]

    # 5. Fallback a animal.foto_url
    animal = getattr(seguimiento, 'id_animal', None)
    if animal and getattr(animal, 'foto_url', None):
        foto_url = str(animal.foto_url).strip()
        if foto_url:
            return [foto_url]

    return []


def obtener_videos_acta(seguimiento) -> list:
    """
    Retorna la lista de URLs de videos asociados a un acta de visita (SeguimientoPeticionesVisita).
    Busca en EvidenciaSeguimientoVisita y como fallback en EvidenciaPeticiones.
    """
    if not seguimiento:
        return []

    from peticiones.models import EvidenciaSeguimientoVisita, EvidenciaPeticiones

    id_seguimiento = getattr(seguimiento, 'id_seguimiento', None) or getattr(seguimiento, 'pk', None)
    if not id_seguimiento:
        return []

    # 1. Buscar en la nueva tabla dedicada EvidenciaSeguimientoVisita
    evidencias_rel = getattr(seguimiento, 'evidencias', None)
    if evidencias_rel is not None and hasattr(evidencias_rel, 'all'):
        lista_evs = list(evidencias_rel.all())
    else:
        lista_evs = list(
            EvidenciaSeguimientoVisita.objects.filter(id_seguimiento_id=id_seguimiento)
            .order_by('id_evidencia')
        )

    videos = [
        e.ruta_archivo for e in lista_evs
        if e.ruta_archivo and (
            (e.tipo_archivo or '').lower() == 'video'
            or detectar_tipo_archivo(e.ruta_archivo) == 'video'
        )
    ]
    if videos:
        return videos

    # 2. Fallback a EvidenciaPeticiones si había videos con el prefijo
    peticion = getattr(seguimiento, 'id_peticion', None)
    peticion_pk = getattr(peticion, 'pk', None) or getattr(seguimiento, 'id_peticion_id', None)
    if peticion_pk:
        prefijo_acta = f"Acta de visita #{id_seguimiento}"
        evs_peticion = EvidenciaPeticiones.objects.filter(id_peticion_id=peticion_pk)
        videos_legados = [
            e.ruta_archivo for e in evs_peticion
            if e.ruta_archivo and e.descripcion and e.descripcion.startswith(prefijo_acta)
            and detectar_tipo_archivo(e.ruta_archivo) == 'video'
        ]
        if videos_legados:
            return videos_legados

    return []


def obtener_multimedia_acta(seguimiento) -> dict:
    """
    Retorna un diccionario consolidado con todas las fotos, videos y el detalle
    estructurado de evidencias para una visita de campo.
    """
    fotos = obtener_fotos_acta_con_fallback(seguimiento)
    videos = obtener_videos_acta(seguimiento)

    multimedia_lista = []
    for f in fotos:
        multimedia_lista.append({'url': f, 'tipo': 'imagen'})
    for v in videos:
        multimedia_lista.append({'url': v, 'tipo': 'video'})

    return {
        'foto': fotos[0] if fotos else None,
        'fotos': fotos,
        'total_fotos': len(fotos),
        'videos': videos,
        'total_videos': len(videos),
        'multimedia': multimedia_lista,
    }



# ==============================================================================
# UTILIDADES DEL MÓDULO JURÍDICO: ESTADOS, TIEMPOS, HISTORIAL, NOTIFICACIONES Y CORREO
# ==============================================================================

import unicodedata
from datetime import datetime, timezone as dt_timezone, timedelta
from django.utils import timezone


ESTADOS_FLUJO_JURIDICO = [
    'En revisión',
    'Asignada',
    'En Proceso',
    'Atendida',
    'En tratamiento',
    'Finalizada',
]

ESTADOS_TERMINALES_NORM = {
    'finalizada',
    'resuelta',
    'cancelada',
    'rechazada',
    'alta medica',
    'fallecido',
}

DIAS_PARA_REASIGNAR = 5


def normalizar_texto_juridico(texto) -> str:
    if not texto:
        return ""
    return ''.join(
        c for c in unicodedata.normalize('NFD', str(texto))
        if unicodedata.category(c) != 'Mn'
    ).lower().strip()


def es_prioridad_urgente(prioridad) -> bool:
    """
    Determina si la prioridad de una petición corresponde a una petición urgente.
    Soporta valores como 'Urgente', 'Alta', 'Crítica', 'Emergencia' (insensible a mayúsculas/tildes).
    """
    norm = normalizar_texto_juridico(prioridad)
    if not norm:
        return False
    return norm in ('urgente', 'alta', 'critica', 'emergencia') or 'urgent' in norm or 'critic' in norm


def _a_naive_local(dt):
    """
    Convierte cualquier datetime (con o sin tzinfo) a datetime naive en hora local (America/Bogota, UTC-5)
    para evitar errores al restar fechas con USE_TZ=False y columnas timestamptz en PostgreSQL.
    """
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is not None:
        bogota_tz = dt_timezone(timedelta(hours=-5))
        return dt.astimezone(bogota_tz).replace(tzinfo=None)
    return dt


def formatear_duracion_humana(fecha_inicio, ahora=None) -> str:
    """
    Devuelve una representación legible del tiempo transcurrido desde fecha_inicio hasta ahora.
    """
    inicio = _a_naive_local(fecha_inicio)
    if not inicio:
        return "Sin registro"
    fin = _a_naive_local(ahora or timezone.now())
    delta = fin - inicio
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return "Hace unos segundos"

    dias = total_seconds // 86400
    horas = (total_seconds % 86400) // 3600
    minutos = (total_seconds % 3600) // 60

    partes = []
    if dias > 0:
        partes.append(f"{dias} {'día' if dias == 1 else 'días'}")
    if horas > 0:
        partes.append(f"{horas} {'hora' if horas == 1 else 'horas'}")
    if dias == 0 and minutos > 0:
        partes.append(f"{minutos} min")

    return ", ".join(partes) if partes else "Hace unos minutos"


def calcular_tiempos_peticion(peticion, ahora=None) -> dict:
    """
    Calcula para una petición:
    - tiempo_transcurrido: texto legible desde la creación (fecha)
    - tiempo_desde_asignacion: texto legible desde fecha_asignacion (si existe)
    - dias_transcurridos: entero de días desde la creación
    - dias_sin_avanzar: entero de días desde el último avance (asignación, cambio de estado o creación)
    - fecha_ultimo_avance: datetime del último movimiento registrado
    - puede_reasignar: True si tiene veterinario asignado, no está en estado terminal y lleva >= 5 días sin avanzar
    """
    fin = _a_naive_local(ahora or timezone.now())
    fecha_creacion = _a_naive_local(getattr(peticion, 'fecha', None))
    fecha_asig = _a_naive_local(getattr(peticion, 'fecha_asignacion', None))

    if fecha_creacion:
        dias_transcurridos = max(0, (fin - fecha_creacion).days)
        tiempo_transcurrido = formatear_duracion_humana(fecha_creacion, fin)
    else:
        dias_transcurridos = 0
        tiempo_transcurrido = "Sin registro"

    tiempo_desde_asignacion = formatear_duracion_humana(fecha_asig, fin) if fecha_asig else None

    # Determinar la fecha del último avance (asignación, cambio de estado en historial o creación)
    fechas_candidatas = [f for f in (fecha_creacion, fecha_asig) if f is not None]

    try:
        prefetched_hist = getattr(peticion, '_prefetched_objects_cache', {}).get('historial_cambios')
        if prefetched_hist is not None:
            for h in prefetched_hist:
                fh = _a_naive_local(getattr(h, 'fecha', None))
                if fh:
                    fechas_candidatas.append(fh)
        elif getattr(peticion, 'pk', None):
            from peticiones.models import HistorialPeticiones
            ultimo_hist = (
                HistorialPeticiones.objects
                .filter(id_peticion_id=peticion.pk)
                .order_by('-fecha', '-id_historial')
                .values_list('fecha', flat=True)
                .first()
            )
            fh = _a_naive_local(ultimo_hist)
            if fh:
                fechas_candidatas.append(fh)
    except Exception as exc:
        logger.debug(f"No se pudo consultar historial_cambios para tiempos de petición #{getattr(peticion, 'pk', None)}: {exc}")

    fecha_ultimo_avance = max(fechas_candidatas) if fechas_candidatas else fin
    dias_sin_avanzar = max(0, (fin - fecha_ultimo_avance).days)

    estado_obj = getattr(peticion, 'id_estado', None)
    estado_norm = normalizar_texto_juridico(getattr(estado_obj, 'nombre', '') if estado_obj else '')
    es_terminal = estado_norm in ESTADOS_TERMINALES_NORM
    tiene_veterinario = bool(getattr(peticion, 'asignado_a_id', None))

    puede_reasignar = bool(
        tiene_veterinario
        and not es_terminal
        and dias_sin_avanzar >= DIAS_PARA_REASIGNAR
    )

    return {
        'tiempo_transcurrido': tiempo_transcurrido,
        'tiempo_desde_asignacion': tiempo_desde_asignacion,
        'dias_transcurridos': dias_transcurridos,
        'dias_sin_avanzar': dias_sin_avanzar,
        'fecha_ultimo_avance': fecha_ultimo_avance,
        'puede_reasignar': puede_reasignar,
    }


def registrar_historial_peticion(
    peticion,
    tipo_cambio: str,
    estado_anterior=None,
    estado_nuevo=None,
    veterinario_anterior=None,
    veterinario_nuevo=None,
    usuario_accion=None,
    observacion: str = None,
):
    """
    Registra un evento en la tabla historial_peticiones de manera segura.
    """
    try:
        from peticiones.models import HistorialPeticiones
        user_obj = usuario_accion if (usuario_accion and getattr(usuario_accion, 'id_usuario', None)) else None
        return HistorialPeticiones.objects.create(
            id_peticion=peticion,
            tipo_cambio=tipo_cambio,
            id_estado_anterior=estado_anterior,
            id_estado_nuevo=estado_nuevo,
            id_veterinario_anterior=veterinario_anterior,
            id_veterinario_nuevo=veterinario_nuevo,
            id_usuario_accion=user_obj,
            observacion=observacion,
            fecha=timezone.now(),
        )
    except Exception as exc:
        logger.warning(f"Error al registrar historial de petición #{getattr(peticion, 'pk', None)}: {exc}")
        return None


def crear_notificaciones_nueva_peticion(peticion):
    """
    Crea notificaciones dentro de la aplicación web para los usuarios activos de Jurídico (rol 2)
    y Administrador (rol 1) cuando se registra una nueva petición.
    """
    try:
        from peticiones.models import NotificacionPeticion
        from users.models import Usuarios

        es_urgente = es_prioridad_urgente(getattr(peticion, 'prioridad', None))
        radicado = getattr(peticion, 'numero_radicado', None) or f"#{peticion.id_peticion}"
        tipo_nombre = getattr(getattr(peticion, 'id_tipo', None), 'nombre', 'Petición') or 'Petición'
        prioridad_txt = getattr(peticion, 'prioridad', None) or 'Normal'

        tipo_notif = 'PETICION_URGENTE' if es_urgente else 'NUEVA_PETICION'
        prefijo = "URGENTE - " if es_urgente else ""
        titulo = f"{prefijo}Nueva petición registrada ({radicado})"
        mensaje = (
            f"Se ha registrado una nueva petición de tipo '{tipo_nombre.strip()}' "
            f"con prioridad '{prioridad_txt}' y radicado {radicado}."
        )

        destinatarios = list(
            Usuarios.objects.filter(id_rol_id__in=[1, 2], activo=True)
        )
        notificaciones_creadas = []
        ahora = timezone.now()
        if destinatarios:
            for u in destinatarios:
                notificaciones_creadas.append(
                    NotificacionPeticion.objects.create(
                        id_peticion=peticion,
                        id_usuario_destino=u,
                        tipo=tipo_notif,
                        titulo=titulo,
                        mensaje=mensaje,
                        leida=False,
                        fecha_creacion=ahora,
                    )
                )
        else:
            notificaciones_creadas.append(
                NotificacionPeticion.objects.create(
                    id_peticion=peticion,
                    id_usuario_destino=None,
                    tipo=tipo_notif,
                    titulo=titulo,
                    mensaje=mensaje,
                    leida=False,
                    fecha_creacion=ahora,
                )
            )
        return notificaciones_creadas
    except Exception as exc:
        logger.warning(f"Error al crear notificaciones web para petición #{getattr(peticion, 'pk', None)}: {exc}")
        return []


def crear_notificacion_asignacion(peticion, veterinario, es_reasignacion: bool = False):
    """
    Crea una notificación en la aplicación web para el veterinario cuando se le asigna o reasigna una petición.
    """
    if not veterinario or not getattr(veterinario, 'id_usuario', None):
        return None
    try:
        from peticiones.models import NotificacionPeticion
        radicado = getattr(peticion, 'numero_radicado', None) or f"#{peticion.id_peticion}"
        accion = "reasignada" if es_reasignacion else "asignada"
        tipo_notif = 'REASIGNACION' if es_reasignacion else 'ASIGNACION'
        titulo = f"Petición {accion}: {radicado}"
        mensaje = f"Se te ha {accion[:-1]}o la petición {radicado} con prioridad '{getattr(peticion, 'prioridad', None) or 'Normal'}'."
        return NotificacionPeticion.objects.create(
            id_peticion=peticion,
            id_usuario_destino=veterinario,
            tipo=tipo_notif,
            titulo=titulo,
            mensaje=mensaje,
            leida=False,
            fecha_creacion=timezone.now(),
        )
    except Exception as exc:
        logger.warning(f"Error al crear notificación de asignación para petición #{getattr(peticion, 'pk', None)}: {exc}")
        return None


def enviar_correo_peticion_urgente_juridico(peticion) -> dict:
    """
    Envía un correo automático a todos los usuarios activos del rol Jurídico (id_rol = 2)
    cuando se registra una petición urgente.
    Reutiliza enviar_correo_transaccional_brevo y hace fallback a send_mail de Django.
    """
    if not es_prioridad_urgente(getattr(peticion, 'prioridad', None)):
        return {"enviado": False, "motivo": "La petición no es urgente."}

    try:
        from users.models import Usuarios
        from users.services.email_service import enviar_correo_transaccional_brevo
        from django.core.mail import send_mail
        from django.conf import settings

        juridicos = list(
            Usuarios.objects.filter(id_rol_id=2, activo=True)
            .exclude(email__isnull=True)
            .exclude(email='')
        )
        destinatarios = [
            (u.email.strip(), f"{u.nombre or ''} {u.apellido or ''}".strip() or "Equipo Jurídico")
            for u in juridicos if u.email and u.email.strip()
        ]
        if not destinatarios:
            correo_fallback = os.environ.get('JURIDICO_EMAIL', 'juridico@vetericano.com')
            destinatarios = [(correo_fallback, "Área Jurídica Vetericano")]

        radicado = getattr(peticion, 'numero_radicado', None) or f"PET-{peticion.id_peticion}"
        tipo_nombre = getattr(getattr(peticion, 'id_tipo', None), 'nombre', 'No especificado') or 'No especificado'
        direccion = getattr(getattr(peticion, 'id_ubicacion', None), 'direccion', None) or 'No especificada'
        prioridad_val = getattr(peticion, 'prioridad', 'Urgente')
        descripcion = getattr(peticion, 'descripcion', '') or 'Sin descripción adicional.'
        solicitante = "Anónimo"
        if getattr(peticion, 'responsable', None):
            solicitante = f"{peticion.responsable.nombre or ''} {peticion.responsable.apellido or ''}".strip() or peticion.responsable.email

        asunto = f"[URGENTE] Nueva Petición Registrada - {radicado}"
        contenido_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #222;">
            <h2 style="color: #c62828;">Alerta de Petición Urgente - Vetericano</h2>
            <p>Hola equipo de <strong>Jurídico</strong>,</p>
            <p>Se ha registrado una nueva petición marcada con prioridad <strong>{prioridad_val}</strong> que requiere atención prioritaria:</p>
            <ul>
                <li><strong>Número de radicado:</strong> {radicado}</li>
                <li><strong>Tipo de petición:</strong> {tipo_nombre.strip()}</li>
                <li><strong>Prioridad:</strong> {prioridad_val}</li>
                <li><strong>Dirección / Ubicación:</strong> {direccion}</li>
                <li><strong>Reportado por:</strong> {solicitante}</li>
                <li><strong>Descripción:</strong> {descripcion}</li>
            </ul>
            <p>Por favor ingresa al módulo de Jurídico en la aplicación web de Vetericano para revisarla y asignarla a un veterinario.</p>
        </body>
        </html>
        """
        mensaje_texto = (
            f"ALERTA DE PETICIÓN URGENTE - VETERICANO\n\n"
            f"Radicado: {radicado}\n"
            f"Tipo: {tipo_nombre.strip()}\n"
            f"Prioridad: {prioridad_val}\n"
            f"Ubicación: {direccion}\n"
            f"Reportado por: {solicitante}\n"
            f"Descripción: {descripcion}\n"
        )

        enviados = 0
        errores = []
        api_key_brevo = os.environ.get("BREVO_API_KEY")

        for email_dest, nombre_dest in destinatarios:
            exito_individual = False
            if api_key_brevo:
                res_brevo = enviar_correo_transaccional_brevo(
                    destinatario_email=email_dest,
                    destinatario_nombre=nombre_dest,
                    asunto=asunto,
                    contenido_html=contenido_html,
                )
                if res_brevo.get("exito"):
                    exito_individual = True
                    enviados += 1
                else:
                    errores.append(res_brevo.get("error"))

            if not exito_individual:
                try:
                    remitente = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or os.environ.get('DEFAULT_FROM_EMAIL', 'vetericano@gmail.com')
                    send_mail(
                        subject=asunto,
                        message=mensaje_texto,
                        from_email=remitente,
                        recipient_list=[email_dest],
                        html_message=contenido_html,
                        fail_silently=False,
                    )
                    enviados += 1
                except Exception as mail_exc:
                    errores.append(str(mail_exc))

        return {"enviado": enviados > 0, "destinatarios_enviados": enviados, "errores": errores}
    except Exception as exc:
        logger.error(f"Excepción al enviar correo de petición urgente #{getattr(peticion, 'pk', None)}: {exc}")
        return {"enviado": False, "error": str(exc)}
