"""
Pruebas unitarias sin BD: catálogo, publicación best-effort, notificar, FCM, serialización,
origen y señales de usuarios (USR-01 / SEG-01).
"""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.test import SimpleTestCase, override_settings

from realtime import eventos, fcm, notificar, publicar, senales
from realtime.origen import normalizar_origen
from realtime.serializacion import item_peticion, item_sistema, parsear_clave

CAPA = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


class CatalogoTests(SimpleTestCase):
    databases = set()

    def test_catalogo_cubre_todos_los_ids_del_contrato(self):
        esperados = {
            'peticion.creada', 'peticion.urgente', 'peticion.asignada', 'peticion.reasignada',
            'peticion.desasignada', 'peticion.asignacion_actualizada', 'peticion.estado_actualizado',
            'peticion.posible_duplicado', 'peticion.duplicado_resuelto', 'peticion.seguimiento_creado',
            'inventario.movimiento_bodega', 'inventario.alerta_bodega', 'kit.stock_actualizado',
            'kit.alerta_stock_vencimiento', 'kit.solicitud_creada', 'kit.solicitud_resuelta',
            'kit.solicitud_cancelada', 'kit.conflicto_sincronizacion', 'catalogo.medicamentos_actualizado',
            'clinica.atencion_registrada', 'catalogo.clinico_actualizado', 'adopcion.catalogo_actualizado',
            'voluntariado.postulacion_creada', 'voluntariado.postulacion_resuelta',
            'voluntariado.evento_actualizado', 'admin.usuario_gestionado', 'admin.sede_actualizada',
            'sesion.revocada', 'notificacion.leida', 'notificacion.todas_leidas',
        }
        self.assertEqual(set(eventos.CATALOGO), esperados)

    def test_sobre_v1(self):
        sobre = eventos.construir_sobre('kit.solicitud_creada', eventos.NP, {'clave': 'sis-1'})
        self.assertEqual(sobre['type'], 'event')
        self.assertEqual(sobre['schema_version'], '1')
        self.assertEqual(sobre['modulo'], 'inventario')
        self.assertEqual(sobre['categoria'], 'NP')
        self.assertEqual(len(sobre['event_id']), 36)
        self.assertNotEqual(sobre['event_id'], eventos.construir_sobre('kit.solicitud_creada', 'NP', {})['event_id'])

    def test_evento_desconocido(self):
        with self.assertRaises(eventos.EventoDesconocido):
            eventos.construir_sobre('no.existe', eventos.AS, {})


