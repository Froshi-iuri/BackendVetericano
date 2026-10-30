from datetime import timedelta
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from peticiones.models import (
    Peticiones,
    TipoPeticion,
    EstadoPeticiones,
    HistorialAsignacionPeticion,
    NotificacionJuridico,
)
from peticiones.api.permissions import IsJuridicoOrAdmin
from peticiones.api.views import (
    DashboardJuridicoView,
    AsignarPeticionView,
    NotificacionesJuridicoView,
    MarcarNotificacionLeidaView,
    HistorialAsignacionesPorPeticionView,
)
from peticiones.api.serializers import (
    HistorialAsignacionPeticionSerializer,
    NotificacionJuridicoSerializer,
)
from clinica.models import Consulta, HistoriaClinica
from clinica.api.views import ConsultaViewSet
from users.models import Usuarios, Rol


class MockUser:
    """Usuario simulado en memoria para validación exhaustiva de permisos."""
    def __init__(self, id_usuario, rol_id, nombre="Test", apellido="User", email="test@cba.gov.co", activo=True):
        self.id_usuario = id_usuario
        self.id_rol_id = rol_id
        self.id_rol = MagicMock(id_rol=rol_id)
        self.nombre = nombre
        self.apellido = apellido
        self.email = email
        self.activo = activo
        self.is_authenticated = True
        self.pk = id_usuario


class PermisosJuridicoOrAdminTestCase(SimpleTestCase):
    """Pruebas unitarias para la clase de permisos IsJuridicoOrAdmin (Requerimiento 6)."""

    def setUp(self):
        self.permission = IsJuridicoOrAdmin()
        self.factory = APIRequestFactory()

    def test_admin_rol_1_permitido(self):
        request = self.factory.get('/')
        request.user = MockUser(id_usuario=1, rol_id=1, nombre="Admin")
        self.assertTrue(self.permission.has_permission(request, None))

    def test_juridico_rol_2_permitido(self):
        request = self.factory.get('/')
        request.user = MockUser(id_usuario=2, rol_id=2, nombre="Juridico")
        self.assertTrue(self.permission.has_permission(request, None))

    def test_veterinario_rol_3_denegado(self):
        request = self.factory.get('/')
        request.user = MockUser(id_usuario=3, rol_id=3, nombre="Veterinario")
        self.assertFalse(self.permission.has_permission(request, None))

    def test_peticionario_rol_4_denegado(self):
        request = self.factory.get('/')
        request.user = MockUser(id_usuario=4, rol_id=4, nombre="Peticionario")
        self.assertFalse(self.permission.has_permission(request, None))

    def test_usuario_anonimo_denegado(self):
        request = self.factory.get('/')
        request.user = MagicMock(is_authenticated=False)
        self.assertFalse(self.permission.has_permission(request, None))

    def test_usuario_none_denegado(self):
        request = self.factory.get('/')
        request.user = None
        self.assertFalse(self.permission.has_permission(request, None))


