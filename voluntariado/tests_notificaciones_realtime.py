"""
Pruebas de VOL-01 (`voluntariado.postulacion_creada`) y VOL-02 (`voluntariado.postulacion_resuelta`).

1) SimpleTestCase (sin BD): vistas reales con ORM y realtime.notificar parcheados.
2) TestCase (BD local test_vetericano): filas en notificaciones_sistema y atomicidad.

    python manage.py test voluntariado.tests_notificaciones_realtime --settings=vetericano.test_settings
"""
from types import SimpleNamespace
from unittest import mock

from django.db import DatabaseError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from users.api.permissions import PermisoVoluntariado
from voluntariado import notificaciones_realtime as notif_vol
from voluntariado.api.views import EventoVoluntariadoViewSet, PostulacionVoluntariadoViewSet
from voluntariado.models import EventoVoluntariado, PostulacionVoluntariado

ADMINS = [SimpleNamespace(id_usuario=1), SimpleNamespace(id_usuario=5)]


class _SinBDMixin:

    def setUp(self):
        super().setUp()
        self._parche_objeto(transaction.Atomic, '__enter__', lambda self_: None)
        self._parche_objeto(transaction.Atomic, '__exit__', lambda self_, *args: False)
        self._parche_objeto(PermisoVoluntariado, 'has_permission', lambda self_, request, view: True)
        self.np = self._parche('realtime.notificar.notificar_usuarios', return_value=[])
        self.activos = self._parche('realtime.notificar.usuarios_activos_por_rol', return_value=list(ADMINS))
        self.factory = APIRequestFactory()

    def _parche(self, objetivo, **kwargs):
        parche = mock.patch(objetivo, **kwargs)
        simulado = parche.start()
        self.addCleanup(parche.stop)
        return simulado

    def _parche_objeto(self, objeto, atributo, nuevo):
        parche = mock.patch.object(objeto, atributo, nuevo)
        parche.start()
        self.addCleanup(parche.stop)


class ClasificarEstadoTests(SimpleTestCase):

    def test_variantes(self):
        for valor in ('APROBADA', 'aprobado', ' Aceptada '):
            self.assertEqual(notif_vol.clasificar_estado(valor), 'aprobada')
        for valor in ('RECHAZADA', 'rechazado'):
            self.assertEqual(notif_vol.clasificar_estado(valor), 'rechazada')
        for valor in ('PENDIENTE', '', None, 'EN_REVISION'):
            self.assertIsNone(notif_vol.clasificar_estado(valor))


class Vol01PostularseTests(_SinBDMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self.evento = SimpleNamespace(id=4, pk=4, titulo='Jornada de esterilización')
        self._parche_objeto(EventoVoluntariadoViewSet, 'get_object', lambda self_: self.evento)
        self.modelo = self._parche('voluntariado.api.views.PostulacionVoluntariado')
        self.modelo.objects.filter.return_value.exists.return_value = False
        self.modelo.objects.create.side_effect = lambda **kw: SimpleNamespace(
            id=10, pk=10, evento=kw['evento'], evento_id=kw['evento'].id, estado=kw['estado'],
        )
        self.usuario = SimpleNamespace(
            id_usuario=44, pk=44, is_authenticated=True, activo=True, id_rol_id=4, nombre='Ana', apellido='Pérez',
            identificacion='', email='ana@example.com', telefono='',
        )

    def _postularse(self):
        request = self.factory.post('/api/voluntariado/eventos/4/postularse/', {}, format='json')
        force_authenticate(request, user=self.usuario)
        vista = EventoVoluntariadoViewSet.as_view({'post': 'postularse'})
        return vista(request, pk=4)

    def test_np_estricta_a_administradores_activos(self):
        respuesta = self._postularse()
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.data['id_postulacion'], 10)
        self.activos.assert_called_once_with([1])
        args, kwargs = self.np.call_args
        self.assertEqual((args[0], args[1]), ('voluntariado.postulacion_creada', ADMINS))
        self.assertTrue(kwargs['raise_on_error'])
        self.assertEqual(kwargs['excluir'], [self.usuario])
        self.assertEqual(kwargs['referencia'], {'id_postulacion': 10, 'id_evento': 4})
        for dato_personal in ('Ana', 'Pérez', 'ana@example.com'):
            self.assertNotIn(dato_personal, args[2] + args[3])

    def test_fallo_de_la_np_estricta_propaga(self):
        self.np.side_effect = DatabaseError('fallo inyectado')
        with self.assertRaises(DatabaseError):
            self._postularse()

    def test_ya_postulado_no_notifica(self):
        self.modelo.objects.filter.return_value.exists.return_value = True
        respuesta = self._postularse()
        self.assertEqual(respuesta.status_code, 400)
        self.np.assert_not_called()