@override_settings(CHANNEL_LAYERS=CAPA, WS_PUBLICAR_EVENTOS=True)
class PublicarTests(SimpleTestCase):
    databases = set()

    def setUp(self):
        publicar.reiniciar_corte()

    @override_settings(WS_PUBLICAR_EVENTOS=False)
    def test_flag_apagado_no_toca_la_capa(self):
        with patch('channels.layers.get_channel_layer') as gcl:
            self.assertFalse(publicar.publicar_a_usuario(12, 'peticion.asignada', {}))
            gcl.assert_not_called()

    @override_settings(CHANNEL_LAYERS={})
    def test_sin_channel_redis_url_no_publica(self):
        self.assertFalse(publicar.publicar_a_usuario(12, 'peticion.asignada', {}))

    async def test_publica_en_el_grupo_del_usuario(self):
        capa = get_channel_layer()
        canal = await capa.new_channel()
        await capa.group_add('user_12', canal)
        ok = await sync_to_async(publicar.publicar_a_usuario)(12, 'peticion.asignada', {'clave': 'pet-9'})
        self.assertTrue(ok)
        msg = await asyncio.wait_for(capa.receive(canal), 2)
        self.assertEqual(msg['type'], 'evento.enviar')
        self.assertEqual(msg['evento']['data']['clave'], 'pet-9')
        self.assertEqual(msg['evento']['categoria'], 'NP')

    def test_fallo_de_redis_no_lanza_y_activa_corte(self):
        capa = MagicMock()

        async def roto(*a, **k):
            raise ConnectionError('redis caído')

        capa.group_send.side_effect = roto
        with patch('realtime.publicar._capa', return_value=capa):
            self.assertFalse(publicar.publicar_a_usuario(12, 'peticion.asignada', {}))
            self.assertEqual(capa.group_send.call_count, 1)
            # Dentro del corte de 30 s ni siquiera se intenta
            self.assertFalse(publicar.publicar_a_usuario(12, 'peticion.asignada', {}))
            self.assertEqual(capa.group_send.call_count, 1)

    def test_redis_colgado_respeta_el_timeout(self):
        capa = MagicMock()

        async def colgado(*a, **k):
            await asyncio.sleep(10)

        capa.group_send.side_effect = colgado
        with patch('realtime.publicar._capa', return_value=capa), \
                patch('realtime.publicar.TIMEOUT_PUBLICACION_SEGUNDOS', 0.3):
            t0 = time.monotonic()
            self.assertFalse(publicar.publicar_a_usuario(12, 'peticion.asignada', {}))
            self.assertLess(time.monotonic() - t0, 2.0)

    def test_al_confirmar_coalesce_en_la_misma_transaccion(self):
        conexion = SimpleNamespace(in_atomic_block=True, run_on_commit=[])

        def on_commit(func, robust=False):
            conexion.run_on_commit.append((set(), func, robust))

        with patch('realtime.publicar.transaction.get_connection', return_value=conexion), \
                patch('realtime.publicar.transaction.on_commit', side_effect=on_commit):
            for _ in range(50):
                publicar.al_confirmar(lambda: None, clave_unica=('AS', 'x'))
            publicar.al_confirmar(lambda: None, clave_unica=('AS', 'y'))
            publicar.al_confirmar(lambda: None)
            publicar.al_confirmar(lambda: None)
        self.assertEqual(len(conexion.run_on_commit), 4)
        self.assertTrue(all(robust for _, _, robust in conexion.run_on_commit))

    def test_al_confirmar_traga_errores(self):
        conexion = SimpleNamespace(in_atomic_block=False, run_on_commit=[])

        def on_commit(func, robust=False):
            func()  # autocommit: se ejecuta en el acto

        def explota():
            raise RuntimeError('x')

        with patch('realtime.publicar.transaction.get_connection', return_value=conexion), \
                patch('realtime.publicar.transaction.on_commit', side_effect=on_commit):
            publicar.al_confirmar(explota)  # no lanza


class NotificarTests(SimpleTestCase):
    databases = set()

    @override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=False)
    def test_persistencia_apagada_no_crea_nada(self):
        with patch('realtime.models.NotificacionSistema.objects') as objs:
            self.assertEqual(notificar.notificar_usuarios('kit.solicitud_creada', [1, 2], 't', 'm', raise_on_error=True), [])
            objs.create.assert_not_called()

    @override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=True)
    def test_evento_desconocido_estricto_lanza(self):
        with self.assertRaises(eventos.EventoDesconocido):
            notificar.notificar_usuarios('no.existe', [1], 't', 'm', raise_on_error=True)
        self.assertEqual(notificar.notificar_usuarios('no.existe', [1], 't', 'm'), [])

    def test_ids_usuarios_deduplica_y_excluye(self):
        u = SimpleNamespace(id_usuario=5)
        self.assertEqual(notificar._ids_usuarios([u, 5, 6, None, 7], excluir=[7]), [5, 6])

    def test_entregar_peticion_ignora_sin_destinatario(self):
        n_ok = SimpleNamespace(id_notificacion=1, id_usuario_destino_id=12, tipo='ASIGNACION', titulo='t', mensaje='m',
                               leida=False, fecha_creacion=None, fecha_lectura=None, id_peticion_id=3,
                               id_peticion=SimpleNamespace(prioridad='Alta', numero_radicado='RAD-3'))
        n_none = SimpleNamespace(id_usuario_destino_id=None)
        with patch('realtime.notificar.publicar.al_confirmar') as al:
            self.assertEqual(notificar.entregar_notificaciones_peticion([n_ok, n_none, None]), 1)
            al.assert_called_once()

    def test_entrega_publica_np_a_cada_usuario_y_fcm(self):
        pares = [(12, {'event_type': 'peticion.asignada', 'clave': 'pet-1'}),
                 (13, {'event_type': 'peticion.asignada', 'clave': 'pet-2'})]
        with patch('realtime.notificar.publicar.publicar_evento') as pub, \
                patch('realtime.notificar.fcm.enviar_items') as env:
            notificar._entregar(pares)
        self.assertEqual([c.kwargs['usuarios'] for c in pub.call_args_list], [[12], [13]])
        self.assertTrue(all(c.args[1] == 'NP' for c in pub.call_args_list))
        env.assert_called_once_with(pares)

    def test_as_y_ao_se_programan_tras_commit(self):
        with patch('realtime.notificar.publicar.al_confirmar') as al:
            notificar.actualizacion_silenciosa('catalogo.medicamentos_actualizado', roles=[3, 1, 2])
            notificar.alerta_operativa('inventario.alerta_bodega', 'Stock bajo', 'm', roles=[1, 2], nivel='raro')
        self.assertEqual(al.call_count, 2)
        self.assertEqual(al.call_args_list[0].kwargs['clave_unica'],
                         ('AS', 'catalogo.medicamentos_actualizado', (), (1, 2, 3)))