class SerializadoresJuridicoTestCase(SimpleTestCase):
    """Pruebas unitarias para serializadores de historial y notificaciones."""

    def test_historial_asignacion_serializer_metodos(self):
        pet = MagicMock(id_peticion=10, numero_radicado="PET-2026-00010")
        vet_ant = MagicMock(nombre="Carlos", apellido="Pérez")
        vet_nvo = MagicMock(nombre="Laura", apellido="Gómez")
        asig_por = MagicMock(nombre="Abogado", apellido="Jurídico")

        historial = MagicMock(
            id_historial=1,
            id_peticion=pet,
            veterinario_anterior=vet_ant,
            veterinario_nuevo=vet_nvo,
            motivo="Reasignación por sobrecarga",
            asignado_por=asig_por,
            fecha=timezone.now(),
        )

        serializer = HistorialAsignacionPeticionSerializer(historial)
        self.assertEqual(serializer.data['veterinario_anterior_nombre'], "Carlos Pérez")
        self.assertEqual(serializer.data['veterinario_nuevo_nombre'], "Laura Gómez")
        self.assertEqual(serializer.data['asignado_por_nombre'], "Abogado Jurídico")
        self.assertEqual(serializer.data['motivo'], "Reasignación por sobrecarga")

    def test_notificacion_juridico_serializer_campos(self):
        pet = MagicMock(id_peticion=25, numero_radicado="PET-2026-00025")
        notif = MagicMock(
            id_notificacion=7,
            id_peticion=pet,
            tipo="NUEVA_URGENTE",
            mensaje="Caso urgente reportado en Comuna 2",
            leida=False,
            fecha_creacion=timezone.now(),
        )

        serializer = NotificacionJuridicoSerializer(notif)
        self.assertEqual(serializer.data['id_notificacion'], 7)
        self.assertEqual(serializer.data['tipo'], "NUEVA_URGENTE")
        self.assertEqual(serializer.data['mensaje'], "Caso urgente reportado en Comuna 2")
        self.assertFalse(serializer.data['leida'])
        self.assertEqual(serializer.data['numero_radicado'], "PET-2026-00025")


class ConsultaCBAFiltroTestCase(SimpleTestCase):
    """Pruebas del filtro especializado para animales en CBA (Requerimiento 4)."""

    def setUp(self):
        self.factory = APIRequestFactory()

    def test_consulta_viewset_tiene_action_cba(self):
        """Verifica que ConsultaViewSet tenga registrada la acción 'cba'."""
        self.assertTrue(hasattr(ConsultaViewSet, 'cba'))
        action_func = getattr(ConsultaViewSet, 'cba')
        self.assertTrue(getattr(action_func, 'bind_to_methods', None) or hasattr(action_func, 'mapping') or getattr(action_func, 'detail', None) is False)

    @patch.object(ConsultaViewSet, 'get_queryset')
    def test_consulta_cba_url_action(self, mock_qs):
        """Verifica que el endpoint /cba/ responda con 200 para un usuario autenticado."""
        mock_qs.return_value.filter.return_value = []
        request = self.factory.get('/api/clinica/consultas/cba/')
        request.user = MockUser(id_usuario=1, rol_id=1)
        view = ConsultaViewSet.as_view({'get': 'cba'})
        force_authenticate(request, user=request.user)
        response = view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)