class Vol02ResolucionTests(_SinBDMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self._parche_objeto(PostulacionVoluntariado, 'save', lambda self_, *a, **k: None)
        self.admin = SimpleNamespace(id_usuario=1, pk=1, is_authenticated=True, activo=True, id_rol_id=1)

    def _postulacion(self, estado='PENDIENTE', usuario_id=44):
        evento = EventoVoluntariado(id=4, titulo='Jornada de adopción', descripcion='d', imagen='x.jpg')
        postulacion = PostulacionVoluntariado(
            id=10, estado=estado, correo='ana@example.com', nombre_completo='Ana Pérez', usuario_id=usuario_id,
        )
        postulacion.evento = evento
        return postulacion

    def _patch(self, postulacion, estado):
        self._parche_objeto(PostulacionVoluntariadoViewSet, 'get_object', lambda self_: postulacion)
        request = self.factory.patch('/api/voluntariado/postulaciones/10/', {'estado': estado}, format='json')
        force_authenticate(request, user=self.admin)
        vista = PostulacionVoluntariadoViewSet.as_view({'patch': 'partial_update'})
        return vista(request, pk=10)

    def test_aprobar_np_estricta_al_postulante(self):
        respuesta = self._patch(self._postulacion(), 'APROBADA')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['estado'], 'APROBADA')
        args, kwargs = self.np.call_args
        self.assertEqual((args[0], args[1]), ('voluntariado.postulacion_resuelta', [44]))
        self.assertTrue(kwargs['raise_on_error'])
        self.assertEqual(kwargs['excluir'], [self.admin])
        self.assertEqual(kwargs['referencia'], {'id_postulacion': 10, 'id_evento': 4, 'estado': 'APROBADA'})
        self.assertEqual(kwargs['tipo'], 'POSTULACION_APROBADA')

    def test_rechazar(self):
        self._patch(self._postulacion(), 'RECHAZADA')
        self.assertEqual(self.np.call_args.kwargs['tipo'], 'POSTULACION_RECHAZADA')

    def test_misma_resolucion_o_pendiente_no_notifica(self):
        self._patch(self._postulacion(estado='APROBADA'), 'APROBADO')
        self._patch(self._postulacion(), 'PENDIENTE')
        self.np.assert_not_called()

    def test_sin_usuario_vinculado_no_notifica(self):
        respuesta = self._patch(self._postulacion(usuario_id=None), 'APROBADA')
        self.assertEqual(respuesta.status_code, 200)
        self.np.assert_not_called()

    def test_fallo_de_la_np_estricta_propaga(self):
        self.np.side_effect = DatabaseError('fallo inyectado')
        with self.assertRaises(DatabaseError):
            self._patch(self._postulacion(), 'APROBADA')


# ============================================================================
# Pruebas con BD real (PostgreSQL local test_vetericano)
# ============================================================================

FALLO_NP = 'realtime.models.NotificacionSistema.objects.create'


@override_settings(NOTIFICACIONES_SISTEMA_HABILITADAS=True)
class NotificacionesVoluntariadoBDTests(TestCase):
    """Requieren la BD local de pruebas."""

    def setUp(self):
        from inventario.tests.factories import crear_usuario

        self.admin = crear_usuario(rol_id=1)
        self.admin_inactivo = crear_usuario(rol_id=1, activo=False)
        self.juridico = crear_usuario(rol_id=2)
        self.voluntario = crear_usuario(rol_id=4)
        self.evento = EventoVoluntariado.objects.create(
            titulo='Jornada de prueba', descripcion='d', imagen='voluntariado_eventos/prueba.jpg', fecha='2026-12-01',
        )

    def _np(self, usuario, event_type):
        from realtime.models import NotificacionSistema

        return NotificacionSistema.objects.filter(id_usuario_destino=usuario, event_type=event_type)

    def _cliente(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente

    def test_vol01_np_para_administradores_activos(self):
        respuesta = self._cliente(self.voluntario).post(f'/api/voluntariado/eventos/{self.evento.pk}/postularse/', {}, format='json')
        self.assertEqual(respuesta.status_code, 201)
        notif = self._np(self.admin, 'voluntariado.postulacion_creada').get()
        self.assertEqual(notif.referencia, {'id_postulacion': respuesta.data['id_postulacion'], 'id_evento': self.evento.pk})
        self.assertFalse(self._np(self.admin_inactivo, 'voluntariado.postulacion_creada').exists())
        self.assertFalse(self._np(self.juridico, 'voluntariado.postulacion_creada').exists())
        self.assertFalse(self._np(self.voluntario, 'voluntariado.postulacion_creada').exists())

    def test_vol01_fallo_np_revierte_la_postulacion(self):
        with mock.patch(FALLO_NP, side_effect=DatabaseError('fallo inyectado')):
            with self.assertRaises(DatabaseError):
                self._cliente(self.voluntario).post(f'/api/voluntariado/eventos/{self.evento.pk}/postularse/', {}, format='json')
        self.assertFalse(PostulacionVoluntariado.objects.filter(evento=self.evento, usuario=self.voluntario).exists())

    def _postulacion(self):
        return PostulacionVoluntariado.objects.create(
            evento=self.evento, usuario=self.voluntario, correo='v@example.com', nombre_completo='V', estado='PENDIENTE',
        )

    def test_vol02_np_al_postulante(self):
        postulacion = self._postulacion()
        respuesta = self._cliente(self.admin).patch(
            f'/api/voluntariado/postulaciones/{postulacion.pk}/', {'estado': 'APROBADA'}, format='json',
        )
        self.assertEqual(respuesta.status_code, 200)
        notif = self._np(self.voluntario, 'voluntariado.postulacion_resuelta').get()
        self.assertEqual(notif.referencia['estado'], 'APROBADA')
        self.assertFalse(self._np(self.admin, 'voluntariado.postulacion_resuelta').exists())

    def test_vol02_fallo_np_revierte_el_cambio_de_estado(self):
        postulacion = self._postulacion()
        with mock.patch(FALLO_NP, side_effect=DatabaseError('fallo inyectado')):
            with self.assertRaises(DatabaseError):
                self._cliente(self.admin).patch(
                    f'/api/voluntariado/postulaciones/{postulacion.pk}/', {'estado': 'RECHAZADA'}, format='json',
                )
        postulacion.refresh_from_db()
        self.assertEqual(postulacion.estado, 'PENDIENTE')
