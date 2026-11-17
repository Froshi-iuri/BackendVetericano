"""
Pruebas del servicio WebSocket (sin BD ni Redis): WebsocketCommunicator + InMemoryChannelLayer.
La lectura de `Usuarios` se sustituye por un doble asíncrono.
"""
import asyncio
import json
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import HttpCommunicator, WebsocketCommunicator
from django.test import SimpleTestCase, override_settings
from django.urls import path
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from realtime import eventos
from realtime.consumers import NotificacionesConsumer
from realtime.origen import ValidadorOrigenExplicito
from vetericano.asgi_ws import http_minimo

ORIGEN_OK = 'http://localhost:4200'
APP = ValidadorOrigenExplicito(URLRouter([path('ws/notificaciones/', NotificacionesConsumer.as_asgi())]))
CAPA = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


def token_access(uid, segundos=3600):
    t = AccessToken()
    t['user_id'] = uid
    t.set_exp(lifetime=timedelta(seconds=segundos))
    return str(t)


def usuarios(*filas):
    """Doble de cargar_usuario_activo: {id: rol} (None = inactivo/no existe)."""
    tabla = dict(filas)

    async def cargar(uid):
        rol = tabla.get(uid)
        return None if rol is None else {'id_usuario': uid, 'id_rol': rol}

    return cargar


