"""
Pruebas con BD (PostgreSQL local de pruebas: test_settings + plantilla template_vetericano_test).
Cubren persistencia individual, atomicidad (D10), publicación tras el COMMIT, aislamiento del REST
central, marcado de lectura (SYNC-01), dispositivos FCM y PET-03/04/05/06.

Requieren la BD de pruebas local (TEST_DB_PASSWORD válido). No tocan ninguna BD remota.
"""
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from peticiones.models import (
    EstadoPeticiones,
    HistorialPeticiones,
    NotificacionPeticion,
    Peticiones,
    TipoPeticion,
    Ubicaciones,
)
from realtime.models import DispositivoFCM, NotificacionSistema
from users.models import Rol, Usuarios


def _rol(pk, nombre):
    return Rol.objects.get_or_create(id_rol=pk, defaults={'nombre_rol': nombre})[0]


class BaseRT(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.r_admin, cls.r_jur, cls.r_vet, cls.r_pet = (
            _rol(1, 'Administrador'), _rol(2, 'Jurídico'), _rol(3, 'Veterinario'), _rol(4, 'Peticionario'))

        def u(n, rol, activo=True):
            return Usuarios.objects.create(id_rol=rol, nombre=n, email=f'{n}@rt.local.test', password='x', activo=activo)

        cls.admin = u('admin_rt', cls.r_admin)
        cls.juridico = u('juridico_rt', cls.r_jur)
        cls.vet1 = u('vet1_rt', cls.r_vet)
        cls.vet2 = u('vet2_rt', cls.r_vet)
        cls.ciudadano = u('ciudadano_rt', cls.r_pet)
        cls.tipo = TipoPeticion.objects.create(nombre='Tipo RT', activo=True)
        cls.estado_pend = EstadoPeticiones.objects.create(nombre='En revisión RT', activo=True, orden=1)
        cls.estado_proc = EstadoPeticiones.objects.create(nombre='En proceso RT', activo=True, orden=2)
        cls.ubic = Ubicaciones.objects.create(direccion='Calle RT', latitud=2.44, longitud=-76.60)

    def cliente(self, usuario):
        c = APIClient()
        c.force_authenticate(user=usuario)
        return c

    def peticion(self, asignado=None, radicado='RAD-RT-0001'):
        return Peticiones.objects.create(
            numero_radicado=radicado, id_tipo=self.tipo, id_ubicacion=self.ubic, id_estado=self.estado_pend,
            responsable=self.ciudadano, asignado_a=asignado, asignado_por=self.juridico if asignado else None,
            descripcion='Prueba realtime', prioridad='ALTA',
            fecha_asignacion=(timezone.now() - timedelta(days=10)) if asignado else None,
        )


@override_settings(WS_PUBLICAR_EVENTOS=True)
class AsignacionRealtimeTests(BaseRT):
    def test_pet03_asignacion_crea_np_individual_y_publica_solo_tras_commit(self):
        p = self.peticion()
        with patch('realtime.publicar.publicar') as pub, self.captureOnCommitCallbacks(execute=False) as callbacks:
            resp = self.cliente(self.juridico).put(f'/api/peticiones/{p.pk}/asignar/', {'asignado_a': self.vet1.pk}, format='json')
            self.assertEqual(resp.status_code, 200, resp.content)
            pub.assert_not_called()  # nada sale antes del COMMIT
        notifs = NotificacionPeticion.objects.filter(id_peticion=p, tipo='ASIGNACION')
        self.assertEqual([n.id_usuario_destino_id for n in notifs], [self.vet1.pk])
        self.assertTrue(HistorialPeticiones.objects.filter(id_peticion=p, tipo_cambio='ASIGNACION').exists())
        with patch('realtime.publicar.publicar', return_value=True) as pub:
            for cb in callbacks:
                cb()
        grupos = [c.args[0] for c in pub.call_args_list]
        self.assertIn([f'user_{self.vet1.pk}'], grupos)               # PET-03 NP solo al vet asignado
        self.assertIn(['rol_1', 'rol_2'], grupos)                      # PET-05 AS a gestión
        sobre_np = next(c.args[1] for c in pub.call_args_list if c.args[0] == [f'user_{self.vet1.pk}'])
        self.assertEqual(sobre_np['event_type'], 'peticion.asignada')
        self.assertEqual(sobre_np['data']['clave'], f'pet-{notifs[0].pk}')

    def test_fallo_en_la_notificacion_revierte_la_asignacion(self):
        p = self.peticion()
        with patch('peticiones.models.NotificacionPeticion.objects.create', side_effect=RuntimeError('fallo inyectado')), \
                patch('realtime.publicar.publicar') as pub:
            cliente = self.cliente(self.juridico)
            cliente.raise_request_exception = False
            resp = cliente.put(f'/api/peticiones/{p.pk}/asignar/', {'asignado_a': self.vet1.pk}, format='json')
        self.assertEqual(resp.status_code, 500)
        p.refresh_from_db()
        self.assertIsNone(p.asignado_a_id)
        self.assertFalse(HistorialPeticiones.objects.filter(id_peticion=p, tipo_cambio='ASIGNACION').exists())
        pub.assert_not_called()

    def test_redis_caido_no_afecta_la_respuesta(self):
        p = self.peticion()
        with patch('realtime.publicar._publicar_mensaje', side_effect=ConnectionError('redis')), \
                self.captureOnCommitCallbacks(execute=True):
            resp = self.cliente(self.juridico).put(f'/api/peticiones/{p.pk}/asignar/', {'asignado_a': self.vet1.pk}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(NotificacionPeticion.objects.filter(id_peticion=p, id_usuario_destino=self.vet1).exists())

    def test_pet04_reasignacion_avisa_al_vet_saliente(self):
        p = self.peticion(asignado=self.vet1, radicado='RAD-RT-0002')
        # Regla de negocio existente: solo se reasigna tras 5+ días sin avance -> se envejece la petición.
        Peticiones.objects.filter(pk=p.pk).update(fecha=timezone.now() - timedelta(days=10))
        with self.captureOnCommitCallbacks(execute=False):
            resp = self.cliente(self.juridico).put(f'/api/peticiones/{p.pk}/asignar/', {'asignado_a': self.vet2.pk}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(NotificacionPeticion.objects.filter(id_peticion=p, id_usuario_destino=self.vet2, tipo='REASIGNACION').exists())
        self.assertTrue(NotificacionPeticion.objects.filter(id_peticion=p, id_usuario_destino=self.vet1, tipo='DESASIGNACION').exists())

    def test_pet06_cambio_de_estado_notifica_responsable_y_excluye_al_actor(self):
        p = self.peticion(asignado=self.vet1, radicado='RAD-RT-0003')
        with self.captureOnCommitCallbacks(execute=False):
            resp = self.cliente(self.vet1).patch(f'/api/peticiones/{p.pk}/estado/', {'id_estado': self.estado_proc.pk}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        destinos = set(NotificacionPeticion.objects.filter(id_peticion=p, tipo='CAMBIO_ESTADO')
                       .values_list('id_usuario_destino_id', flat=True))
        self.assertEqual(destinos, {self.ciudadano.pk})  # el vet que hizo el cambio no se notifica


class EstadoTolerantePreexistenteTests(BaseRT):
    """Auditoría M1: los INSERT opcionales que la vista original traga no deben abortar la transacción de PET-06."""

    def test_fallo_en_seguimiento_opcional_no_revierte_el_cambio_de_estado(self):
        p = self.peticion(asignado=self.vet1, radicado='RAD-RT-0300')
        with patch('peticiones.api.views.SeguimientoPeticionesVisita.objects.create', side_effect=RuntimeError('falla opcional')), \
                self.captureOnCommitCallbacks(execute=False):
            resp = self.cliente(self.vet1).patch(f'/api/peticiones/{p.pk}/estado/',
                                                 {'id_estado': self.estado_proc.pk, 'observacion': 'con nota'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        p.refresh_from_db()
        self.assertEqual(p.id_estado_id, self.estado_proc.pk)
        self.assertTrue(HistorialPeticiones.objects.filter(id_peticion=p, tipo_cambio='CAMBIO_ESTADO').exists())


class CreacionPeticionRealtimeTests(BaseRT):
    PAYLOAD = {'descripcion': 'Canino herido en prueba realtime', 'direccion': 'Calle RT 1',
               'latitud': '2.4419', 'longitud': '-76.6063', 'prioridad': 'ALTA'}

    def test_pet01_creacion_crea_np_para_admin_y_juridico(self):
        datos = dict(self.PAYLOAD, id_tipo=self.tipo.pk)
        with patch('peticiones.api.views.enviar_correo_peticion_urgente_juridico', return_value={'enviado': False}), \
                self.captureOnCommitCallbacks(execute=False):
            resp = self.cliente(self.ciudadano).post('/api/peticiones/iniciar/', datos, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        destinos = set(NotificacionPeticion.objects.filter(id_peticion_id=resp.data['id_peticion'])
                       .values_list('id_usuario_destino_id', flat=True))
        self.assertTrue({self.admin.pk, self.juridico.pk} <= destinos)

    def test_pet01_fallo_en_la_notificacion_revierte_la_creacion(self):
        datos = dict(self.PAYLOAD, id_tipo=self.tipo.pk, descripcion='Creación que debe revertirse')
        antes = Peticiones.objects.count()
        with patch('peticiones.models.NotificacionPeticion.objects.create', side_effect=RuntimeError('fallo inyectado')), \
                patch('peticiones.api.views.enviar_correo_peticion_urgente_juridico', return_value={'enviado': False}):
            cliente = self.cliente(self.ciudadano)
            cliente.raise_request_exception = False
            resp = cliente.post('/api/peticiones/iniciar/', datos, format='json')
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(Peticiones.objects.count(), antes)


@override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=True)
class ApiCentralTests(BaseRT):
    def setUp(self):
        self.p = self.peticion(radicado='RAD-RT-0100')
        self.n_vet1 = NotificacionPeticion.objects.create(id_peticion=self.p, id_usuario_destino=self.vet1, tipo='ASIGNACION',
                                                          titulo='A', mensaje='m', leida=False, fecha_creacion=timezone.now())
        self.n_vet2 = NotificacionPeticion.objects.create(id_peticion=self.p, id_usuario_destino=self.vet2, tipo='ASIGNACION',
                                                          titulo='B', mensaje='m', leida=False, fecha_creacion=timezone.now())
        self.s_vet1 = NotificacionSistema.objects.create(id_usuario_destino=self.vet1, event_type='kit.solicitud_resuelta',
                                                         modulo='inventario', tipo='SOLICITUD_RESUELTA', titulo='K', mensaje='m',
                                                         referencia={'id_solicitud': 3})

    def test_lista_unificada_solo_del_usuario(self):
        resp = self.cliente(self.vet1).get('/api/notificaciones/?solo_no_leidas=true')
        self.assertEqual(resp.status_code, 200)
        claves = {i['clave'] for i in resp.json()['resultados']}
        self.assertEqual(claves, {f'pet-{self.n_vet1.pk}', f'sis-{self.s_vet1.pk}'})
        self.assertEqual(resp.json()['no_leidas'], 2)

    def test_filtro_por_modulo_y_contador(self):
        c = self.cliente(self.vet1)
        self.assertEqual({i['clave'] for i in c.get('/api/notificaciones/?modulo=inventario').json()['resultados']},
                         {f'sis-{self.s_vet1.pk}'})
        self.assertEqual(c.get('/api/notificaciones/contador/').json(),
                         {'no_leidas': 2, 'por_modulo': {'peticiones': 1, 'inventario': 1}})

    @override_settings(WS_PUBLICAR_EVENTOS=True)
    def test_leer_propia_publica_sync01_y_ajena_da_404(self):
        c = self.cliente(self.vet1)
        with patch('realtime.publicar.publicar') as pub, self.captureOnCommitCallbacks(execute=True):
            resp = c.patch(f'/api/notificaciones/pet-{self.n_vet1.pk}/leer/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['leida'])
        self.assertEqual(pub.call_args.args[0], [f'user_{self.vet1.pk}'])
        self.assertEqual(pub.call_args.args[1]['event_type'], 'notificacion.leida')
        with patch('realtime.publicar.publicar') as pub2, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(c.patch(f'/api/notificaciones/pet-{self.n_vet2.pk}/leer/').status_code, 404)
            self.assertEqual(c.patch('/api/notificaciones/xyz-1/leer/').status_code, 404)
        pub2.assert_not_called()
        self.n_vet2.refresh_from_db()
        self.assertFalse(self.n_vet2.leida)

    def test_leer_todas_por_modulo(self):
        resp = self.cliente(self.vet1).patch('/api/notificaciones/leer-todas/?modulo=inventario')
        self.assertEqual(resp.json(), {'actualizadas': 1})
        self.n_vet1.refresh_from_db()
        self.assertFalse(self.n_vet1.leida)

    def test_endpoint_heredado_de_lectura_conserva_contrato_y_publica(self):
        with patch('realtime.notificar.publicar.al_confirmar') as al:
            resp = self.cliente(self.vet1).patch(f'/api/peticiones/notificaciones/{self.n_vet1.pk}/leer/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id_notificacion', resp.json())
        al.assert_called_once()

    def test_dispositivos_registro_reasignacion_y_desactivar(self):
        token = 'f' * 160
        self.assertEqual(self.cliente(self.vet1).post('/api/notificaciones/dispositivos/', {'token': token}, format='json').status_code, 201)
        self.assertEqual(self.cliente(self.vet2).post('/api/notificaciones/dispositivos/', {'token': token}, format='json').status_code, 200)
        self.assertEqual(DispositivoFCM.objects.get(token=token).id_usuario_id, self.vet2.pk)
        self.assertEqual(self.cliente(self.vet1).post('/api/notificaciones/dispositivos/desactivar/', {'token': token}, format='json').json(),
                         {'desactivado': False})  # no es suyo
        self.assertEqual(self.cliente(self.vet2).post('/api/notificaciones/dispositivos/desactivar/', {'token': token}, format='json').json(),
                         {'desactivado': True})
        self.assertEqual(self.cliente(self.vet2).post('/api/notificaciones/dispositivos/', {'token': 'corto'}, format='json').status_code, 400)

    def test_sin_autenticar_401(self):
        self.assertEqual(APIClient().get('/api/notificaciones/').status_code, 401)


@override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=True)
class NotificarPersistenciaTests(BaseRT):
    def test_np_estricta_revierte_con_la_operacion(self):
        from django.db import transaction
        from realtime import notificar
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                notificar.notificar_usuarios('kit.solicitud_creada', [self.admin, self.juridico], 'T', 'M', raise_on_error=True)
                raise RuntimeError('la operación de negocio falla después')
        self.assertFalse(NotificacionSistema.objects.filter(event_type='kit.solicitud_creada').exists())

    def test_dedup_por_clave(self):
        from realtime import notificar
        notificar.notificar_usuarios('kit.alerta_stock_vencimiento', [self.vet1], 'T', 'M', clave_dedup='venc:1:30')
        notificar.notificar_usuarios('kit.alerta_stock_vencimiento', [self.vet1], 'T', 'M', clave_dedup='venc:1:30')
        self.assertEqual(NotificacionSistema.objects.filter(clave_dedup='venc:1:30').count(), 1)

    def test_seg01_inactivar_usuario_revoca_y_desactiva_fcm(self):
        DispositivoFCM.objects.create(id_usuario=self.vet2, token='g' * 160)
        with patch('realtime.publicar.revocar_sesion') as rev, self.captureOnCommitCallbacks(execute=True):
            self.vet2.activo = False
            self.vet2.save()
        rev.assert_called_once_with(self.vet2.pk, 'inactivado')
        self.assertFalse(DispositivoFCM.objects.get(token='g' * 160).activo)


@override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=False)
class ApiCentralSinMigracionTests(BaseRT):
    """Auditoría A2: antes de aplicar la migración realtime la API central no debe tocar las tablas nuevas."""

    def setUp(self):
        self.p = self.peticion(radicado='RAD-RT-0200')
        self.n = NotificacionPeticion.objects.create(id_peticion=self.p, id_usuario_destino=self.vet1, tipo='ASIGNACION',
                                                     titulo='A', mensaje='m', leida=False, fecha_creacion=timezone.now())

    def test_sin_sql_contra_tablas_nuevas_y_sin_500(self):
        c = self.cliente(self.vet1)
        with CaptureQueriesContext(connection) as ctx:
            lista = c.get('/api/notificaciones/?solo_no_leidas=true')
            contador = c.get('/api/notificaciones/contador/')
            leer_sis = c.patch('/api/notificaciones/sis-1/leer/')
            leer_pet = c.patch(f'/api/notificaciones/pet-{self.n.pk}/leer/')
            todas = c.patch('/api/notificaciones/leer-todas/')
            disp = c.post('/api/notificaciones/dispositivos/', {'token': 'f' * 160}, format='json')
            desac = c.post('/api/notificaciones/dispositivos/desactivar/', {'token': 'f' * 160}, format='json')
        self.assertEqual(lista.status_code, 200)
        self.assertEqual([i['clave'] for i in lista.json()['resultados']], [f'pet-{self.n.pk}'])
        self.assertEqual(contador.json(), {'no_leidas': 1, 'por_modulo': {'peticiones': 1}})
        self.assertEqual(leer_sis.status_code, 404)
        self.assertEqual(leer_pet.status_code, 200)
        self.assertEqual(todas.status_code, 200)
        self.assertEqual((disp.status_code, desac.status_code), (503, 503))
        sql = ' '.join(q['sql'] for q in ctx.captured_queries).lower()
        self.assertNotIn('notificaciones_sistema', sql)
        self.assertNotIn('dispositivos_fcm', sql)


@override_settings(WS_PUBLICAR_EVENTOS=True, NOTIFICACIONES_SISTEMA_HABILITADAS=True)
class EventosPorSenalYDuplicadoBDTests(BaseRT):
    """PET-07, PET-09 y eventos derivados de señales (AS) con filas reales y destinatarios exactos."""

    def _publicados(self, accion):
        with patch('realtime.publicar.publicar_evento', return_value=True) as pub,                 self.captureOnCommitCallbacks(execute=True):
            accion()
        return [(c.args[0], c.args[1], tuple(sorted(c.kwargs.get('roles') or ())), tuple(c.kwargs.get('usuarios') or ()))
                for c in pub.call_args_list]

    def test_pet07_np_individual_admin_juridico_y_ao(self):
        from peticiones.services.alertas_duplicados_service import _notificar_posible_duplicado_tiempo_real
        a, b = self.peticion(radicado='RAD-RT-0400'), self.peticion(radicado='RAD-RT-0401')
        pubs = self._publicados(lambda: _notificar_posible_duplicado_tiempo_real(a, b))
        destinos = set(NotificacionPeticion.objects.filter(id_peticion=a, tipo='POSIBLE_DUPLICADO')
                       .values_list('id_usuario_destino_id', flat=True))
        self.assertEqual(destinos, {self.admin.pk, self.juridico.pk})
        self.assertIn(('peticion.posible_duplicado', 'AO', (1, 2), ()), pubs)
        np_usuarios = {u for ev, cat, _, us in pubs if ev == 'peticion.posible_duplicado' and cat == 'NP' for u in us}
        self.assertEqual(np_usuarios, {self.admin.pk, self.juridico.pk})

    def test_pet09_seguimiento_creado_ao_a_gestion(self):
        from peticiones.models import SeguimientoPeticionesVisita
        p = self.peticion(asignado=self.vet1, radicado='RAD-RT-0402')
        pubs = self._publicados(lambda: SeguimientoPeticionesVisita.objects.create(
            id_peticion=p, id_veterinario=self.vet1, observacion='visita'))
        self.assertIn(('peticion.seguimiento_creado', 'AO', (1, 2), ()), pubs)

    def test_as_por_senal_con_roles_del_contrato(self):
        from especies.models import Especie
        from inventario.tests.factories import crear_lote_bodega, crear_medicamento
        from users.models import Sede
        from voluntariado.models import EventoVoluntariado

        casos = [
            (lambda: Sede.objects.create(nombre='Sede RT', direccion='d', ciudad='c', telefono='1', codigo='RT1'),
             'admin.sede_actualizada', (5,)),
            (lambda: EventoVoluntariado.objects.create(titulo='J', descripcion='d', imagen='x.jpg', fecha='2026-12-01'),
             'voluntariado.evento_actualizado', (1, 2, 3, 4)),
            (lambda: Especie.objects.create(nombre='Felino RT', activo=True), 'catalogo.clinico_actualizado', (1, 3)),
            (lambda: crear_medicamento(), 'catalogo.medicamentos_actualizado', (1, 2, 3)),
            (lambda: crear_lote_bodega(crear_medicamento(), cantidad=5), 'inventario.movimiento_bodega', (1, 2)),
        ]
        for accion, evento, roles in casos:
            with self.subTest(evento=evento):
                pubs = self._publicados(accion)
                self.assertIn((evento, 'AS', roles, ()), pubs)
                # Coalescencia: un solo evento AS por tipo y destinatarios dentro de la transacción
                self.assertEqual(len([p for p in pubs if p[0] == evento]), 1)

    def test_cli01_historia_clinica_solo_admin_y_vets(self):
        from inventario.tests.factories import crear_animal_con_historia
        from especies.models import Especie, Raza
        especie, _ = Especie.objects.get_or_create(id_especie=1, defaults={'nombre': 'Canino', 'activo': True})
        Raza.objects.get_or_create(id_raza=1, defaults={'id_especie': especie, 'nombre': 'Mestizo', 'activo': True})
        pubs = self._publicados(lambda: crear_animal_con_historia())
        self.assertIn(('clinica.atencion_registrada', 'AS', (1, 3), ()), pubs)
        self.assertFalse([p for p in pubs if p[0] == 'clinica.atencion_registrada' and 2 in p[2]])

    def test_usr01_gestion_de_usuario_a_admin_y_superadmin(self):
        # setUpTestData creó usuarios dentro de la transacción de la clase (nunca confirmada): su USR-01 queda
        # pendiente y la coalescencia por transacción lo reutilizaría. En una petición real cada transacción
        # empieza sin callbacks pendientes; se reproduce eso aquí.
        connection.run_on_commit.clear()

        def editar():
            self.vet1.nombre = 'Vet renombrado'
            self.vet1.save()
        pubs = self._publicados(editar)
        self.assertIn(('admin.usuario_gestionado', 'AS', (1, 5), ()), pubs)
        self.assertFalse([p for p in pubs if p[0] == 'sesion.revocada'])