class SerializacionTests(SimpleTestCase):
    databases = set()

    def test_item_peticion_forma_del_contrato(self):
        from datetime import datetime
        n = SimpleNamespace(id_notificacion=893, tipo='REASIGNACION', titulo='T', mensaje='M', leida=False,
                            fecha_creacion=datetime(2026, 11, 17, 10, 0), fecha_lectura=None, id_peticion_id=215,
                            id_peticion=SimpleNamespace(prioridad='Urgente', numero_radicado='RAD-2026-0215'))
        item = item_peticion(n)
        self.assertEqual(set(item), {'clave', 'origen', 'id_notificacion', 'event_type', 'modulo', 'tipo', 'titulo',
                                     'mensaje', 'prioridad', 'leida', 'fecha_creacion', 'fecha_lectura', 'referencia'})
        self.assertEqual(item['clave'], 'pet-893')
        self.assertEqual(item['event_type'], 'peticion.reasignada')
        self.assertEqual(item['referencia'], {'id_peticion': 215, 'numero_radicado': 'RAD-2026-0215'})
        self.assertEqual(item['fecha_creacion'], '2026-11-17T10:00:00')

    def test_item_sistema(self):
        n = SimpleNamespace(id_notificacion=4, event_type='kit.solicitud_creada', modulo='inventario', tipo='SOLICITUD',
                            titulo='T', mensaje='M', prioridad=None, leida=True, fecha_creacion=None,
                            fecha_lectura=None, referencia={'id_solicitud': 9})
        self.assertEqual(item_sistema(n)['clave'], 'sis-4')

    def test_parsear_clave(self):
        self.assertEqual(parsear_clave('pet-12'), ('pet', 12))
        self.assertEqual(parsear_clave('sis-3'), ('sis', 3))
        for mala in ('x-1', 'pet-', 'pet-1a', '12', None, 'pet--1'):
            self.assertEqual(parsear_clave(mala), (None, None))


class OrigenTests(SimpleTestCase):
    databases = set()

    def test_normalizar(self):
        self.assertEqual(normalizar_origen('https://Admin-Vetericano.vercel.app/'), 'https://admin-vetericano.vercel.app')
        self.assertEqual(normalizar_origen('https://a.app:443'), 'https://a.app')
        self.assertEqual(normalizar_origen('http://localhost:4200'), 'http://localhost:4200')
        self.assertIsNone(normalizar_origen('null'))
        self.assertIsNone(normalizar_origen('ftp://x'))