class _Capturador(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.textos = []

    def emit(self, record):
        self.textos.append(record.getMessage())


@override_settings(
    CHANNEL_LAYERS=CAPA,
    WS_ALLOWED_ORIGINS=[ORIGEN_OK],
    WS_AUTH_TIMEOUT_SEGUNDOS=0.3,
    WS_MAX_SIN_AUTENTICAR=200,
    WS_RECHEQUEO_SEGUNDOS=300,
)
class NotificacionesConsumerTests(SimpleTestCase):
    databases = set()

    def setUp(self):
        NotificacionesConsumer.sin_autenticar = 0
        self.patcher = patch('realtime.consumers.cargar_usuario_activo', side_effect=usuarios((12, 3), (13, 3), (20, 2), (30, 1)))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    async def _conectar(self, origen=ORIGEN_OK):
        headers = [(b'origin', origen.encode())] if origen else []
        comm = WebsocketCommunicator(APP, '/ws/notificaciones/', headers=headers)
        conectado, codigo = await comm.connect()
        return comm, conectado, codigo

    async def _autenticado(self, uid):
        comm, conectado, _ = await self._conectar()
        self.assertTrue(conectado)
        await comm.send_to(text_data=json.dumps({'type': 'auth', 'token': token_access(uid)}))
        ok = json.loads(await comm.receive_from(timeout=2))
        self.assertEqual(ok['type'], 'auth_ok')
        return comm, ok

    async def _cierre(self, comm, timeout=2):
        salida = await comm.receive_output(timeout=timeout)
        while salida['type'] != 'websocket.close':
            salida = await comm.receive_output(timeout=timeout)
        return salida.get('code')

    # 1. Origin
    async def test_origen_no_permitido_rechazado(self):
        _, conectado, _ = await self._conectar('https://evil.example.com')
        self.assertFalse(conectado)

    async def test_sin_origin_rechazado(self):
        _, conectado, _ = await self._conectar(origen=None)
        self.assertFalse(conectado)

    async def test_origen_con_puerto_distinto_rechazado(self):
        _, conectado, _ = await self._conectar('http://localhost:4300')
        self.assertFalse(conectado)

    # 2. Plazo de autenticación y mensajes previos
    async def test_sin_auth_en_plazo_cierra_4001(self):
        comm, conectado, _ = await self._conectar()
        self.assertTrue(conectado)
        self.assertEqual(await self._cierre(comm), 4001)

    async def test_mensaje_distinto_de_auth_antes_de_autenticar_cierra_4001(self):
        comm, _, _ = await self._conectar()
        await comm.send_to(text_data=json.dumps({'type': 'ping'}))
        self.assertEqual(await self._cierre(comm), 4001)

    async def test_json_invalido_antes_de_autenticar_cierra_4001(self):
        comm, _, _ = await self._conectar()
        await comm.send_to(text_data='no-es-json')
        self.assertEqual(await self._cierre(comm), 4001)

    # 3. Tokens inválidos
    async def test_token_invalido_cierra_4001(self):
        comm, _, _ = await self._conectar()
        await comm.send_to(text_data=json.dumps({'type': 'auth', 'token': 'abc.def.ghi'}))
        self.assertEqual(await self._cierre(comm), 4001)

    async def test_token_refresh_cierra_4001(self):
        refresh = RefreshToken()
        refresh['user_id'] = 12
        comm, _, _ = await self._conectar()
        await comm.send_to(text_data=json.dumps({'type': 'auth', 'token': str(refresh)}))
        self.assertEqual(await self._cierre(comm), 4001)

    async def test_token_expirado_cierra_4001(self):
        comm, _, _ = await self._conectar()
        await comm.send_to(text_data=json.dumps({'type': 'auth', 'token': token_access(12, segundos=-30)}))
        self.assertEqual(await self._cierre(comm), 4001)

    async def test_usuario_inactivo_cierra_4001(self):
        comm, _, _ = await self._conectar()
        await comm.send_to(text_data=json.dumps({'type': 'auth', 'token': token_access(99)}))
        self.assertEqual(await self._cierre(comm), 4001)

    # 4 y 5. auth_ok, grupos y aislamiento
    async def test_auth_ok_rol_desde_bd_y_eventos_solo_despues(self):
        capa = get_channel_layer()
        comm, _, _ = await self._conectar()
        # Evento enviado ANTES de autenticarse: no hay grupo, no debe llegar nunca.
        await capa.group_send('user_12', {'type': 'evento.enviar', 'evento': {'type': 'event', 'n': 0}})
        await comm.send_to(text_data=json.dumps({'type': 'auth', 'token': token_access(12)}))
        ok = json.loads(await comm.receive_from(timeout=2))
        self.assertEqual((ok['type'], ok['user_id'], ok['id_rol']), ('auth_ok', 12, 3))
        self.assertTrue(await comm.receive_nothing(timeout=0.2))
        await capa.group_send('user_12', {'type': 'evento.enviar', 'evento': {'type': 'event', 'n': 1}})
        self.assertEqual(json.loads(await comm.receive_from(timeout=2))['n'], 1)
        await comm.disconnect()

    async def test_aislamiento_entre_usuarios_y_roles(self):
        capa = get_channel_layer()
        vet_a, _ = await self._autenticado(12)
        vet_b, _ = await self._autenticado(13)
        juridico, _ = await self._autenticado(20)
        admin, _ = await self._autenticado(30)

        sobre = eventos.construir_sobre('peticion.asignada', eventos.NP, {'clave': 'pet-1'})
        await capa.group_send('user_12', {'type': 'evento.enviar', 'evento': sobre})
        self.assertEqual(json.loads(await vet_a.receive_from(timeout=2))['data']['clave'], 'pet-1')
        for otro in (vet_b, juridico, admin):
            self.assertTrue(await otro.receive_nothing(timeout=0.2))

        # Grupo de rol: solo los del rol 2 lo reciben
        await capa.group_send('rol_2', {'type': 'evento.enviar', 'evento': {'type': 'event', 'g': 'rol_2'}})
        self.assertEqual(json.loads(await juridico.receive_from(timeout=2))['g'], 'rol_2')
        for otro in (vet_a, vet_b, admin):
            self.assertTrue(await otro.receive_nothing(timeout=0.2))
        for c in (vet_a, vet_b, juridico, admin):
            await c.disconnect()

    async def test_cliente_no_puede_emitir_ni_elegir_grupos(self):
        capa = get_channel_layer()
        vet_a, _ = await self._autenticado(12)
        vet_b, _ = await self._autenticado(13)
        await vet_a.send_to(text_data=json.dumps({'type': 'subscribe', 'group': 'user_13'}))
        await vet_a.send_to(text_data=json.dumps({'type': 'event', 'target': 'user_13', 'data': {}}))
        self.assertTrue(await vet_b.receive_nothing(timeout=0.2))
        self.assertTrue(await vet_a.receive_nothing(timeout=0.2))
        await capa.group_send('user_13', {'type': 'evento.enviar', 'evento': {'type': 'event', 'x': 1}})
        self.assertTrue(await vet_a.receive_nothing(timeout=0.2))
        await vet_a.disconnect()
        await vet_b.disconnect()

    # 6. ping/pong
    async def test_ping_pong_renueva_grupos(self):
        comm, _ = await self._autenticado(12)
        await comm.send_to(text_data=json.dumps({'type': 'ping'}))
        self.assertEqual(json.loads(await comm.receive_from(timeout=2)), {'type': 'pong'})
        await comm.disconnect()

    # 7. Expiración y revocación
    async def test_expiracion_del_token_cierra_4002(self):
        comm, _, _ = await self._conectar()
        await comm.send_to(text_data=json.dumps({'type': 'auth', 'token': token_access(12, segundos=1)}))
        self.assertEqual(json.loads(await comm.receive_from(timeout=2))['type'], 'auth_ok')
        self.assertEqual(await self._cierre(comm, timeout=4), 4002)

    async def test_revocacion_por_grupo_envia_evento_y_cierra_4003(self):
        capa = get_channel_layer()
        comm, _ = await self._autenticado(12)
        await capa.group_send('user_12', {'type': 'sesion.revocar', 'motivo': 'inactivado'})
        evento = json.loads(await comm.receive_from(timeout=2))
        self.assertEqual(evento['event_type'], 'sesion.revocada')
        self.assertEqual(evento['data']['motivo'], 'inactivado')
        self.assertEqual(await self._cierre(comm), 4003)

    @override_settings(WS_RECHEQUEO_SEGUNDOS=0.1)
    async def test_usuario_desactivado_durante_la_sesion_cierra_4003(self):
        comm, _ = await self._autenticado(12)
        with patch('realtime.consumers.cargar_usuario_activo', side_effect=usuarios()):
            evento = json.loads(await comm.receive_from(timeout=2))
            self.assertEqual(evento['event_type'], 'sesion.revocada')
            self.assertEqual(await self._cierre(comm), 4003)

    @override_settings(WS_RECHEQUEO_SEGUNDOS=0.1)
    async def test_cambio_de_rol_durante_la_sesion_cierra_4003(self):
        comm, _ = await self._autenticado(12)
        with patch('realtime.consumers.cargar_usuario_activo', side_effect=usuarios((12, 2))):
            evento = json.loads(await comm.receive_from(timeout=2))
            self.assertEqual(evento['data']['motivo'], 'rol_cambiado')
            self.assertEqual(await self._cierre(comm), 4003)

    @override_settings(WS_RECHEQUEO_SEGUNDOS=0.05)
    async def test_errores_de_bd_en_vigilancia_no_la_detienen_y_cierran_1011(self):
        comm, _ = await self._autenticado(12)
        with patch('realtime.consumers.cargar_usuario_activo', side_effect=RuntimeError('pooler caído')) as cargar:
            self.assertEqual(await self._cierre(comm, timeout=3), 1011)
            self.assertGreaterEqual(cargar.call_count, 3)

    @override_settings(WS_RECHEQUEO_SEGUNDOS=0.05)
    async def test_error_transitorio_de_bd_y_luego_cambio_de_rol_cierra_4003(self):
        comm, _ = await self._autenticado(12)
        respuestas = [RuntimeError('pooler caído'), {'id_usuario': 12, 'id_rol': 1}]

        async def cargar(uid):
            r = respuestas.pop(0) if respuestas else {'id_usuario': 12, 'id_rol': 1}
            if isinstance(r, Exception):
                raise r
            return r

        with patch('realtime.consumers.cargar_usuario_activo', side_effect=cargar):
            evento = json.loads(await comm.receive_from(timeout=3))
            self.assertEqual(evento['data']['motivo'], 'rol_cambiado')
            self.assertEqual(await self._cierre(comm), 4003)

    # 8. Límites
    async def test_mensaje_mayor_a_16kb_rechazado(self):
        comm, _, _ = await self._conectar()
        await comm.send_to(text_data=json.dumps({'type': 'auth', 'token': 'x' * 17000}))
        self.assertEqual(await self._cierre(comm), 4001)

    @override_settings(WS_MAX_SIN_AUTENTICAR=1)
    async def test_cupo_de_conexiones_sin_autenticar_cierra_1013(self):
        primera, conectado, _ = await self._conectar()
        self.assertTrue(conectado)
        segunda, conectado2, _ = await self._conectar()
        self.assertTrue(conectado2)
        self.assertEqual(await self._cierre(segunda), 1013)
        await primera.disconnect()

    @override_settings(CHANNEL_LAYERS={})
    async def test_sin_channel_layer_cierra_1011(self):
        comm, conectado, _ = await self._conectar()
        self.assertTrue(conectado)
        self.assertEqual(await self._cierre(comm), 1011)

    # 10. El token nunca aparece en los logs
    async def test_token_nunca_en_logs(self):
        captura = _Capturador()
        raiz = logging.getLogger()
        raiz.addHandler(captura)
        nivel = raiz.level
        raiz.setLevel(logging.DEBUG)
        try:
            tk = token_access(12)
            comm, _, _ = await self._conectar()
            await comm.send_to(text_data=json.dumps({'type': 'auth', 'token': tk}))
            await comm.receive_from(timeout=2)
            await comm.disconnect()
            comm2, _, _ = await self._conectar()
            await comm2.send_to(text_data=json.dumps({'type': 'auth', 'token': tk + 'x'}))
            await self._cierre(comm2)
        finally:
            raiz.removeHandler(captura)
            raiz.setLevel(nivel)
        self.assertTrue(captura.textos, 'se esperaban logs del consumer')
        self.assertFalse([t for t in captura.textos if tk[:40] in t])


class HttpMinimoTests(SimpleTestCase):
    databases = set()

    async def test_health_200(self):
        resp = await HttpCommunicator(http_minimo, 'GET', '/health/').get_response()
        self.assertEqual(resp['status'], 200)

    async def test_api_rest_no_expuesta_404(self):
        resp = await HttpCommunicator(http_minimo, 'GET', '/api/peticiones/listar/').get_response()
        self.assertEqual(resp['status'], 404)
