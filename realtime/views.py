"""
API REST central de notificaciones (contrato v1, §6). Solo datos del usuario autenticado.
Une `notificaciones_peticiones` (clave pet-<id>) y `notificaciones_sistema` (clave sis-<id>).
Los endpoints heredados de peticiones/inventario siguen intactos.
"""
from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from realtime import notificar
from realtime.models import DispositivoFCM, NotificacionSistema
from realtime.serializacion import (
    PREFIJO_PETICION,
    PREFIJO_SISTEMA,
    item_peticion,
    item_sistema,
    parsear_clave,
)

LIMITE_POR_DEFECTO = 50
LIMITE_MAXIMO = 200
PLATAFORMAS = ('android', 'ios', 'web')


def _bool(valor):
    return str(valor).strip().lower() in ('1', 'true', 'yes', 'si', 'sí', 'on')


def _entero(valor, defecto, minimo, maximo):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return defecto
    return max(minimo, min(n, maximo))


def _qs_peticiones(usuario):
    from peticiones.models import NotificacionPeticion

    return NotificacionPeticion.objects.filter(id_usuario_destino=usuario)


def tablas_realtime_disponibles():
    """
    notificaciones_sistema y dispositivos_fcm solo existen tras aplicar la migración `realtime`.
    Hasta entonces (NOTIFICACIONES_SISTEMA_HABILITADAS=False) el REST central sirve solo las NP de
    peticiones (tabla existente) y nunca consulta las tablas nuevas: no hay 500 antes de migrar.
    """
    return bool(getattr(settings, 'NOTIFICACIONES_SISTEMA_HABILITADAS', False))


def _qs_sistema(usuario):
    if not tablas_realtime_disponibles():
        return NotificacionSistema.objects.none()  # no ejecuta SQL
    return NotificacionSistema.objects.filter(id_usuario_destino=usuario)


