"""
Pruebas del módulo Super Administrador (Rol 5: super_administrador):
1. Gestión de Administradores (CRUD):
   - POST /api/super-administrador/administradores/crear/
   - GET /api/super-administrador/administradores/
   - GET /api/super-administrador/administradores/<id>/
   - PUT/PATCH /api/super-administrador/administradores/<id>/
   - DELETE /api/super-administrador/administradores/<id>/
2. Reportes:
   - GET /api/super-administrador/reportes/peticiones/
   - GET /api/super-administrador/reportes/atendidas/
   - GET /api/super-administrador/reportes/estadisticas/
   - Filtros por fecha (fecha, fecha_desde, fecha_hasta), estado (estado, id_estado) y veterinario (veterinario, asignado_a).
3. Seguridad:
   - Protección con JWT (401 sin token).
   - Acceso exclusivo para Super Administrador (403 para Administrador, Jurídico, Veterinario y Peticionario).
   - Prohibición de que Administradores creen o asignen el rol Super Administrador (403).
4. Documentación en Swagger (/swagger/?format=openapi).
"""

from datetime import datetime
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from django.urls import reverse, resolve
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from users.constants import (
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_VETERINARIO,
    ROL_PETICIONARIO,
    ROL_SUPER_ADMINISTRADOR,
)
from users.models import Rol, Usuarios
from users.api.views import (
    UsuariosViewSet,
    SuperAdminCrearAdministradorView,
    SuperAdminListarAdministradoresView,
    SuperAdminDetalleAdministradorView,
    SuperAdminReportePeticionesView,
    SuperAdminReporteAtendidasView,
    SuperAdminReporteEstadisticasView,
    _aplicar_filtros_reportes_peticiones,
)
from vetericano.urls import schema_view


class DummyUserSuperAdmin:
    """Usuario simulado para pruebas HTTP sobre vistas DRF."""
    _meta = Usuarios._meta

    def __init__(self, id_usuario, rol_id, nombre_rol=None, activo=True):
        self.id_usuario = id_usuario
        self.pk = id_usuario
        self.id_rol_id = rol_id
        default_names = {
            ROL_ADMINISTRADOR: 'Administrador',
            ROL_JURIDICO: 'Juridico',
            ROL_VETERINARIO: 'Veterinario',
            ROL_PETICIONARIO: 'Peticionario',
            ROL_SUPER_ADMINISTRADOR: 'super_administrador',
        }
        self.id_rol = MagicMock(
            id_rol=rol_id,
            nombre_rol=nombre_rol or default_names.get(rol_id, 'Desconocido'),
        )
        self.activo = activo
        self.is_authenticated = True
        self.is_anonymous = False
        self.email = f"user{id_usuario}@vetericano.gov.co"
        self.nombre = f"Usuario {id_usuario}"
        self.apellido = "Prueba"


