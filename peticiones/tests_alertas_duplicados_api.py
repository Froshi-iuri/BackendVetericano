"""
Pruebas unitarias de la Fase 4C: Endpoints de consulta y resolución de alertas
de posibles duplicados para Jurídico y Administrador.

Cubre:
- Autenticación (401) y control estricto de roles Administrador (1) y Jurídico (2) (403 a 3, 4, 5 e inactivos).
- Resolución de rutas y esquema Swagger (/swagger/?format=openapi).
- Filtros, alias y paginación en GET /api/peticiones/duplicados/alertas/ y GET /api/peticiones/<id>/duplicados/.
- Comparación detallada en GET /api/peticiones/duplicados/alertas/<id_alerta>/.
- Confirmación, descarte, petición principal inválida (400), alerta/petición inexistente (404) y conflictos (409).
- Compatibilidad con endpoints existentes y no intervención en /iniciar/.
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.db import OperationalError
from django.test import SimpleTestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIClient, force_authenticate

from peticiones.models import PeticionPosibleDuplicado
from peticiones.services.alertas_duplicados_service import (
    AlertaNoEncontradaError,
    ResultadoDecisionAlerta,
    SeleccionPrincipalInvalidaError,
    TransicionAlertaInvalidaError,
)


class _DummyUser:
    def __init__(self, id_usuario, rol_id, activo=True, nombre='Usuario', apellido='Prueba'):
        self.id_usuario = id_usuario
        self.pk = id_usuario
        self.id_rol_id = rol_id
        self.id_rol = MagicMock(id_rol=rol_id)
        self.activo = activo
        self.is_authenticated = True
        self.is_anonymous = False
        self.nombre = nombre
        self.apellido = apellido
        self.email = f'user{id_usuario}@cba.gov.co'


def _crear_mock_peticion(
    *,
    id_peticion,
    numero_radicado,
    descripcion,
    fecha,
    estado_id=1,
    estado_nombre='En revisión',
    tipo_id=1,
    tipo_nombre='Rescate',
    ubicacion=None,
    foto=None,
    evidencias=None,
    asignado_a=None,
    asignado_por=None,
    fecha_asignacion=None,
    prioridad='ALTA',
):
    obj = MagicMock()
    obj.id_peticion = id_peticion
    obj.pk = id_peticion
    obj.numero_radicado = numero_radicado
    obj.descripcion = descripcion
    obj.fecha = fecha
    obj.prioridad = prioridad
    obj.id_tipo_id = tipo_id
    obj.id_tipo = MagicMock(id_tipo=tipo_id, nombre=tipo_nombre)
    obj.id_estado_id = estado_id
    obj.id_estado = MagicMock(
        id_estado=estado_id,
        nombre=estado_nombre,
        descripcion=f'Estado {estado_nombre}',
        orden=estado_id,
    )
    obj.id_ubicacion = ubicacion
    obj.foto = foto
    obj.asignado_a_id = getattr(asignado_a, 'id_usuario', None) if asignado_a else None
    obj.asignado_a = asignado_a
    obj.asignado_por_id = getattr(asignado_por, 'id_usuario', None) if asignado_por else None
    obj.asignado_por = asignado_por
    obj.fecha_asignacion = fecha_asignacion

    ev_objs = []
    for idx, ev in enumerate(evidencias or [], start=1):
        if isinstance(ev, dict):
            ev_objs.append(
                MagicMock(
                    id_evidencia=ev.get('id_evidencia', idx),
                    ruta_archivo=ev.get('ruta_archivo'),
                    descripcion=ev.get('descripcion'),
                )
            )
        else:
            ev_objs.append(MagicMock(id_evidencia=idx, ruta_archivo=ev, descripcion=None))
    obj._prefetched_objects_cache = {'evidenciapeticiones_set': ev_objs}
    return obj


def _crear_mock_alerta(
    *,
    id_alerta=1,
    estado_revision='PENDIENTE',
    peticion_menor=None,
    peticion_mayor=None,
    peticion_origen_id=None,
    peticion_principal_id=None,
    revisado_por=None,
    observacion_revision=None,
    fecha_revision=None,
):
    if peticion_menor is None:
        peticion_menor = _crear_mock_peticion(
            id_peticion=10,
            numero_radicado='PET-2026-0010',
            descripcion='Canino herido en parque central',
            fecha=datetime(2026, 11, 12, 9, 0, 0),
            ubicacion=MagicMock(
                id_ubicacion=100,
                direccion='Parque Caldas #1-10',
                latitud=Decimal('2.4419000'),
                longitud=Decimal('-76.6063000'),
            ),
            foto='https://res.cloudinary.com/demo/image/upload/v1/p10.jpg',
            evidencias=[{'id_evidencia': 1, 'ruta_archivo': 'https://res.cloudinary.com/demo/image/upload/v1/p10_b.jpg', 'descripcion': 'Lesion pata'}],
        )
    if peticion_mayor is None:
        vet = _DummyUser(id_usuario=30, rol_id=3, nombre='Carlos', apellido='Vargas')
        vet.matricula_profesional = 'MP-555'
        jur = _DummyUser(id_usuario=2, rol_id=2, nombre='Laura', apellido='Juridica')
        peticion_mayor = _crear_mock_peticion(
            id_peticion=20,
            numero_radicado='PET-2026-0020',
            descripcion='Perro lesionado frente al parque central',
            fecha=datetime(2026, 11, 12, 11, 30, 0),
            estado_id=2,
            estado_nombre='Asignada',
            ubicacion=MagicMock(
                id_ubicacion=101,
                direccion='Cra 6 #4-20',
                latitud=Decimal('2.4420000'),
                longitud=Decimal('-76.6064000'),
            ),
            foto='https://res.cloudinary.com/demo/image/upload/v1/p20.jpg',
            asignado_a=vet,
            asignado_por=jur,
            fecha_asignacion=datetime(2026, 11, 12, 12, 0, 0),
        )

    alerta = MagicMock()
    alerta.id_alerta = id_alerta
    alerta.pk = id_alerta
    alerta.estado_revision = estado_revision
    alerta.peticion_menor_id = peticion_menor.id_peticion
    alerta.peticion_mayor_id = peticion_mayor.id_peticion
    alerta.peticion_menor = peticion_menor
    alerta.peticion_mayor = peticion_mayor
    alerta.peticion_origen_id = peticion_origen_id if peticion_origen_id is not None else peticion_mayor.id_peticion
    alerta.peticion_principal_id = peticion_principal_id
    alerta.similitud_texto = 0.87
    alerta.coincidencia_texto = True
    alerta.distancia_metros = 18.4
    alerta.coincidencia_gps = True
    alerta.distancia_hamming = 3
    alerta.similitud_imagen = 0.9531
    alerta.coincidencia_imagen = True
    alerta.score_combinado = 0.92
    alerta.criterios_coincidencia = ['TEXTO', 'GPS', 'IMAGEN']
    alerta.origen_deteccion = PeticionPosibleDuplicado.ORIGEN_ENDPOINT_MANUAL
    alerta.observacion_revision = observacion_revision
    alerta.revisado_por = revisado_por
    alerta.fecha_revision = fecha_revision
    alerta.fecha_creacion = datetime(2026, 11, 12, 11, 31, 0)
    alerta.fecha_actualizacion = datetime(2026, 11, 12, 11, 31, 0)
    return alerta


class AlertasDuplicadosRutasYPermisosTestCase(SimpleTestCase):
    """Valida rutas, 401/403 por rol y Swagger de los 4 endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.admin = _DummyUser(id_usuario=1, rol_id=1, activo=True, nombre='Admin', apellido='Uno')
        self.juridico = _DummyUser(id_usuario=2, rol_id=2, activo=True, nombre='Juridico', apellido='Uno')

    def test_resolucion_de_urls_alertas_y_existentes(self):
        casos = [
            ('/api/peticiones/duplicados/alertas/', 'alertas-duplicados-listar'),
            ('/api/peticiones/duplicados/alertas/15/', 'alerta-duplicado-detalle'),
            ('/api/peticiones/duplicados/alertas/15/resolver/', 'alerta-duplicado-resolver'),
            ('/api/peticiones/duplicados/alertas/15/comparar-visual/', 'alerta-duplicado-comparar-visual'),
            ('/api/peticiones/25/duplicados/', 'peticion-alertas-duplicados'),
            ('/api/peticiones/25/', 'detalle-peticion'),
            ('/api/peticiones/detectar-duplicados/', 'detectar-duplicados-peticion'),
        ]
        for ruta, nombre in casos:
            with self.subTest(ruta=ruta):
                match = resolve(ruta)
                self.assertEqual(match.url_name, nombre)

        self.assertEqual(reverse('alertas-duplicados-listar'), '/api/peticiones/duplicados/alertas/')
        self.assertEqual(reverse('alerta-duplicado-detalle', args=[15]), '/api/peticiones/duplicados/alertas/15/')
        self.assertEqual(reverse('alerta-duplicado-resolver', args=[15]), '/api/peticiones/duplicados/alertas/15/resolver/')
        self.assertEqual(reverse('alerta-duplicado-comparar-visual', args=[15]), '/api/peticiones/duplicados/alertas/15/comparar-visual/')
        self.assertEqual(reverse('peticion-alertas-duplicados', args=[25]), '/api/peticiones/25/duplicados/')

    def test_endpoints_retornan_401_sin_autenticacion(self):
        rutas = [
            ('get', '/api/peticiones/duplicados/alertas/', None),
            ('get', '/api/peticiones/duplicados/alertas/1/', None),
            ('post', '/api/peticiones/duplicados/alertas/1/resolver/', {'estado_revision': 'DESCARTADO'}),
            ('post', '/api/peticiones/duplicados/alertas/1/comparar-visual/', {}),
            ('get', '/api/peticiones/10/duplicados/', None),
        ]
        for metodo, url, body in rutas:
            with self.subTest(metodo=metodo, url=url):
                resp = getattr(self.client, metodo)(url, body, format='json')
                self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_endpoints_retornan_403_para_roles_no_autorizados_e_inactivos(self):
        usuarios_denegados = [
            _DummyUser(id_usuario=13, rol_id=3, activo=True),
            _DummyUser(id_usuario=14, rol_id=4, activo=True),
            _DummyUser(id_usuario=15, rol_id=5, activo=True),
            _DummyUser(id_usuario=91, rol_id=1, activo=False),
            _DummyUser(id_usuario=92, rol_id=2, activo=False),
        ]
        rutas = [
            ('get', '/api/peticiones/duplicados/alertas/', None),
            ('get', '/api/peticiones/duplicados/alertas/1/', None),
            ('post', '/api/peticiones/duplicados/alertas/1/resolver/', {'estado_revision': 'DESCARTADO'}),
            ('post', '/api/peticiones/duplicados/alertas/1/comparar-visual/', {}),
            ('get', '/api/peticiones/10/duplicados/', None),
        ]
        for user in usuarios_denegados:
            self.client.force_authenticate(user=user)
            for metodo, url, body in rutas:
                with self.subTest(rol=user.id_rol_id, activo=user.activo, url=url):
                    resp = getattr(self.client, metodo)(url, body, format='json')
                    self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @patch('peticiones.api.views.obtener_resumen_alertas_duplicados', return_value={'PENDIENTE': 1, 'CONFIRMADO': 0, 'DESCARTADO': 0, 'total': 1})
    @patch('peticiones.api.views.listar_alertas_duplicados')
    def test_admin_y_juridico_pueden_listar_alertas_200(self, mock_listar, _mock_resumen):
        mock_listar.return_value = [_crear_mock_alerta()]
        for user in (self.admin, self.juridico):
            with self.subTest(rol=user.id_rol_id):
                self.client.force_authenticate(user=user)
                resp = self.client.get('/api/peticiones/duplicados/alertas/')
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
                self.assertEqual(resp.data['count'], 1)

    def test_swagger_incluye_todos_los_endpoints_de_alertas(self):
        resp = self.client.get('/swagger/?format=openapi')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        paths = resp.json().get('paths', {})
        self.assertIn('/peticiones/duplicados/alertas/', paths)
        self.assertIn('get', paths['/peticiones/duplicados/alertas/'])
        self.assertIn('/peticiones/duplicados/alertas/{id_alerta}/', paths)
        self.assertIn('get', paths['/peticiones/duplicados/alertas/{id_alerta}/'])
        self.assertIn('/peticiones/duplicados/alertas/{id_alerta}/resolver/', paths)
        self.assertIn('post', paths['/peticiones/duplicados/alertas/{id_alerta}/resolver/'])
        self.assertIn('/peticiones/{id_peticion}/duplicados/', paths)
        self.assertIn('get', paths['/peticiones/{id_peticion}/duplicados/'])


