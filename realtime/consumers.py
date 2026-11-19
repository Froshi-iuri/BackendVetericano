"""
NotificacionesConsumer — /ws/notificaciones/ (contrato v1 §2, plan §10.2).

Secuencia: Origin ✓ (middleware) → accept → espera `auth` (5 s) → JWT ✓ + Usuarios.activo ✓ (BD)
→ group_add(user_<id>, rol_<id_rol>) → `auth_ok`. Antes de autenticarse no hay grupos ni eventos.
El cliente solo puede enviar `auth` y `ping`; nunca elige grupos ni emite eventos.
"""
import asyncio
import json
import logging
import time

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from realtime import eventos
from realtime.auth import cargar_usuario_activo, validar_access_token

logger = logging.getLogger('realtime.ws')

CIERRE_NORMAL = 1000
CIERRE_SATURADO = 1013
CIERRE_ERROR_INTERNO = 1011
CIERRE_AUTH = 4001
CIERRE_EXPIRADO = 4002
CIERRE_REVOCADO = 4003
MAX_FALLOS_VIGILANCIA = 3


class NotificacionesConsumer(AsyncWebsocketConsumer):
    sin_autenticar = 0  # conexiones abiertas aún sin autenticar en este proceso

    async def connect(self):
        self.autenticado = False
        self.cerrando = False
        self.grupos = []
        self.user_id = None
        self.id_rol = None
        self.token_exp = None
        self._contado = False
        self._tarea_auth = None
        self._tarea_vigilancia = None

        await self.accept()
        if NotificacionesConsumer.sin_autenticar >= settings.WS_MAX_SIN_AUTENTICAR:
            await self._cerrar(CIERRE_SATURADO)
            return
        if self.channel_layer is None:
            logger.warning('WS sin channel layer (falta CHANNEL_REDIS_URL)')
            await self._cerrar(CIERRE_ERROR_INTERNO)
            return
        NotificacionesConsumer.sin_autenticar += 1
        self._contado = True
        self._tarea_auth = asyncio.ensure_future(self._expirar_autenticacion())

    async def _expirar_autenticacion(self):
        try:
            await asyncio.sleep(settings.WS_AUTH_TIMEOUT_SEGUNDOS)
            if not self.autenticado:
                await self._cerrar(CIERRE_AUTH)
        except asyncio.CancelledError:
            pass

    def _descontar(self):
        if self._contado:
            NotificacionesConsumer.sin_autenticar = max(0, NotificacionesConsumer.sin_autenticar - 1)
            self._contado = False

    async def _cerrar(self, codigo):
        if self.cerrando:
            return
        self.cerrando = True
        try:
            await self.close(code=codigo)
        except Exception:  # pragma: no cover - socket ya cerrado
            pass

    async def receive(self, text_data=None, bytes_data=None):
        if self.cerrando:
            return
        if bytes_data is not None or not text_data or len(text_data.encode('utf-8')) > settings.WS_MAX_MENSAJE_BYTES:
            await self._cerrar(CIERRE_AUTH if not self.autenticado else 1009)
            return
        try:
            mensaje = json.loads(text_data)
            tipo = mensaje.get('type') if isinstance(mensaje, dict) else None
        except (ValueError, AttributeError):
            tipo = None

        if not self.autenticado:
            if tipo != 'auth':
                await self._cerrar(CIERRE_AUTH)
                return
            await self._autenticar(mensaje.get('token'))
            return

        if tipo == 'ping':
            # Renueva la membresía (idempotente) por si Redis se reinició o expiró el grupo.
            for grupo in self.grupos:
                await self.channel_layer.group_add(grupo, self.channel_name)
            await self.send(text_data=json.dumps({'type': 'pong'}))
        # Cualquier otro tipo tras autenticarse se ignora: el cliente no emite eventos.

    async def _autenticar(self, token):
        datos = validar_access_token(token)
        if not datos or datos['exp'] <= int(time.time()):
            await self._cerrar(CIERRE_AUTH)
            return
        try:
            usuario = await cargar_usuario_activo(datos['user_id'])
        except Exception as exc:
            logger.warning('WS: error leyendo usuario: %s', type(exc).__name__)
            await self._cerrar(CIERRE_ERROR_INTERNO)
            return
        if not usuario:
            await self._cerrar(CIERRE_AUTH)
            return
        if self.cerrando:
            return

        self.user_id = usuario['id_usuario']
        self.id_rol = usuario['id_rol']
        self.token_exp = datos['exp']
        self.grupos = [eventos.grupo_usuario(self.user_id)]
        if self.id_rol:
            self.grupos.append(eventos.grupo_rol(self.id_rol))
        for grupo in self.grupos:
            await self.channel_layer.group_add(grupo, self.channel_name)
        if self.cerrando:  # cerró mientras se unían los grupos: se retiran aquí por si disconnect() ya pasó
            for grupo in self.grupos:
                try:
                    await self.channel_layer.group_discard(grupo, self.channel_name)
                except Exception:  # pragma: no cover - Redis caído
                    pass
            return

        self.autenticado = True
        self._descontar()
        if self._tarea_auth:
            self._tarea_auth.cancel()
        await self.send(text_data=json.dumps({
            'type': 'auth_ok', 'user_id': self.user_id, 'id_rol': self.id_rol, 'token_exp': self.token_exp,
        }))
        logger.info('WS autenticado user=%s rol=%s', self.user_id, self.id_rol)
        self._tarea_vigilancia = asyncio.ensure_future(self._vigilar())

    async def _vigilar(self):
        """
        Cierra con 4002 al expirar el token y con 4003 si el usuario se inactiva o cambia de rol.
        Un error de BD NO detiene la vigilancia (la expiración se sigue aplicando); tras
        MAX_FALLOS_VIGILANCIA errores seguidos se cierra con 1011 y el cliente re-autentica.
        """
        fallos = 0
        try:
            while not self.cerrando:
                restante = self.token_exp - time.time()
                if restante <= 0:
                    await self._cerrar(CIERRE_EXPIRADO)
                    return
                await asyncio.sleep(min(settings.WS_RECHEQUEO_SEGUNDOS, max(restante, 0.05)))
                if self.cerrando:
                    return
                if time.time() >= self.token_exp:
                    await self._cerrar(CIERRE_EXPIRADO)
                    return
                try:
                    usuario = await cargar_usuario_activo(self.user_id)
                except Exception as exc:
                    fallos += 1
                    logger.warning('WS: rechequeo de usuario falló (%d/%d): %s',
                                   fallos, MAX_FALLOS_VIGILANCIA, type(exc).__name__)
                    if fallos >= MAX_FALLOS_VIGILANCIA:
                        await self._cerrar(CIERRE_ERROR_INTERNO)
                        return
                    continue
                fallos = 0
                if not usuario or usuario['id_rol'] != self.id_rol:
                    await self._enviar_revocacion('inactivado' if not usuario else 'rol_cambiado')
                    return
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # cualquier fallo inesperado cierra: nunca queda un socket sin vigilancia
            logger.warning('WS: error en vigilancia: %s', type(exc).__name__)
            await self._cerrar(CIERRE_ERROR_INTERNO)

    async def _enviar_revocacion(self, motivo):
        sobre = eventos.construir_sobre('sesion.revocada', eventos.AO, {
            'titulo': 'Sesión finalizada',
            'mensaje': 'Tu sesión fue cerrada por un cambio en tu cuenta.',
            'nivel': 'danger',
            'referencia': {},
            'motivo': motivo,
        })
        try:
            await self.send(text_data=json.dumps(sobre))
        finally:
            await self._cerrar(CIERRE_REVOCADO)

    # ── Handlers del channel layer ──────────────────────────────────────────────────────
    async def evento_enviar(self, mensaje):
        if self.autenticado and not self.cerrando:
            await self.send(text_data=json.dumps(mensaje['evento'], default=str))

    async def sesion_revocar(self, mensaje):
        if self.autenticado and not self.cerrando:
            await self._enviar_revocacion(mensaje.get('motivo') or 'revocada')

    async def disconnect(self, code):
        self.cerrando = True
        self._descontar()
        for tarea in (self._tarea_auth, self._tarea_vigilancia):
            if tarea:
                tarea.cancel()
        if self.channel_layer is not None:
            for grupo in self.grupos:
                try:
                    await self.channel_layer.group_discard(grupo, self.channel_name)
                except Exception:  # pragma: no cover - Redis caído
                    pass