class DashboardJuridicoViewTestCase(SimpleTestCase):
    """Pruebas para el endpoint consolidado del Dashboard Jurídico (Requerimiento 1)."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DashboardJuridicoView.as_view()

    def test_acceso_denegado_para_rol_no_autorizado(self):
        request = self.factory.get('/api/peticiones/dashboard-juridico/')
        request.user = MockUser(id_usuario=3, rol_id=3)  # Veterinario
        force_authenticate(request, user=request.user)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('peticiones.api.views.Consulta.objects')
    @patch('peticiones.api.views.Usuarios.objects')
    @patch('peticiones.api.views.Peticiones.objects')
    def test_acceso_permitido_juridico_retorna_estructura_completa(self, mock_pet, mock_users, mock_consultas):
        # Configurar mocks de peticiones
        mock_pet.count.return_value = 10
        mock_pet.filter.return_value.count.side_effect = [3, 2]  # urgentes, atrasadas
        mock_pet.values.return_value.annotate.return_value = [
            {'id_estado__nombre': 'En evaluación', 'total': 4},
            {'id_estado__nombre': 'Asignada', 'total': 3},
            {'id_estado__nombre': 'Finalizada', 'total': 3},
        ]

        # Configurar mocks de veterinarios
        mock_vet1 = MagicMock(
            id_usuario=5,
            nombre="Carlos",
            apellido="Pérez",
            total_asig=15,
            pendientes_asig=5,
            en_proceso_asig=7,
            finalizadas_asig=3,
        )
        mock_users.filter.return_value.annotate.return_value.order_by.return_value = [mock_vet1]

        # Configurar mocks de consultas CBA
        mock_cba1 = MagicMock(
            id_consulta=42,
            fecha_hora=timezone.now(),
            canil_asignado="Canil 3",
            funcionario_recibe_cba="Dra. Smith",
            motivo_ingreso_cba="Ingreso CBA",
            pronostico="En Tratamiento",
            resultado_estado=None,
        )
        mock_qs_cba = MagicMock()
        mock_qs_cba.count.return_value = 5
        mock_qs_cba.filter.return_value.count.side_effect = [2, 2, 1]  # obs, trat, adop
        mock_qs_cba.order_by.return_value.__getitem__.return_value = [mock_cba1]
        mock_consultas.filter.return_value = mock_qs_cba

        request = self.factory.get('/api/peticiones/dashboard-juridico/')
        request.user = MockUser(id_usuario=2, rol_id=2)  # Jurídico
        force_authenticate(request, user=request.user)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data

        # 1. Validación de sección peticiones
        self.assertIn('peticiones', data)
        self.assertEqual(data['peticiones']['total'], 10)
        self.assertEqual(data['peticiones']['urgentes'], 3)
        self.assertEqual(data['peticiones']['atrasadas_mas_5dias'], 2)
        self.assertIn('distribucion_estados', data['peticiones'])

        # 2. Validación de sección veterinarios
        self.assertIn('veterinarios', data)
        self.assertIsInstance(data['veterinarios'], list)
        self.assertEqual(len(data['veterinarios']), 1)
        self.assertEqual(data['veterinarios'][0]['id_veterinario'], 5)
        self.assertEqual(data['veterinarios'][0]['nombre_completo'], "Carlos Pérez")
        self.assertEqual(data['veterinarios'][0]['porcentaje_completado'], 20)

        # 3. Validación de sección cba
        self.assertIn('cba', data)
        self.assertEqual(data['cba']['total_ingresados_cba'], 5)
        self.assertEqual(data['cba']['en_observacion'], 2)
        self.assertEqual(data['cba']['en_tratamiento'], 2)
        self.assertEqual(data['cba']['aptos_adopcion'], 1)
        self.assertIn('ultimos_ingresos', data['cba'])
        self.assertEqual(len(data['cba']['ultimos_ingresos']), 1)
        self.assertEqual(data['cba']['ultimos_ingresos'][0]['id_consulta'], 42)
        self.assertEqual(data['cba']['ultimos_ingresos'][0]['canil'], "Canil 3")


class NotificacionesJuridicoViewTestCase(SimpleTestCase):
    """Pruebas para los endpoints de notificaciones jurídicas (Requerimiento 5)."""

    def setUp(self):
        self.factory = APIRequestFactory()

    def test_acceso_denegado_para_veterinario(self):
        request = self.factory.get('/api/peticiones/notificaciones/')
        request.user = MockUser(id_usuario=3, rol_id=3)
        force_authenticate(request, user=request.user)
        view = NotificacionesJuridicoView.as_view()
        response = view(request)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('peticiones.api.views.NotificacionJuridico.objects')
    def test_listar_notificaciones_juridico_autorizado(self, mock_notif):
        mock_notif.select_related.return_value.filter.return_value.order_by.return_value = []
        request = self.factory.get('/api/peticiones/notificaciones/')
        request.user = MockUser(id_usuario=2, rol_id=2)
        force_authenticate(request, user=request.user)
        view = NotificacionesJuridicoView.as_view()
        response = view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    @patch('peticiones.api.views.NotificacionJuridico.objects')
    def test_marcar_leida_notificacion_inexistente_retorna_404(self, mock_notif):
        mock_notif.filter.return_value.first.return_value = None
        request = self.factory.patch('/api/peticiones/notificaciones/999999/marcar-leida/')
        request.user = MockUser(id_usuario=2, rol_id=2)
        force_authenticate(request, user=request.user)
        view = MarcarNotificacionLeidaView.as_view()
        response = view(request, id=999999)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)

    @patch('peticiones.api.views.NotificacionJuridico.objects')
    def test_marcar_todas_notificaciones_como_leidas(self, mock_notif):
        mock_notif.filter.return_value.update.return_value = 5
        request = self.factory.patch('/api/peticiones/notificaciones/marcar-leida/?todas=true')
        request.user = MockUser(id_usuario=2, rol_id=2)
        force_authenticate(request, user=request.user)
        view = MarcarNotificacionLeidaView.as_view()
        response = view(request, id=None)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['actualizadas'], 5)



class AsignarPeticionViewTestCase(SimpleTestCase):
    """Pruebas para AsignarPeticionView y trazabilidad SCRUM-210 (Requerimientos 2 y 3)."""

    def setUp(self):
        self.factory = APIRequestFactory()

    def test_asignar_peticion_acceso_denegado_veterinario(self):
        request = self.factory.patch('/api/peticiones/1/asignar/', {'asignado_a': 5})
        request.user = MockUser(id_usuario=3, rol_id=3)
        force_authenticate(request, user=request.user)
        view = AsignarPeticionView.as_view()
        response = view(request, id_peticion=1)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('peticiones.api.views.HistorialAsignacionPeticion.objects.create')
    @patch('peticiones.api.views.EstadoPeticiones.objects.filter')
    @patch('peticiones.api.views.Usuarios.objects.filter')
    @patch.object(AsignarPeticionView, 'get_object')
    def test_asignar_peticion_exito_transicion_estado_y_trazabilidad(
        self, mock_get_obj, mock_users_filter, mock_estado_filter, mock_historial_create
    ):
        mock_vet_anterior = MagicMock(id_usuario=4, nombre="Dr. Anterior")
        mock_vet_nuevo = MagicMock(id_usuario=5, id_rol_id=3, nombre="Dra. Nueva")
        mock_estado_asignada = MagicMock(id_estado=2, nombre="Asignada")

        mock_peticion = MagicMock(
            id_peticion=10,
            asignado_a=mock_vet_anterior,
            id_estado=MagicMock(id_estado=1, nombre="En evaluación"),
        )
        mock_get_obj.return_value = mock_peticion
        mock_users_filter.return_value.first.return_value = mock_vet_nuevo
        mock_estado_filter.return_value.first.return_value = mock_estado_asignada

        request = self.factory.patch(
            '/api/peticiones/10/asignar/',
            {'asignado_a': 5, 'motivo': 'Reasignación por redistribución de carga'},
            format='json'
        )
        request.user = MockUser(id_usuario=2, rol_id=2, nombre="Jurídico")
        force_authenticate(request, user=request.user)

        with patch('peticiones.api.views.transaction.atomic'), \
             patch.object(AsignarPeticionView, 'get_serializer') as mock_get_ser:
            mock_serializer = MagicMock()
            mock_serializer.is_valid.return_value = True
            mock_serializer.save.return_value = mock_peticion
            mock_serializer.data = {'id_peticion': 10, 'asignado_a': 5, 'id_estado': 2}
            mock_get_ser.return_value = mock_serializer

            view = AsignarPeticionView.as_view()
            response = view(request, id_peticion=10)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['id_estado'], 2)
            mock_serializer.save.assert_called_once()
            _, kwargs = mock_serializer.save.call_args
            self.assertEqual(kwargs.get('id_estado'), mock_estado_asignada)
            mock_historial_create.assert_called_once()
            hist_args, hist_kwargs = mock_historial_create.call_args
            self.assertEqual(hist_kwargs['veterinario_anterior'], mock_vet_anterior)
            self.assertEqual(hist_kwargs['veterinario_nuevo'], mock_vet_nuevo)
            self.assertEqual(hist_kwargs['motivo'], 'Reasignación por redistribución de carga')