class AlertasDuplicadosEndpointsComportamientoTestCase(SimpleTestCase):
    """Valida filtros, paginación, comparación detallada, resolución y errores."""

    def setUp(self):
        self.client = APIClient()
        self.juridico = _DummyUser(id_usuario=2, rol_id=2, activo=True, nombre='Laura', apellido='Juridico')
        self.client.force_authenticate(user=self.juridico)

    @patch('peticiones.api.views.obtener_resumen_alertas_duplicados')
    @patch('peticiones.api.views.listar_alertas_duplicados')
    def test_listar_con_filtros_y_paginacion(self, mock_listar, mock_resumen):
        alertas = [_crear_mock_alerta(id_alerta=idx) for idx in range(1, 6)]
        mock_listar.return_value = alertas
        mock_resumen.return_value = {'PENDIENTE': 5, 'CONFIRMADO': 0, 'DESCARTADO': 0, 'total': 5}

        resp = self.client.get(
            '/api/peticiones/duplicados/alertas/?estado=pendiente&id_peticion=10&page=2&page_size=2'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_listar.assert_called_once_with(
            estado_revision='PENDIENTE',
            id_peticion=10,
            solo_pendientes=False,
        )
        mock_resumen.assert_called_once_with(id_peticion=10)
        self.assertEqual(resp.data['count'], 5)
        self.assertEqual(resp.data['page'], 2)
        self.assertEqual(resp.data['page_size'], 2)
        self.assertEqual(resp.data['total_pages'], 3)
        self.assertEqual(len(resp.data['results']), 2)
        self.assertEqual([item['id_alerta'] for item in resp.data['results']], [3, 4])
        self.assertEqual(resp.data['resumen']['total'], 5)

    def test_listar_con_filtros_o_paginacion_invalidos_retorna_400(self):
        urls_invalidas = [
            '/api/peticiones/duplicados/alertas/?estado=INVALIDO',
            '/api/peticiones/duplicados/alertas/?estado=PENDIENTE&estado_revision=CONFIRMADO',
            '/api/peticiones/duplicados/alertas/?id_peticion=abc',
            '/api/peticiones/duplicados/alertas/?id_peticion=0',
            '/api/peticiones/duplicados/alertas/?id_peticion=10&peticion=20',
            '/api/peticiones/duplicados/alertas/?solo_pendientes=quizas',
            '/api/peticiones/duplicados/alertas/?page=0',
            '/api/peticiones/duplicados/alertas/?page=abc',
            '/api/peticiones/duplicados/alertas/?page_size=0',
            '/api/peticiones/duplicados/alertas/?page_size=xyz',
        ]
        for url in urls_invalidas:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('peticiones.api.views.obtener_alerta_por_id')
    def test_detalle_alerta_incluye_comparacion_completa_y_404_si_no_existe(self, mock_obtener):
        alerta = _crear_mock_alerta(id_alerta=77)
        mock_obtener.return_value = alerta

        resp = self.client.get('/api/peticiones/duplicados/alertas/77/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertEqual(data['id_alerta'], 77)
        self.assertIn('comparacion', data)
        self.assertEqual(data['comparacion']['diferencia_tiempo_horas'], 2.5)
        self.assertFalse(data['comparacion']['mismo_estado'])
        self.assertFalse(data['comparacion']['ambas_asignadas'])

        menor = data['peticion_menor']
        mayor = data['peticion_mayor']
        self.assertEqual(menor['descripcion'], 'Canino herido en parque central')
        self.assertEqual(len(menor['fotos']), 2)
        self.assertEqual(menor['ubicacion']['direccion'], 'Parque Caldas #1-10')
        self.assertEqual(menor['estado']['nombre'], 'En revisión')
        self.assertFalse(menor['asignacion']['asignada'])

        self.assertTrue(mayor['asignacion']['asignada'])
        self.assertEqual(mayor['asignacion']['asignado_a']['id_usuario'], 30)
        self.assertEqual(mayor['asignacion']['asignado_por']['id_usuario'], 2)
        self.assertEqual(mayor['estado']['nombre'], 'Asignada')

        mock_obtener.side_effect = AlertaNoEncontradaError('No existe')
        resp_404 = self.client.get('/api/peticiones/duplicados/alertas/99999/')
        self.assertEqual(resp_404.status_code, status.HTTP_404_NOT_FOUND)

    @patch('peticiones.api.views.confirmar_duplicado')
    def test_resolver_confirmado_valido_retorna_200(self, mock_confirmar):
        alerta_conf = _crear_mock_alerta(
            id_alerta=5,
            estado_revision='CONFIRMADO',
            peticion_principal_id=10,
            revisado_por=self.juridico,
            observacion_revision='Confirmado mismo caso',
            fecha_revision=datetime(2026, 11, 12, 14, 0, 0),
        )
        mock_confirmar.return_value = ResultadoDecisionAlerta(
            alerta=alerta_conf,
            cambiada=True,
            estado_anterior='PENDIENTE',
            estado_nuevo='CONFIRMADO',
            historial_ids=[101, 102],
        )

        resp = self.client.post(
            '/api/peticiones/duplicados/alertas/5/resolver/',
            {
                'estado_revision': 'confirmado',
                'peticion_principal': 10,
                'observacion': '  Confirmado mismo caso  ',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['cambiada'])
        self.assertEqual(resp.data['estado_nuevo'], 'CONFIRMADO')
        self.assertEqual(resp.data['alerta']['peticion_principal_id'], 10)
        mock_confirmar.assert_called_once_with(
            5,
            10,
            usuario_revisor=self.juridico,
            observacion='Confirmado mismo caso',
            permitir_cambio_decision_previa=False,
            registrar_historial=True,
        )

    @patch('peticiones.api.views.descartar_duplicado')
    def test_resolver_descartado_valido_retorna_200(self, mock_descartar):
        alerta_desc = _crear_mock_alerta(
            id_alerta=6,
            estado_revision='DESCARTADO',
            revisado_por=self.juridico,
            observacion_revision='Son dos animales distintos',
            fecha_revision=datetime(2026, 11, 12, 14, 5, 0),
        )
        mock_descartar.return_value = ResultadoDecisionAlerta(
            alerta=alerta_desc,
            cambiada=True,
            estado_anterior='PENDIENTE',
            estado_nuevo='DESCARTADO',
            historial_ids=[103, 104],
        )

        resp = self.client.post(
            '/api/peticiones/duplicados/alertas/6/resolver/',
            {'estado_revision': 'DESCARTADO', 'observacion': 'Son dos animales distintos'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['cambiada'])
        self.assertEqual(resp.data['estado_nuevo'], 'DESCARTADO')
        mock_descartar.assert_called_once()

    @patch('peticiones.api.views.confirmar_duplicado')
    def test_resolver_errores_400_404_y_409(self, mock_confirmar):
        # 400 validación entrada
        payloads_400 = [
            {},
            {'estado_revision': 'PENDIENTE'},
            {'estado_revision': 'OTRO'},
            {'estado_revision': 'CONFIRMADO'},
            {'estado_revision': 'CONFIRMADO', 'peticion_principal': 0},
            {'estado_revision': 'DESCARTADO', 'peticion_principal': 10},
        ]
        for body in payloads_400:
            with self.subTest(body=body):
                resp = self.client.post('/api/peticiones/duplicados/alertas/5/resolver/', body, format='json')
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # 400 petición principal fuera del par (servicio)
        mock_confirmar.side_effect = SeleccionPrincipalInvalidaError('Principal no pertenece al par')
        resp_princ = self.client.post(
            '/api/peticiones/duplicados/alertas/5/resolver/',
            {'estado_revision': 'CONFIRMADO', 'peticion_principal': 999},
            format='json',
        )
        self.assertEqual(resp_princ.status_code, status.HTTP_400_BAD_REQUEST)

        # 404 alerta inexistente
        mock_confirmar.side_effect = AlertaNoEncontradaError('No existe')
        resp_404 = self.client.post(
            '/api/peticiones/duplicados/alertas/999/resolver/',
            {'estado_revision': 'CONFIRMADO', 'peticion_principal': 10},
            format='json',
        )
        self.assertEqual(resp_404.status_code, status.HTTP_404_NOT_FOUND)

        # 409 transición inválida / ya resuelta con otra decisión
        mock_confirmar.side_effect = TransicionAlertaInvalidaError('Alerta ya descartada')
        resp_409 = self.client.post(
            '/api/peticiones/duplicados/alertas/5/resolver/',
            {'estado_revision': 'CONFIRMADO', 'peticion_principal': 10},
            format='json',
        )
        self.assertEqual(resp_409.status_code, status.HTTP_409_CONFLICT)

        # 409 conflicto de concurrencia BD
        mock_confirmar.side_effect = OperationalError('could not serialize access')
        resp_op = self.client.post(
            '/api/peticiones/duplicados/alertas/5/resolver/',
            {'estado_revision': 'CONFIRMADO', 'peticion_principal': 10},
            format='json',
        )
        self.assertEqual(resp_op.status_code, status.HTTP_409_CONFLICT)

    @patch('peticiones.api.views.obtener_resumen_alertas_duplicados', return_value={'PENDIENTE': 1, 'CONFIRMADO': 0, 'DESCARTADO': 0, 'total': 1})
    @patch('peticiones.api.views.listar_alertas_duplicados')
    @patch('peticiones.api.views.Peticiones.objects')
    def test_alertas_por_peticion_200_y_404(self, mock_pet_obj, mock_listar, mock_resumen):
        mock_pet_obj.filter.return_value.values.return_value.first.return_value = {
            'id_peticion': 10,
            'numero_radicado': 'PET-2026-0010',
        }
        mock_listar.return_value = [_crear_mock_alerta(id_alerta=1)]

        resp = self.client.get('/api/peticiones/10/duplicados/?estado=PENDIENTE')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['id_peticion'], 10)
        self.assertEqual(resp.data['numero_radicado'], 'PET-2026-0010')
        self.assertEqual(resp.data['count'], 1)
        mock_listar.assert_called_once_with(
            estado_revision='PENDIENTE',
            id_peticion=10,
            solo_pendientes=False,
        )

        # 404 cuando la petición no existe
        mock_pet_obj.filter.return_value.values.return_value.first.return_value = None
        resp_404 = self.client.get('/api/peticiones/99999/duplicados/')
        self.assertEqual(resp_404.status_code, status.HTTP_404_NOT_FOUND)

    @patch('peticiones.api.views.obtener_alerta_por_id')
    def test_metricas_separadas_gps_dinov2_y_no_calculada_en_detalle(self, mock_obtener):
        # Caso 1: Alerta con DINOv2 real + distancia GPS 50m / radio 200m (= 75% proximidad)
        alerta_dinov2 = _crear_mock_alerta(id_alerta=81)
        alerta_dinov2.distancia_metros = 50.0
        alerta_dinov2.radio_gps_metros = 200.0
        alerta_dinov2.porcentaje_proximidad_gps = None
        alerta_dinov2.distancia_hamming = None
        alerta_dinov2.metodo_imagen = 'dinov2_small'
        alerta_dinov2.similitud_imagen = 0.885
        alerta_dinov2.similitud_visual_coseno = 0.885
        alerta_dinov2.porcentaje_similitud_visual = 88.5
        mock_obtener.return_value = alerta_dinov2

        resp1 = self.client.get('/api/peticiones/duplicados/alertas/81/')
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        m1 = resp1.data['comparacion']['metricas']
        self.assertEqual(m1['distancia_metros'], 50.0)
        self.assertEqual(m1['radio_gps_metros'], 200.0)
        self.assertEqual(m1['porcentaje_proximidad_gps'], 75.0)
        self.assertTrue(m1['dinov2_calculada'])
        self.assertEqual(m1['porcentaje_similitud_visual'], 88.5)
        self.assertEqual(m1['estado_comparacion_visual'], 'Calculada (DINOv2)')
        self.assertEqual(m1['porcentaje_similitud_texto'], 87.0)
        self.assertEqual(m1['porcentaje_score_combinado'], 92.0)

        # Caso 2: Alerta sin comparación visual DINOv2 (o solo dHash) -> No calculada, nunca inventada
        alerta_sin_visual = _crear_mock_alerta(id_alerta=82)
        alerta_sin_visual.similitud_imagen = None
        alerta_sin_visual.distancia_hamming = None
        alerta_sin_visual.metodo_imagen = None
        alerta_sin_visual.similitud_visual_coseno = None
        alerta_sin_visual.porcentaje_similitud_visual = None
        alerta_sin_visual.coincidencia_imagen = False
        mock_obtener.return_value = alerta_sin_visual

        resp2 = self.client.get('/api/peticiones/duplicados/alertas/82/')
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        m2 = resp2.data['comparacion']['metricas']
        self.assertFalse(m2['dinov2_calculada'])
        self.assertIsNone(m2['porcentaje_similitud_visual'])
        self.assertEqual(m2['estado_comparacion_visual'], 'No calculada')

    @patch('peticiones.api.views.calcular_comparacion_visual_dinov2_alerta')
    def test_endpoint_comparar_visual_dinov2_200_422_404_503_json_y_contrato(self, mock_comp):
        from peticiones.services.alertas_duplicados_service import Dinov2NoDisponibleParaAlertaError

        alerta_pendiente = _crear_mock_alerta(id_alerta=90)
        alerta_pendiente.distancia_metros = 20.0
        alerta_pendiente.distancia_hamming = None
        alerta_pendiente.metodo_imagen = None
        alerta_pendiente.similitud_imagen = None
        alerta_pendiente.porcentaje_similitud_visual = None

        mock_comp.return_value = {
            'id_alerta': 90,
            'alerta': alerta_pendiente,
            'comparado': True,
            'dinov2_calculada': True,
            'visual_calculada': True,
            'codigo': 'OK',
            'motivo': None,
            'mensaje': 'Similitud visual DINOv2 calculada correctamente.',
            'similitud_imagen_porcentaje': 91.0,
            'porcentaje_similitud_visual': 91.0,
            'similitud_imagen': 0.91,
            'similitud_visual_coseno': 0.91,
            'coincidencia_imagen': True,
            'metodo_imagen': 'dinov2_small',
            'distancia_gps_metros': 20.0,
            'radio_gps_metros': 200.0,
            'porcentaje_proximidad_gps': 90.0,
            'estado_revision': 'PENDIENTE',
            'alerta_modificada': False,
        }
        resp = self.client.post(
            '/api/peticiones/duplicados/alertas/90/comparar-visual/',
            {},
            format='json',
            HTTP_ACCEPT='text/html',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('application/json', resp['Content-Type'])
        self.assertTrue(resp.data['comparado'])
        self.assertEqual(resp.data['similitud_imagen_porcentaje'], 91.0)
        self.assertEqual(resp.data['metodo_imagen'], 'dinov2_small')
        self.assertEqual(resp.data['distancia_gps_metros'], 20.0)
        self.assertFalse(resp.data['alerta_modificada'])
        self.assertEqual(resp.data['alerta']['estado_revision'], 'PENDIENTE')

        mock_comp.return_value = {
            'id_alerta': 90,
            'alerta': alerta_pendiente,
            'comparado': False,
            'codigo': 'SIN_FOTOGRAFIAS',
            'motivo': 'SIN_FOTOGRAFIAS_EN_AMBAS_PETICIONES',
            'mensaje': 'Ambas peticiones deben contar con al menos una fotografía válida para calcular DINOv2.',
            'distancia_gps_metros': 20.0,
            'radio_gps_metros': 200.0,
            'porcentaje_proximidad_gps': 90.0,
            'peticiones_sin_fotos': [10],
            'alerta_modificada': False,
        }
        resp_422 = self.client.post(
            '/api/peticiones/duplicados/alertas/90/comparar-visual/',
            {},
            format='json',
            HTTP_ACCEPT='text/html',
        )
        self.assertEqual(resp_422.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('application/json', resp_422['Content-Type'])
        self.assertFalse(resp_422.data['comparado'])
        self.assertEqual(resp_422.data['codigo'], 'SIN_FOTOGRAFIAS')
        self.assertIsNone(resp_422.data['similitud_imagen_porcentaje'])
        self.assertIsNone(resp_422.data['metodo_imagen'])
        self.assertEqual(resp_422.data['distancia_gps_metros'], 20.0)
        self.assertIn('fotografía válida', resp_422.data['error'])

        mock_comp.side_effect = Dinov2NoDisponibleParaAlertaError(
            'VetericanoIA offline',
            distancia_gps_metros=20.0,
        )
        resp_503 = self.client.post(
            '/api/peticiones/duplicados/alertas/90/comparar-visual/',
            {},
            format='json',
            HTTP_ACCEPT='text/html',
        )
        self.assertEqual(resp_503.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn('application/json', resp_503['Content-Type'])
        self.assertFalse(resp_503.data['comparado'])
        self.assertEqual(resp_503.data['codigo'], 'DINOV2_NO_DISPONIBLE')
        self.assertEqual(resp_503.data['distancia_gps_metros'], 20.0)
        self.assertIn('VetericanoIA offline', resp_503.data['error'])

        mock_comp.side_effect = AlertaNoEncontradaError('No existe')
        resp_404 = self.client.post(
            '/api/peticiones/duplicados/alertas/90/comparar-visual/',
            {},
            format='json',
            HTTP_ACCEPT='text/html',
        )
        self.assertEqual(resp_404.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('application/json', resp_404['Content-Type'])
        self.assertFalse(resp_404.data['comparado'])