class SuperAdministradorHTTPTestCase(SimpleTestCase):
    """Pruebas unitarias y de despacho HTTP para el módulo Super Administrador."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.super_admin = DummyUserSuperAdmin(id_usuario=500, rol_id=ROL_SUPER_ADMINISTRADOR)
        self.admin = DummyUserSuperAdmin(id_usuario=1, rol_id=ROL_ADMINISTRADOR)
        self.juridico = DummyUserSuperAdmin(id_usuario=2, rol_id=ROL_JURIDICO)
        self.veterinario = DummyUserSuperAdmin(id_usuario=3, rol_id=ROL_VETERINARIO)
        self.peticionario = DummyUserSuperAdmin(id_usuario=4, rol_id=ROL_PETICIONARIO)

    def _dispatch(self, view_callable, method, path, user=None, data=None, **kwargs):
        req_fn = getattr(self.factory, method.lower())
        request = req_fn(path, data=data, format='json') if data is not None else req_fn(path)
        if user is not None:
            force_authenticate(request, user=user)
        return view_callable(request, **kwargs)

    def test_01_resolucion_de_endpoints_super_administrador(self):
        """Verifica que todas las rutas solicitadas estén registradas y resuelvan a sus vistas."""
        self.assertEqual(
            reverse('super-admin-administradores-crear'),
            '/api/super-administrador/administradores/crear/',
        )
        self.assertEqual(
            reverse('super-admin-administradores-listar'),
            '/api/super-administrador/administradores/',
        )
        self.assertEqual(
            reverse('super-admin-administradores-detalle', kwargs={'id': 10}),
            '/api/super-administrador/administradores/10/',
        )
        self.assertEqual(
            reverse('super-admin-reportes-peticiones'),
            '/api/super-administrador/reportes/peticiones/',
        )
        self.assertEqual(
            reverse('super-admin-reportes-atendidas'),
            '/api/super-administrador/reportes/atendidas/',
        )
        self.assertEqual(
            reverse('super-admin-reportes-estadisticas'),
            '/api/super-administrador/reportes/estadisticas/',
        )

        self.assertEqual(resolve('/api/super-administrador/administradores/crear/').func.view_class, SuperAdminCrearAdministradorView)
        self.assertEqual(resolve('/api/super-administrador/administradores/').func.view_class, SuperAdminListarAdministradoresView)
        self.assertEqual(resolve('/api/super-administrador/administradores/10/').func.view_class, SuperAdminDetalleAdministradorView)
        self.assertEqual(resolve('/api/super-administrador/reportes/peticiones/').func.view_class, SuperAdminReportePeticionesView)
        self.assertEqual(resolve('/api/super-administrador/reportes/atendidas/').func.view_class, SuperAdminReporteAtendidasView)
        self.assertEqual(resolve('/api/super-administrador/reportes/estadisticas/').func.view_class, SuperAdminReporteEstadisticasView)

    def test_02_bloqueo_401_sin_token_jwt_en_todos_los_endpoints(self):
        """Verifica que ningún endpoint del Super Administrador sea accesible sin autenticación JWT (HTTP 401)."""
        endpoints = [
            (SuperAdminCrearAdministradorView.as_view(), 'POST', '/api/super-administrador/administradores/crear/', {}, {}),
            (SuperAdminListarAdministradoresView.as_view(), 'GET', '/api/super-administrador/administradores/', None, {}),
            (SuperAdminDetalleAdministradorView.as_view(), 'GET', '/api/super-administrador/administradores/1/', None, {'id': 1}),
            (SuperAdminDetalleAdministradorView.as_view(), 'PUT', '/api/super-administrador/administradores/1/', {}, {'id': 1}),
            (SuperAdminDetalleAdministradorView.as_view(), 'PATCH', '/api/super-administrador/administradores/1/', {}, {'id': 1}),
            (SuperAdminDetalleAdministradorView.as_view(), 'DELETE', '/api/super-administrador/administradores/1/', None, {'id': 1}),
            (SuperAdminReportePeticionesView.as_view(), 'GET', '/api/super-administrador/reportes/peticiones/', None, {}),
            (SuperAdminReporteAtendidasView.as_view(), 'GET', '/api/super-administrador/reportes/atendidas/', None, {}),
            (SuperAdminReporteEstadisticasView.as_view(), 'GET', '/api/super-administrador/reportes/estadisticas/', None, {}),
        ]
        for view_fn, method, url, payload, kwargs in endpoints:
            with self.subTest(method=method, url=url):
                resp = self._dispatch(view_fn, method, url, user=None, data=payload, **kwargs)
                self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_03_bloqueo_403_para_administrador_juridico_veterinario_y_peticionario(self):
        """Verifica que Administrador (1), Jurídico (2), Veterinario (3) y Peticionario (4) reciban HTTP 403."""
        endpoints = [
            (SuperAdminCrearAdministradorView.as_view(), 'POST', '/api/super-administrador/administradores/crear/', {'email': 'a@a.com', 'nombre': 'A'}, {}),
            (SuperAdminListarAdministradoresView.as_view(), 'GET', '/api/super-administrador/administradores/', None, {}),
            (SuperAdminDetalleAdministradorView.as_view(), 'GET', '/api/super-administrador/administradores/1/', None, {'id': 1}),
            (SuperAdminDetalleAdministradorView.as_view(), 'PUT', '/api/super-administrador/administradores/1/', {'nombre': 'X'}, {'id': 1}),
            (SuperAdminDetalleAdministradorView.as_view(), 'PATCH', '/api/super-administrador/administradores/1/', {'activo': False}, {'id': 1}),
            (SuperAdminDetalleAdministradorView.as_view(), 'DELETE', '/api/super-administrador/administradores/1/', None, {'id': 1}),
            (SuperAdminReportePeticionesView.as_view(), 'GET', '/api/super-administrador/reportes/peticiones/', None, {}),
            (SuperAdminReporteAtendidasView.as_view(), 'GET', '/api/super-administrador/reportes/atendidas/', None, {}),
            (SuperAdminReporteEstadisticasView.as_view(), 'GET', '/api/super-administrador/reportes/estadisticas/', None, {}),
        ]
        for rol_user in (self.admin, self.juridico, self.veterinario, self.peticionario):
            for view_fn, method, url, payload, kwargs in endpoints:
                with self.subTest(rol=rol_user.id_rol.nombre_rol, method=method, url=url):
                    resp = self._dispatch(view_fn, method, url, user=rol_user, data=payload, **kwargs)
                    self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_04_administrador_no_puede_crear_super_administrador(self):
        """Verifica que un Administrador (Rol 1) reciba 403 Forbidden al intentar crear o promover un Super Administrador."""
        create_user_view = UsuariosViewSet.as_view({'post': 'create'})
        payload = {
            'email': 'intento.super@vetericano.gov.co',
            'nombre': 'Intento',
            'apellido': 'Escalacion',
            'id_rol': ROL_SUPER_ADMINISTRADOR,
        }
        resp = self._dispatch(create_user_view, 'POST', '/api/usuarios/usuarios/', user=self.admin, data=payload)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        payload_nom = {
            'email': 'intento2.super@vetericano.gov.co',
            'nombre': 'Intento',
            'id_rol': 'super_administrador',
        }
        resp_nom = self._dispatch(create_user_view, 'POST', '/api/usuarios/usuarios/', user=self.admin, data=payload_nom)
        self.assertEqual(resp_nom.status_code, status.HTTP_403_FORBIDDEN)

    @patch('users.api.serializers.Rol.objects')
    @patch('users.api.serializers.Usuarios.objects')
    def test_05_crud_crear_listar_consultar_editar_eliminar_administradores(self, mock_user_objs, mock_rol_objs):
        """Verifica el ciclo CRUD completo de administradores por parte del Super Administrador."""
        rol_admin = Rol(id_rol=ROL_ADMINISTRADOR, nombre_rol='Administrador')
        mock_rol_objs.filter.return_value.first.return_value = rol_admin

        # Mock de unicidad para ModelSerializer
        mock_user_objs.all.return_value.filter.return_value.exists.return_value = False
        mock_user_objs.filter.return_value.exists.return_value = False

        def fake_create(**kwargs):
            u = Usuarios(id_usuario=77, **kwargs)
            u.id_rol = rol_admin
            u.id_rol_id = ROL_ADMINISTRADOR
            return u

        mock_user_objs.create.side_effect = fake_create

        # 1. Crear Administrador
        crear_view = SuperAdminCrearAdministradorView.as_view()
        resp_post = self._dispatch(
            crear_view,
            'POST',
            '/api/super-administrador/administradores/crear/',
            user=self.super_admin,
            data={
                'email': 'nuevo.admin@vetericano.gov.co',
                'nombre': 'Carlos',
                'apellido': 'Admin',
                'identificacion': '1061999888',
                'telefono': '3101234567',
            },
        )
        self.assertEqual(resp_post.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp_post.data['id_usuario'], 77)
        self.assertEqual(resp_post.data['id_rol'], ROL_ADMINISTRADOR)
        self.assertEqual(resp_post.data['activo'], True)

        # 2. Listar Administradores
        admin_inst = Usuarios(
            id_usuario=77,
            email='nuevo.admin@vetericano.gov.co',
            nombre='Carlos',
            apellido='Admin',
            identificacion='1061999888',
            telefono='3101234567',
            activo=True,
        )
        admin_inst.id_rol = rol_admin
        admin_inst.id_rol_id = ROL_ADMINISTRADOR

        with patch.object(SuperAdminListarAdministradoresView, 'get_queryset', return_value=[admin_inst]):
            listar_view = SuperAdminListarAdministradoresView.as_view()
            resp_list = self._dispatch(listar_view, 'GET', '/api/super-administrador/administradores/', user=self.super_admin)
            self.assertEqual(resp_list.status_code, status.HTTP_200_OK)
            self.assertEqual(len(resp_list.data), 1)
            self.assertEqual(resp_list.data[0]['email'], 'nuevo.admin@vetericano.gov.co')

        # 3. Consultar por ID, PATCH y DELETE
        with patch.object(SuperAdminDetalleAdministradorView, 'get_object', return_value=admin_inst), \
             patch.object(Usuarios, 'save'), \
             patch.object(Usuarios, 'delete'):
            detalle_view = SuperAdminDetalleAdministradorView.as_view()

            resp_get = self._dispatch(detalle_view, 'GET', '/api/super-administrador/administradores/77/', user=self.super_admin, id=77)
            self.assertEqual(resp_get.status_code, status.HTTP_200_OK)
            self.assertEqual(resp_get.data['id_usuario'], 77)

            resp_patch = self._dispatch(
                detalle_view,
                'PATCH',
                '/api/super-administrador/administradores/77/',
                user=self.super_admin,
                data={'nombre': 'Carlos Editado', 'activo': False},
                id=77,
            )
            self.assertEqual(resp_patch.status_code, status.HTTP_200_OK)
            self.assertEqual(resp_patch.data['nombre'], 'Carlos Editado')
            self.assertEqual(resp_patch.data['activo'], False)

            resp_del = self._dispatch(
                detalle_view,
                'DELETE',
                '/api/super-administrador/administradores/77/',
                user=self.super_admin,
                id=77,
            )
            self.assertEqual(resp_del.status_code, status.HTTP_200_OK)
            self.assertEqual(resp_del.data['id_usuario'], 77)

    def test_06_filtros_reportes_fecha_estado_y_veterinario(self):
        """Verifica que _aplicar_filtros_reportes_peticiones aplique filtros por fecha, estado y veterinario."""
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs

        with patch('users.api.views.EstadoPeticiones.objects') as mock_est:
            est_obj = MagicMock(id_estado=7, nombre='Atendida')
            mock_est.all.return_value = [est_obj]

            _aplicar_filtros_reportes_peticiones(
                mock_qs,
                {
                    'fecha': '2026-11-10',
                    'fecha_desde': '2026-11-01',
                    'fecha_hasta': '2026-11-30',
                    'estado': 'Atendida',
                    'veterinario': '14',
                },
            )
            self.assertTrue(mock_qs.filter.called)
            self.assertGreaterEqual(mock_qs.filter.call_count, 5)

    def test_07_documentacion_swagger_incluye_endpoints_super_administrador(self):
        """Verifica que el esquema OpenAPI de Swagger documente todos los endpoints del Super Administrador."""
        req = self.factory.get('/swagger/?format=openapi')
        resp = schema_view.without_ui(cache_timeout=0)(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        paths = resp.data.get('paths', {})
        rutas_esperadas = [
            '/super-administrador/administradores/crear/',
            '/super-administrador/administradores/',
            '/super-administrador/administradores/{id}/',
            '/super-administrador/reportes/peticiones/',
            '/super-administrador/reportes/atendidas/',
            '/super-administrador/reportes/estadisticas/',
        ]
        for ruta in rutas_esperadas:
            self.assertIn(ruta, paths, f"Falta documentar {ruta} en Swagger")