class FcmTests(SimpleTestCase):
    databases = set()

    def test_mensaje_v1_con_data_en_strings(self):
        m = fcm.construir_mensaje('tok', {'titulo': 'T', 'mensaje': 'M', 'event_type': 'kit.solicitud_resuelta',
                                          'clave': 'sis-12', 'modulo': 'inventario', 'referencia': {'id_solicitud': 5}})['message']
        self.assertEqual(m['token'], 'tok')
        self.assertEqual(m['android']['notification']['channel_id'], 'vetericano_notificaciones')
        self.assertTrue(all(isinstance(v, str) for v in m['data'].values()))
        self.assertEqual(json.loads(m['data']['referencia']), {'id_solicitud': 5})
        self.assertNotIn('topic', m)

    def test_token_invalido(self):
        r404 = SimpleNamespace(status_code=404, json=lambda: {})
        r400u = SimpleNamespace(status_code=400, json=lambda: {'error': {'details': [{'errorCode': 'UNREGISTERED'}]}})
        r400 = SimpleNamespace(status_code=400, json=lambda: {'error': {'message': 'Invalid JSON payload'}})
        r500 = SimpleNamespace(status_code=500, json=lambda: {})
        self.assertTrue(fcm._es_token_invalido(r404))
        self.assertTrue(fcm._es_token_invalido(r400u))
        self.assertFalse(fcm._es_token_invalido(r400))
        self.assertFalse(fcm._es_token_invalido(r500))

    @override_settings(FCM_HABILITADO=False)
    def test_apagado_no_consulta_dispositivos(self):
        with patch('realtime.models.DispositivoFCM.objects') as objs:
            self.assertEqual(fcm.enviar_items([(1, {})]), 0)
            objs.filter.assert_not_called()

    @override_settings(FCM_HABILITADO=True, FCM_PROJECT_ID='p')
    def test_lote_desactiva_tokens_unregistered(self):
        sesion = MagicMock()
        sesion.post.side_effect = [
            SimpleNamespace(status_code=200, json=lambda: {}),
            SimpleNamespace(status_code=404, json=lambda: {}),
            ConnectionError('red'),
        ]
        with patch('realtime.models.DispositivoFCM.objects') as objs, patch.object(fcm, 'EJECUTAR_EN_LINEA', True):
            enviados, invalidos = fcm._enviar_lote([('tokA', {}), ('tokB', {}), ('tokC', {})], sesion=sesion)
        self.assertEqual((enviados, invalidos), (1, ['tokB']))
        objs.filter.assert_called_once_with(token__in=['tokB'])
        self.assertIn('fcm.googleapis.com/v1/projects/p/messages:send', sesion.post.call_args_list[0].args[0])


class SenalesUsuariosTests(SimpleTestCase):
    databases = set()

    def _guardar(self, previo, activo, rol):
        inst = SimpleNamespace(pk=7, activo=activo, id_rol_id=rol, _realtime_previo=previo)
        llamadas = []
        with patch('realtime.senales.notificar.actualizacion_silenciosa') as as_, \
                patch('realtime.senales.publicar.al_confirmar', side_effect=lambda f, **k: llamadas.append(f)), \
                patch('realtime.senales.publicar.revocar_sesion') as rev, \
                patch('realtime.senales.fcm.desactivar_tokens_usuario') as des:
            senales._usuario_guardado(sender=None, instance=inst, created=False)
            for f in llamadas:
                f()
        return as_, rev, des

    def test_usr01_siempre_y_sin_revocar_si_nada_cambia(self):
        as_, rev, des = self._guardar((True, 3), True, 3)
        self.assertEqual(as_.call_args.args[0], 'admin.usuario_gestionado')
        self.assertEqual(as_.call_args.kwargs['roles'], (1, 5))
        rev.assert_not_called()
        des.assert_not_called()

    def test_seg01_inactivar_revoca_y_desactiva_fcm(self):
        _, rev, des = self._guardar((True, 3), False, 3)
        rev.assert_called_once_with(7, 'inactivado')
        des.assert_called_once_with(7)

    def test_seg01_cambio_de_rol_revoca(self):
        _, rev, des = self._guardar((True, 3), True, 2)
        rev.assert_called_once_with(7, 'rol_cambiado')
        des.assert_not_called()

    def test_registro_cubre_modelos_existentes(self):
        from django.apps import apps
        for etiqueta, event_type, _ in senales.REGISTRO_AS:
            self.assertIn(event_type, eventos.CATALOGO)
            apps.get_model(etiqueta)  # lanza si el modelo no existe