def _respuesta_dispositivos_no_disponible():
    return Response({'detail': 'Registro de dispositivos no disponible todavía.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE)


def _aplicar_filtros(modulo, solo_no_leidas, qs_pet, qs_sis):
    if solo_no_leidas:
        qs_pet = qs_pet.filter(leida=False)
        qs_sis = qs_sis.filter(leida=False)
    if modulo:
        qs_sis = qs_sis.filter(modulo=modulo)
        if modulo != 'peticiones':
            qs_pet = qs_pet.none()
    return qs_pet, qs_sis


class NotificacionesListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        usuario = request.user
        modulo = (request.query_params.get('modulo') or '').strip() or None
        solo = _bool(request.query_params.get('solo_no_leidas', 'false'))
        limit = _entero(request.query_params.get('limit'), LIMITE_POR_DEFECTO, 1, LIMITE_MAXIMO)
        offset = _entero(request.query_params.get('offset'), 0, 0, 100000)

        qs_pet, qs_sis = _aplicar_filtros(modulo, solo, _qs_peticiones(usuario), _qs_sistema(usuario))
        tope = offset + limit
        pet = list(qs_pet.select_related('id_peticion').order_by('-fecha_creacion', '-id_notificacion')[:tope])
        sis = list(qs_sis.order_by('-fecha_creacion', '-id_notificacion')[:tope])
        items = [item_peticion(n) for n in pet] + [item_sistema(n) for n in sis]
        items.sort(key=lambda i: (i['fecha_creacion'] or '', i['clave']), reverse=True)

        no_pet, no_sis = _aplicar_filtros(modulo, True, _qs_peticiones(usuario), _qs_sistema(usuario))
        return Response({
            'total': qs_pet.count() + qs_sis.count(),
            'no_leidas': no_pet.count() + no_sis.count(),
            'resultados': items[offset:tope],
        })


class NotificacionesContadorView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        usuario = request.user
        por_modulo = {}
        pet = _qs_peticiones(usuario).filter(leida=False).count()
        if pet:
            por_modulo['peticiones'] = pet
        if tablas_realtime_disponibles():
            for fila in _qs_sistema(usuario).filter(leida=False).values('modulo').annotate(n=Count('id_notificacion')):
                por_modulo[fila['modulo']] = por_modulo.get(fila['modulo'], 0) + fila['n']
        return Response({'no_leidas': sum(por_modulo.values()), 'por_modulo': por_modulo})


class NotificacionLeerView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, clave):
        prefijo, numero = parsear_clave(clave)
        if prefijo is None or (prefijo == PREFIJO_SISTEMA and not tablas_realtime_disponibles()):
            return Response({'detail': 'Notificación no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        usuario = request.user
        if prefijo == PREFIJO_PETICION:
            notif = _qs_peticiones(usuario).select_related('id_peticion').filter(id_notificacion=numero).first()
        else:
            notif = _qs_sistema(usuario).filter(id_notificacion=numero).first()
        if notif is None:
            return Response({'detail': 'Notificación no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            if not notif.leida:
                notif.leida = True
                notif.fecha_lectura = timezone.now()
                notif.save(update_fields=['leida', 'fecha_lectura'])
            notificar.emitir_lectura(usuario.id_usuario, clave=clave)
        item = item_peticion(notif) if prefijo == PREFIJO_PETICION else item_sistema(notif)
        return Response(item)

    post = patch


class NotificacionesLeerTodasView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        usuario = request.user
        modulo = (request.query_params.get('modulo') or request.data.get('modulo') or '').strip() or None
        ahora = timezone.now()
        qs_pet, qs_sis = _aplicar_filtros(modulo, True, _qs_peticiones(usuario), _qs_sistema(usuario))
        with transaction.atomic():
            n = qs_pet.update(leida=True, fecha_lectura=ahora)
            if tablas_realtime_disponibles():
                n += qs_sis.update(leida=True, fecha_lectura=ahora)
            notificar.emitir_lectura(usuario.id_usuario, modulo=modulo, todas=True)
        return Response({'actualizadas': n})

    post = patch


class DispositivoRegistrarView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not tablas_realtime_disponibles():
            return _respuesta_dispositivos_no_disponible()
        token = str(request.data.get('token') or '').strip()
        plataforma = str(request.data.get('plataforma') or 'android').strip().lower()
        if len(token) < 20 or len(token) > 512:
            return Response({'detail': 'Token inválido.'}, status=status.HTTP_400_BAD_REQUEST)
        if plataforma not in PLATAFORMAS:
            return Response({'detail': 'Plataforma inválida.'}, status=status.HTTP_400_BAD_REQUEST)
        ahora = timezone.now()
        with transaction.atomic():
            existente = DispositivoFCM.objects.select_for_update().filter(token=token).first()
            if existente:
                existente.id_usuario = request.user
                existente.plataforma = plataforma
                existente.activo = True
                existente.ultimo_error = None
                existente.actualizado_en = ahora
                existente.save(update_fields=['id_usuario', 'plataforma', 'activo', 'ultimo_error', 'actualizado_en'])
                return Response({'registrado': True}, status=status.HTTP_200_OK)
            DispositivoFCM.objects.create(
                id_usuario=request.user, token=token, plataforma=plataforma,
                activo=True, creado_en=ahora, actualizado_en=ahora,
            )
        return Response({'registrado': True}, status=status.HTTP_201_CREATED)


class DispositivoDesactivarView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not tablas_realtime_disponibles():
            return _respuesta_dispositivos_no_disponible()
        token = str(request.data.get('token') or '').strip()
        if not token:
            return Response({'detail': 'Token requerido.'}, status=status.HTTP_400_BAD_REQUEST)
        n = DispositivoFCM.objects.filter(token=token, id_usuario=request.user, activo=True).update(
            activo=False, actualizado_en=timezone.now(), ultimo_error='LOGOUT',
        )
        return Response({'desactivado': n > 0})
