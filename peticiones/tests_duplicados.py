"""
Pruebas de la Fase 1 de detección de posibles reportes duplicados (pg_trgm / similarity).

Cubre:
- Autenticación y permisos por rol (401 sin token; 403 Veterinario/Peticionario/SuperAdmin/Inactivo; 200 Admin/Jurídico).
- Validación de payload y parámetros configurables.
- Descripciones vacías, espacios o demasiado cortas (ignoradas sin error).
- Comparación con similitud alta y ordenamiento descendente (incluyendo fotografía y estado).
- Descripciones diferentes sin coincidencias por encima del umbral.
- Exclusión de la petición actual (`id_peticion` / `excluir_id_peticion`).
- Continuidad y compatibilidad de endpoints existentes y esquema Swagger.
"""

import io
import socket
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests
from django.contrib.postgres.search import TrigramSimilarity
from django.test import SimpleTestCase, override_settings
from django.urls import resolve
from PIL import Image, ImageDraw, ImageEnhance
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from peticiones.api.views import (
    DashboardJuridicoView,
    DetectarDuplicadosPeticionView,
    IniciarPeticionView,
    ListarEstadosPeticionView,
    ListarPeticionesView,
    ListarTiposPeticionView,
)
from peticiones.services.duplicados_service import (
    buscar_peticiones_similares_por_descripcion,
    calcular_bounding_box,
    calcular_distancia_haversine_metros,
    normalizar_coordenadas,
    obtener_config_duplicados,
)
from peticiones.services.imagen_hash_service import (
    _validar_destino_dns,
    calcular_dhash_desde_bytes,
    calcular_distancia_hamming,
    comparar_conjuntos_hashes,
    construir_url_miniatura_cloudinary,
    descargar_imagen_segura,
    validar_url_cloudinary_segura,
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
    estado_nombre,
    id_estado=1,
    foto=None,
    evidencias=None,
    similitud=0.8,
    latitud=None,
    longitud=None,
    direccion=None,
):
    obj = MagicMock()
    obj.id_peticion = id_peticion
    obj.numero_radicado = numero_radicado
    obj.descripcion = descripcion
    obj.fecha = fecha
    obj.id_estado_id = id_estado
    obj.id_estado = MagicMock(id_estado=id_estado, nombre=estado_nombre)
    if latitud is not None or longitud is not None or direccion is not None:
        obj.id_ubicacion = MagicMock(
            direccion=direccion,
            latitud=latitud,
            longitud=longitud,
        )
    else:
        obj.id_ubicacion = None
    obj.foto = foto
    obj.similitud = similitud
    ev_objs = [MagicMock(ruta_archivo=ruta) for ruta in (evidencias or [])]
    obj._prefetched_objects_cache = {'evidenciapeticiones_set': ev_objs}
    return obj


class DetectarDuplicadosPermisosTestCase(SimpleTestCase):
    """Valida que el endpoint esté protegido por autenticación JWT y rol Jurídico/Admin."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosPeticionView.as_view()
        self.payload = {'descripcion': 'Perro herido atropellado frente al parque principal'}

    def test_acceso_sin_autenticacion_retorna_401(self):
        request = self.factory.post('/api/peticiones/detectar-duplicados/', self.payload, format='json')
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_acceso_denegado_usuario_inactivo_403(self):
        request = self.factory.post('/api/peticiones/detectar-duplicados/', self.payload, format='json')
        force_authenticate(request, user=_DummyUser(id_usuario=99, rol_id=2, activo=False))
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_acceso_denegado_veterinario_y_peticionario_y_superadmin_403(self):
        for rol_id in (3, 4, 5):
            with self.subTest(rol_id=rol_id):
                request = self.factory.post('/api/peticiones/detectar-duplicados/', self.payload, format='json')
                force_authenticate(request, user=_DummyUser(id_usuario=10 + rol_id, rol_id=rol_id, activo=True))
                response = self.view(request)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('peticiones.api.views.detectar_duplicados_desde_solicitud')
    def test_acceso_permitido_admin_y_juridico_200(self, mock_servicio):
        mock_servicio.return_value = {
            'advertencia_duplicado': False,
            'total_candidatos': 0,
            'posibles_duplicados': [],
            'mensaje': 'Sin candidatos',
            'parametros': {},
        }
        for rol_id in (1, 2):
            with self.subTest(rol_id=rol_id):
                request = self.factory.post('/api/peticiones/detectar-duplicados/', self.payload, format='json')
                force_authenticate(request, user=_DummyUser(id_usuario=rol_id, rol_id=rol_id, activo=True))
                response = self.view(request)
                self.assertEqual(response.status_code, status.HTTP_200_OK)


class DetectarDuplicadosValidacionTestCase(SimpleTestCase):
    """Valida reglas de entrada del endpoint POST /api/peticiones/detectar-duplicados/."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosPeticionView.as_view()
        self.juridico = _DummyUser(id_usuario=2, rol_id=2, activo=True)

    def test_payload_vacio_sin_descripcion_ni_id_retorna_400(self):
        request = self.factory.post('/api/peticiones/detectar-duplicados/', {}, format='json')
        force_authenticate(request, user=self.juridico)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_umbral_fuera_de_rango_retorna_400(self):
        for umbral_invalido in (0.0, -0.2, 1.5):
            with self.subTest(umbral=umbral_invalido):
                request = self.factory.post(
                    '/api/peticiones/detectar-duplicados/',
                    {'descripcion': 'Perro herido en avenida central', 'umbral': umbral_invalido},
                    format='json',
                )
                force_authenticate(request, user=self.juridico)
                response = self.view(request)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_id_peticion_inexistente_sin_descripcion_retorna_404(self, mock_pet_objects):
        mock_pet_objects.filter.return_value.values.return_value.first.return_value = None
        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {'id_peticion': 99999},
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(
        DUPLICADOS_UMBRAL_DESCRIPCION=0.55,
        DUPLICADOS_DIAS_VENTANA=15,
        DUPLICADOS_MIN_LONGITUD_DESCRIPCION=15,
        DUPLICADOS_MAX_CANDIDATOS=5,
    )
    def test_configuracion_sobreescribible_desde_settings(self):
        cfg = obtener_config_duplicados()
        self.assertEqual(cfg['umbral'], 0.55)
        self.assertEqual(cfg['dias'], 15)
        self.assertEqual(cfg['min_longitud'], 15)
        self.assertEqual(cfg['limite'], 5)


class DetectarDuplicadosFase1TestCase(SimpleTestCase):
    """Pruebas funcionales de la Fase 1 (pg_trgm similarity, ventana temporal, exclusión y orden)."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosPeticionView.as_view()
        self.juridico = _DummyUser(id_usuario=2, rol_id=2, activo=True)

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_descripcion_vacia_o_corta_es_ignorada_sin_consultar_bd(self, mock_pet_objects):
        for entrada in ('', '   \n\t  ', None, 'perro'):
            with self.subTest(entrada=entrada):
                request = self.factory.post(
                    '/api/peticiones/detectar-duplicados/',
                    {'descripcion': entrada},
                    format='json',
                )
                force_authenticate(request, user=self.juridico)
                response = self.view(request)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertFalse(response.data['advertencia_duplicado'])
                self.assertEqual(response.data['total_candidatos'], 0)
                self.assertEqual(response.data['posibles_duplicados'], [])
                mock_pet_objects.select_related.assert_not_called()

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_dos_descripciones_muy_similares_ordenadas_descendente(self, mock_pet_objects):
        cand_alta = _crear_mock_peticion(
            id_peticion=31,
            numero_radicado='PET-2026-00031',
            descripcion='Perro herido atropellado cerca al parque Caldas',
            fecha=datetime(2026, 11, 10, 14, 30, 0),
            estado_nombre='En revisión',
            id_estado=1,
            foto='https://res.cloudinary.com/test/image/upload/perro1.jpg',
            evidencias=['https://res.cloudinary.com/test/image/upload/perro1.jpg'],
            similitud=0.8421,
        )
        cand_media = _crear_mock_peticion(
            id_peticion=28,
            numero_radicado='PET-2026-00028',
            descripcion='Perro lesionado por accidente en el parque Caldas',
            fecha=datetime(2026, 11, 9, 9, 15, 0),
            estado_nombre='Asignada',
            id_estado=2,
            foto=None,
            evidencias=['https://res.cloudinary.com/test/image/upload/evidencia28.jpg'],
            similitud=0.4712,
        )

        # Cadena ORM hasta .order_by(...)[:limite]
        qs_base = mock_pet_objects.select_related.return_value.prefetch_related.return_value
        qs_ventana = qs_base.filter.return_value
        qs_no_null = qs_ventana.exclude.return_value
        qs_len = qs_no_null.annotate.return_value.filter.return_value
        qs_sim = qs_len.annotate.return_value.filter.return_value
        qs_ordenado = qs_sim.order_by.return_value
        qs_ordenado.__getitem__.return_value = [cand_alta, cand_media]

        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {'descripcion': '  Perro   herido atropellado frente al parque Caldas  '},
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['advertencia_duplicado'])
        self.assertEqual(response.data['total_candidatos'], 2)

        candidatos = response.data['posibles_duplicados']
        self.assertEqual([c['id_peticion'] for c in candidatos], [31, 28])
        self.assertGreater(candidatos[0]['porcentaje_similitud'], candidatos[1]['porcentaje_similitud'])

        # Campos obligatorios del candidato (Fase 1 + Fase 2)
        primero = candidatos[0]
        self.assertEqual(primero['numero_radicado'], 'PET-2026-00031')
        self.assertEqual(primero['descripcion'], 'Perro herido atropellado cerca al parque Caldas')
        self.assertEqual(primero['fecha'], '2026-11-10T14:30:00')
        self.assertEqual(primero['similitud'], 0.8421)
        self.assertEqual(primero['similitud_texto'], 0.8421)
        self.assertEqual(primero['porcentaje_similitud'], 84.21)
        self.assertIsNone(primero['distancia_metros'])
        self.assertFalse(primero['coincidencia_gps'])
        self.assertTrue(primero['posible_duplicado'])
        self.assertEqual(primero['estado'], 'En revisión')
        self.assertEqual(primero['foto'], 'https://res.cloudinary.com/test/image/upload/perro1.jpg')
        self.assertTrue(primero['tiene_fotografia'])

        # Verifica uso de fallback cuando Peticiones.foto es None pero existe EvidenciaPeticiones
        segundo = candidatos[1]
        self.assertEqual(segundo['foto'], 'https://res.cloudinary.com/test/image/upload/evidencia28.jpg')
        self.assertTrue(segundo['tiene_fotografia'])

        # Verifica parámetros clave enviados al ORM (TrigramSimilarity + umbral 0.35 + orden descendente)
        mock_pet_objects.select_related.assert_called_once_with('id_estado', 'id_ubicacion')
        qs_base.filter.assert_called_once()
        _, sim_kwargs = qs_len.annotate.call_args
        self.assertIsInstance(sim_kwargs['similitud'], TrigramSimilarity)
        qs_len.annotate.return_value.filter.assert_called_once_with(similitud__gte=0.35)
        qs_sim.order_by.assert_called_once_with('-similitud', '-fecha', '-id_peticion')

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_dos_descripciones_diferentes_retornan_lista_vacia(self, mock_pet_objects):
        qs_len = (
            mock_pet_objects.select_related.return_value
            .prefetch_related.return_value
            .filter.return_value
            .exclude.return_value
            .annotate.return_value
            .filter.return_value
        )
        qs_len.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = []

        resultado = buscar_peticiones_similares_por_descripcion(
            'Reporte de alumbrado publico dañado en avenida sur'
        )
        self.assertFalse(resultado['advertencia_duplicado'])
        self.assertEqual(resultado['total_candidatos'], 0)
        self.assertEqual(resultado['posibles_duplicados'], [])

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_exclusion_peticion_actual_con_descripcion_explicita(self, mock_pet_objects):
        qs_len = (
            mock_pet_objects.select_related.return_value
            .prefetch_related.return_value
            .filter.return_value
            .exclude.return_value
            .annotate.return_value
            .filter.return_value
        )
        qs_excl = qs_len.exclude.return_value
        qs_excl.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = []

        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {
                'descripcion': 'Gato abandonado en caja de carton en el barrio centro',
                'id_peticion': 31,
            },
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        qs_len.exclude.assert_called_once_with(id_peticion=31)
        self.assertEqual(response.data['parametros']['id_peticion_excluido'], 31)

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_exclusion_peticion_actual_consultando_solo_por_id_peticion(self, mock_pet_objects):
        mock_pet_objects.filter.return_value.values.return_value.first.return_value = {
            'id_peticion': 31,
            'descripcion': 'Canino herido en via publica sector norte',
        }
        cand_otro = _crear_mock_peticion(
            id_peticion=29,
            numero_radicado='PET-2026-00029',
            descripcion='Perro herido en via publica del sector norte',
            fecha=datetime(2026, 11, 8, 11, 0, 0),
            estado_nombre='En Proceso',
            similitud=0.63,
        )
        qs_len = (
            mock_pet_objects.select_related.return_value
            .prefetch_related.return_value
            .filter.return_value
            .exclude.return_value
            .annotate.return_value
            .filter.return_value
        )
        qs_excl = qs_len.exclude.return_value
        qs_excl.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = [cand_otro]

        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {'id_peticion': 31},
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        qs_len.exclude.assert_called_once_with(id_peticion=31)
        self.assertEqual(response.data['total_candidatos'], 1)
        self.assertEqual(response.data['posibles_duplicados'][0]['id_peticion'], 29)


class EndpointsExistentesCompatibilidadTestCase(SimpleTestCase):
    """Verifica que las rutas, Swagger y endpoints existentes continúan operando sin alteraciones."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.client = APIClient()
        self.admin = _DummyUser(id_usuario=1, rol_id=1, activo=True)
        self.peticionario = _DummyUser(id_usuario=40, rol_id=4, activo=True)

    def test_resolucion_urls_nuevas_y_existentes(self):
        rutas = [
            ('/api/peticiones/detectar-duplicados/', 'detectar-duplicados-peticion'),
            ('/api/peticiones/tipos/', 'tipos-peticion'),
            ('/api/peticiones/listar/', 'listar-peticiones'),
            ('/api/peticiones/iniciar/', 'iniciar-peticion'),
            ('/api/peticiones/estados/', 'estados-peticion'),
            ('/api/peticiones/dashboard-juridico/', 'dashboard-juridico'),
            ('/api/peticiones/1/', 'detalle-peticion'),
        ]
        for path_url, nombre_esperado in rutas:
            with self.subTest(path=path_url):
                match = resolve(path_url)
                self.assertEqual(match.url_name, nombre_esperado)

    @patch.object(ListarPeticionesView, 'get_queryset', return_value=[])
    @patch.object(ListarTiposPeticionView, 'get_queryset', return_value=[])
    @patch('peticiones.api.views.EstadoPeticiones.objects')
    @patch('peticiones.api.views.Peticiones.objects')
    def test_endpoints_existentes_mantienen_funcionamiento_y_estructura(
        self, mock_pet_obj, mock_estado_obj, _mock_tipos_qs, _mock_listar_qs
    ):
        # 1. GET /api/peticiones/tipos/
        req_tipos = self.factory.get('/api/peticiones/tipos/')
        force_authenticate(req_tipos, user=self.peticionario)
        resp_tipos = ListarTiposPeticionView.as_view()(req_tipos)
        self.assertEqual(resp_tipos.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_tipos.data, [])

        # 2. GET /api/peticiones/estados/
        mock_estado_obj.filter.return_value.order_by.return_value = []
        req_est = self.factory.get('/api/peticiones/estados/')
        force_authenticate(req_est, user=self.admin)
        resp_est = ListarEstadosPeticionView.as_view()(req_est)
        self.assertEqual(resp_est.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_est.data, [])

        # 3. GET /api/peticiones/listar/
        req_list = self.factory.get('/api/peticiones/listar/')
        force_authenticate(req_list, user=self.admin)
        resp_list = ListarPeticionesView.as_view()(req_list)
        self.assertEqual(resp_list.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_list.data, [])

        # 4. POST /api/peticiones/iniciar/ (sin modificación en su respuesta)
        mock_estado_obj.filter.return_value.first.return_value = MagicMock(id_estado=1, nombre='En revisión')
        mock_pet = MagicMock(
            id_peticion=101,
            numero_radicado='PET-2026-00101',
            id_tipo=MagicMock(id_tipo=1),
            id_ubicacion=None,
            foto=None,
            prioridad='Media',
            id_estado=MagicMock(id_estado=1, nombre='En revisión'),
            asignado_a=None,
            asignado_a_id=None,
            descripcion='Mascota requiere atención veterinaria urgente',
        )
        mock_pet.evidenciapeticiones_set.values_list.return_value = []

        with patch.object(IniciarPeticionView, 'get_serializer') as mock_get_ser, \
             patch('peticiones.api.views.registrar_historial_peticion'), \
             patch('peticiones.api.views.crear_notificaciones_nueva_peticion'):
            ser = MagicMock()
            ser.is_valid.return_value = True
            ser.save.return_value = mock_pet
            mock_get_ser.return_value = ser

            req_iniciar = self.factory.post(
                '/api/peticiones/iniciar/',
                {'id_tipo': 1, 'descripcion': 'Mascota requiere atención veterinaria urgente'},
                format='json',
            )
            force_authenticate(req_iniciar, user=self.peticionario)
            resp_iniciar = IniciarPeticionView.as_view()(req_iniciar)

        self.assertEqual(resp_iniciar.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp_iniciar.data['id_peticion'], 101)
        self.assertEqual(resp_iniciar.data['numero_radicado'], 'PET-2026-00101')
        self.assertNotIn('posibles_duplicados', resp_iniciar.data)

    def test_swagger_incluye_endpoint_detectar_duplicados(self):
        response = self.client.get('/swagger/?format=openapi')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        paths = response.json().get('paths', {})
        coincidencias = [p for p in paths if 'detectar-duplicados' in p]
        self.assertTrue(coincidencias)
        self.assertIn('post', paths[coincidencias[0]])


class DetectarDuplicadosFase2GPSTestCase(SimpleTestCase):
    """Pruebas unitarias para la Fase 2 (Ubicaciones GPS, bounding box y Haversine)."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosPeticionView.as_view()
        self.juridico = _DummyUser(id_usuario=2, rol_id=2, activo=True)
        # Punto base en Popayán
        self.lat_base = 2.4448140
        self.lon_base = -76.6147390

    def test_haversine_y_bounding_box_y_validacion_coordenadas(self):
        # Mismo punto -> 0 m
        self.assertAlmostEqual(
            calcular_distancia_haversine_metros(self.lat_base, self.lon_base, self.lat_base, self.lon_base),
            0.0,
            places=2,
        )
        # ~0.0009 grados de latitud son ~100 m
        dist_100m = calcular_distancia_haversine_metros(
            self.lat_base, self.lon_base, self.lat_base + 0.0009, self.lon_base
        )
        self.assertTrue(95.0 <= dist_100m <= 105.0)

        bbox = calcular_bounding_box(self.lat_base, self.lon_base, 200.0)
        self.assertIsInstance(bbox['lat_min'], Decimal)
        self.assertLess(bbox['lat_min'], Decimal(str(self.lat_base)))
        self.assertGreater(bbox['lat_max'], Decimal(str(self.lat_base)))
        self.assertLess(bbox['lon_min'], Decimal(str(self.lon_base)))
        self.assertGreater(bbox['lon_max'], Decimal(str(self.lon_base)))

        # Coordenadas nulas, inválidas y (0,0)
        self.assertIsNone(normalizar_coordenadas(0, 0))
        self.assertIsNone(normalizar_coordenadas('0.0', '0.0'))
        self.assertIsNone(normalizar_coordenadas(None, -76.6))
        self.assertIsNone(normalizar_coordenadas(91.0, -76.6))
        self.assertIsNone(normalizar_coordenadas(2.44, 181.0))
        self.assertIsNone(normalizar_coordenadas('invalido', -76.6))
        self.assertIsNone(normalizar_coordenadas(float('nan'), -76.6))
        self.assertEqual(normalizar_coordenadas('2.4448', '-76.6147'), (2.4448, -76.6147))

    @override_settings(DUPLICADOS_RADIO_GPS_METROS=350.0, DUPLICADOS_MAX_CANDIDATOS_SQL=60)
    def test_configuracion_radio_gps_desde_settings_y_validacion_request(self):
        cfg = obtener_config_duplicados()
        self.assertEqual(cfg['radio_metros'], 350.0)
        self.assertEqual(cfg['limite_sql'], 60)

        req_bad = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {'latitud': self.lat_base, 'longitud': self.lon_base, 'radio_metros': 0.5},
            format='json',
        )
        force_authenticate(req_bad, user=self.juridico)
        resp_bad = self.view(req_bad)
        self.assertEqual(resp_bad.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_reportes_cercanos_y_descripciones_diferentes_en_misma_zona(self, mock_pet_objects):
        # Candidato cercano (~55 m) con descripción completamente diferente (similitud 0.08 < 0.35)
        cand_cercano = _crear_mock_peticion(
            id_peticion=50,
            numero_radicado='PET-2026-00050',
            descripcion='Gato atrapado en techo de vivienda residencial',
            fecha=datetime(2026, 11, 11, 10, 0, 0),
            estado_nombre='En revisión',
            similitud=0.08,
            latitud=Decimal('2.4453100'),
            longitud=Decimal('-76.6147390'),
            direccion='Calle 5 # 4-20',
        )
        # Candidato en esquina del bounding box (~250 m > 200 m) que debe ser descartado por Haversine
        cand_esquina_lejana = _crear_mock_peticion(
            id_peticion=51,
            numero_radicado='PET-2026-00051',
            descripcion='Otro reporte totalmente distinto fuera de 200 metros',
            fecha=datetime(2026, 11, 11, 9, 0, 0),
            estado_nombre='En revisión',
            similitud=0.05,
            latitud=Decimal('2.4464000'),
            longitud=Decimal('-76.6132000'),
        )
        # Candidato con (0,0) corrupto en BD también debe ser ignorado
        cand_cero = _crear_mock_peticion(
            id_peticion=52,
            numero_radicado='PET-2026-00052',
            descripcion='Reporte con coordenadas en cero',
            fecha=datetime(2026, 11, 11, 8, 0, 0),
            estado_nombre='En revisión',
            similitud=0.02,
            latitud=Decimal('0'),
            longitud=Decimal('0'),
        )

        # Primera llamada select_related -> query texto (sin coincidencias >= 0.35)
        qs_texto_root = MagicMock()
        (
            qs_texto_root.prefetch_related.return_value
            .filter.return_value
            .exclude.return_value
            .annotate.return_value
            .filter.return_value
            .annotate.return_value
            .filter.return_value
            .order_by.return_value
            .__getitem__.return_value
        ) = []

        # Segunda llamada select_related -> query GPS (bounding box devuelve 3 candidatos)
        qs_gps_root = MagicMock()
        (
            qs_gps_root.prefetch_related.return_value
            .filter.return_value
            .filter.return_value
            .annotate.return_value
            .order_by.return_value
            .__getitem__.return_value
        ) = [cand_cercano, cand_esquina_lejana, cand_cero]

        mock_pet_objects.select_related.side_effect = [qs_texto_root, qs_gps_root]

        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {
                'descripcion': 'Canino atropellado con fractura en extremidad posterior',
                'latitud': self.lat_base,
                'longitud': self.lon_base,
            },
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['posible_duplicado'])
        self.assertTrue(response.data['parametros']['texto_activo'])
        self.assertTrue(response.data['parametros']['gps_activo'])
        self.assertEqual(response.data['total_candidatos'], 1)

        cand = response.data['posibles_duplicados'][0]
        self.assertEqual(cand['id_peticion'], 50)
        self.assertAlmostEqual(cand['similitud_texto'], 0.08, places=2)
        self.assertFalse(cand['coincidencia_texto'])
        self.assertTrue(cand['coincidencia_gps'])
        self.assertTrue(cand['posible_duplicado'])
        self.assertIsNotNone(cand['distancia_metros'])
        self.assertLessEqual(cand['distancia_metros'], 200.0)
        self.assertEqual(cand['criterios_coincidencia'], ['gps'])

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_reportes_lejanos_sin_coincidencia_texto_no_se_marcan_duplicados(self, mock_pet_objects):
        # Texto diferente y fuera de bounding box (query GPS devuelve vacío, query texto vacío)
        qs_root = MagicMock()
        qs_root.prefetch_related.return_value.filter.return_value.exclude.return_value.annotate.return_value.filter.return_value.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = []
        qs_root.prefetch_related.return_value.filter.return_value.filter.return_value.annotate.return_value.order_by.return_value.__getitem__.return_value = []
        mock_pet_objects.select_related.return_value = qs_root

        resultado = buscar_peticiones_similares_por_descripcion(
            'Reporte de jornada de vacunacion en vereda lejana',
            latitud=self.lat_base + 0.05,  # > 5 km
            longitud=self.lon_base,
        )
        self.assertEqual(resultado['total_candidatos'], 0)
        self.assertFalse(resultado['posible_duplicado'])
        self.assertTrue(resultado['parametros']['gps_activo'])

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_coordenadas_invalidas_se_ignoran_y_texto_sigue_funcionando(self, mock_pet_objects):
        cand_texto = _crear_mock_peticion(
            id_peticion=60,
            numero_radicado='PET-2026-00060',
            descripcion='Perro herido atropellado frente al parque Caldas',
            fecha=datetime(2026, 11, 11, 12, 0, 0),
            estado_nombre='En revisión',
            similitud=0.81,
            latitud=Decimal('2.4450000'),
            longitud=Decimal('-76.6150000'),
        )

        qs_txt = MagicMock()
        qs_txt.prefetch_related.return_value.filter.return_value.exclude.return_value.annotate.return_value.filter.return_value.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = [cand_texto]
        mock_pet_objects.select_related.return_value = qs_txt

        for lat_bad, lon_bad in [
            (0, 0),
            (150.0, -76.6),
            (2.44, -200.0),
            ('no_numero', '-76.6'),
            (None, -76.6),
        ]:
            with self.subTest(lat=lat_bad, lon=lon_bad):
                mock_pet_objects.select_related.reset_mock()
                request = self.factory.post(
                    '/api/peticiones/detectar-duplicados/',
                    {
                        'descripcion': 'Perro herido atropellado cerca al parque Caldas',
                        'latitud': lat_bad,
                        'longitud': lon_bad,
                    },
                    format='json',
                )
                force_authenticate(request, user=self.juridico)
                response = self.view(request)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertFalse(response.data['parametros']['gps_activo'])
                self.assertTrue(response.data['parametros']['texto_activo'])
                self.assertEqual(response.data['total_candidatos'], 1)
                self.assertIsNone(response.data['posibles_duplicados'][0]['distancia_metros'])
                self.assertFalse(response.data['posibles_duplicados'][0]['coincidencia_gps'])
                self.assertTrue(response.data['posibles_duplicados'][0]['posible_duplicado'])
                # Solo 1 llamada select_related (texto), sin consulta GPS innecesaria
                self.assertEqual(mock_pet_objects.select_related.call_count, 1)

    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_combinacion_ordena_coincidencia_dual_primero_y_soporta_sin_gps_en_candidato(self, mock_pet_objects):
        # Candidato 1: coincide solo por texto (no tiene GPS) con similitud 0.85
        cand_solo_texto = _crear_mock_peticion(
            id_peticion=70,
            numero_radicado='PET-2026-00070',
            descripcion='Perro herido atropellado cerca al parque principal',
            fecha=datetime(2026, 11, 11, 14, 0, 0),
            estado_nombre='En revisión',
            similitud=0.85,
        )
        # Candidato 2: coincide por texto (0.70) Y por GPS (~33 m) -> debe ir primero
        cand_dual = _crear_mock_peticion(
            id_peticion=71,
            numero_radicado='PET-2026-00071',
            descripcion='Perro atropellado en parque principal centro',
            fecha=datetime(2026, 11, 11, 13, 0, 0),
            estado_nombre='Asignada',
            similitud=0.70,
            latitud=Decimal('2.4451100'),
            longitud=Decimal('-76.6147390'),
        )
        # Candidato 3: coincide por texto (0.78) pero está lejos (> 200m, ~555m)
        cand_texto_lejos = _crear_mock_peticion(
            id_peticion=72,
            numero_radicado='PET-2026-00072',
            descripcion='Perro herido atropellado cerca al parque norte',
            fecha=datetime(2026, 11, 11, 12, 0, 0),
            estado_nombre='En Proceso',
            similitud=0.78,
            latitud=Decimal('2.4498140'),
            longitud=Decimal('-76.6147390'),
        )

        mock_pet_objects.filter.return_value.values.return_value.first.return_value = {
            'id_peticion': 90,
            'descripcion': 'Perro herido atropellado frente al parque principal',
            'id_ubicacion__latitud': Decimal(str(self.lat_base)),
            'id_ubicacion__longitud': Decimal(str(self.lon_base)),
        }

        qs_txt = MagicMock()
        qs_txt.prefetch_related.return_value.filter.return_value.exclude.return_value.annotate.return_value.filter.return_value.exclude.return_value.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = [
            cand_solo_texto, cand_texto_lejos, cand_dual
        ]
        qs_gps = MagicMock()
        qs_gps.prefetch_related.return_value.filter.return_value.filter.return_value.exclude.return_value.annotate.return_value.order_by.return_value.__getitem__.return_value = [
            cand_dual
        ]
        mock_pet_objects.select_related.side_effect = [qs_txt, qs_gps]

        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {'id_peticion': 90},
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['parametros']['id_peticion_excluido'], 90)
        self.assertTrue(response.data['parametros']['gps_activo'])
        cands = response.data['posibles_duplicados']
        self.assertEqual([c['id_peticion'] for c in cands], [71, 70, 72])

        # El candidato dual aparece primero con ambas métricas
        self.assertEqual(cands[0]['criterios_coincidencia'], ['texto', 'gps'])
        self.assertTrue(cands[0]['coincidencia_gps'])
        self.assertLess(cands[0]['distancia_metros'], 50.0)

        # El candidato sin GPS mantiene distancia_metros=None y coincidencia_gps=False
        self.assertIsNone(cands[1]['distancia_metros'])
        self.assertFalse(cands[1]['coincidencia_gps'])

        # El candidato lejano muestra su distancia > 200m pero coincidencia_gps=False
        self.assertGreater(cands[2]['distancia_metros'], 500.0)
        self.assertFalse(cands[2]['coincidencia_gps'])


def _generar_imagen_bytes_patron_a(*, formato='JPEG', calidad=92, tamano=(160, 120), factor_brillo=1.0):
    """Genera una imagen sintética con gradientes y figuras asimétricas para pruebas dHash."""
    img = Image.new('RGB', tamano)
    px = img.load()
    w, h = tamano
    for y in range(h):
        for x in range(w):
            r = int((x / max(1, w - 1)) * 220)
            g = 240 if ((x // 24 + y // 24) % 2 == 0) else 35
            b = int((y / max(1, h - 1)) * 200)
            if x < w // 3 and y < h // 2:
                r = min(255, r + 60)
            px[x, y] = (r, g, b)
    draw = ImageDraw.Draw(img)
    draw.rectangle([w // 2, h // 4, w - 10, h - 12], fill=(15, 210, 40))
    draw.polygon([(12, h - 15), (w // 2, 18), (w // 3, h - 20)], fill=(250, 230, 30))

    if factor_brillo != 1.0:
        img = ImageEnhance.Brightness(img).enhance(factor_brillo)

    buf = io.BytesIO()
    if formato.upper() == 'JPEG':
        img.save(buf, format='JPEG', quality=calidad)
    else:
        img.save(buf, format=formato)
    return buf.getvalue()


def _modificar_imagen_ligeramente(imagen_bytes: bytes, *, tamano=(128, 96), calidad=65, factor_brillo=1.10) -> bytes:
    """Genera una versión ligeramente modificada (re-escalado, brillo y recompresión JPEG)."""
    with Image.open(io.BytesIO(imagen_bytes)) as img:
        resample = getattr(getattr(Image, 'Resampling', Image), 'BILINEAR', Image.BILINEAR)
        modificada = img.resize(tamano, resample)
        modificada = ImageEnhance.Brightness(modificada).enhance(factor_brillo)
        buf = io.BytesIO()
        modificada.save(buf, format='JPEG', quality=calidad)
        return buf.getvalue()


def _generar_imagen_bytes_patron_b(*, tamano=(160, 120)):
    """Genera una imagen estructuralmente opuesta para garantizar distancia Hamming alta."""
    img = Image.new('RGB', tamano)
    px = img.load()
    w, h = tamano
    for y in range(h):
        for x in range(w):
            val = 250 if (x < w // 2) else 10
            if (x * 3 + y) % 19 < 8:
                val = 255 - val
            px[x, y] = (val, 255 - val, (val + 90) % 255)
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, w // 2 - 4, h - 8], fill=(0, 0, 20))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


def _generar_imagen_bytes_patron_c(*, tamano=(160, 120)):
    """Genera un tercer patrón distinto para pruebas con múltiples fotos no coincidentes."""
    img = Image.new('RGB', tamano)
    px = img.load()
    w, h = tamano
    for y in range(h):
        for x in range(w):
            v = int(((w - 1 - x) / max(1, w - 1)) * 255)
            banda = 220 if (x // 8) % 2 == 1 else 25
            px[x, y] = (banda, v, 255 - banda)
    draw = ImageDraw.Draw(img)
    draw.rectangle([4, 4, w // 3, h - 4], fill=(245, 245, 245))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


class _FakeHttpResponse:
    def __init__(self, body=b'', status_code=200, headers=None, content_type='image/jpeg'):
        self._body = body
        self.status_code = status_code
        self.headers = dict(headers or {})
        if content_type and 'Content-Type' not in self.headers:
            self.headers['Content-Type'] = content_type

    def iter_content(self, chunk_size=16384):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        return None


class DetectarDuplicadosFase3ImagenTestCase(SimpleTestCase):
    """Pruebas unitarias para Fase 3 (Pillow dHash 64-bit, Hamming y seguridad SSRF/Cloudinary)."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosPeticionView.as_view()
        self.juridico = _DummyUser(id_usuario=2, rol_id=2, activo=True)
        self.bytes_a = _generar_imagen_bytes_patron_a()
        self.bytes_a_mod = _modificar_imagen_ligeramente(
            self.bytes_a,
            tamano=(128, 96),
            calidad=60,
            factor_brillo=1.10,
        )
        self.bytes_b = _generar_imagen_bytes_patron_b()
        self.bytes_c = _generar_imagen_bytes_patron_c()

    def test_dhash_identico_modificado_diferente_y_danado(self):
        h_a = calcular_dhash_desde_bytes(self.bytes_a)
        h_a_copia = calcular_dhash_desde_bytes(self.bytes_a)
        h_a_mod = calcular_dhash_desde_bytes(self.bytes_a_mod)
        h_b = calcular_dhash_desde_bytes(self.bytes_b)

        self.assertIsNotNone(h_a)
        self.assertIsNotNone(h_a_mod)
        self.assertIsNotNone(h_b)

        self.assertEqual(calcular_distancia_hamming(h_a, h_a_copia), 0)
        dist_mod = calcular_distancia_hamming(h_a, h_a_mod)
        self.assertLessEqual(dist_mod, 10)

        dist_diff = calcular_distancia_hamming(h_a, h_b)
        self.assertGreater(dist_diff, 10)

        # Imágenes faltantes o corruptas retornan None sin lanzar excepción
        self.assertIsNone(calcular_dhash_desde_bytes(None))
        self.assertIsNone(calcular_dhash_desde_bytes(b''))
        self.assertIsNone(calcular_dhash_desde_bytes(b'contenido-corrupto-no-imagen'))
        self.assertIsNone(calcular_dhash_desde_bytes(self.bytes_a[:15]))

    def test_validacion_url_cloudinary_ssrf_y_redirecciones_bloqueadas(self):
        url_valida = 'https://res.cloudinary.com/demo/image/upload/v1700/perro.jpg'
        self.assertEqual(validar_url_cloudinary_segura(url_valida), url_valida)
        self.assertIn(
            '/image/upload/w_128,h_128,c_limit,f_jpg/',
            construir_url_miniatura_cloudinary(url_valida),
        )

        # Dominios no autorizados, HTTP plano, credenciales, IPs literales, traversal y videos
        urls_invalidas = [
            None,
            '',
            'http://res.cloudinary.com/demo/image/upload/perro.jpg',
            'https://evil.com/perro.jpg',
            'https://res.cloudinary.com.evil.com/perro.jpg',
            'https://127.0.0.1/perro.jpg',
            'https://169.254.169.254/latest/meta-data/',
            'https://user:pass@res.cloudinary.com/demo/image/upload/perro.jpg',
            'https://res.cloudinary.com:8443/demo/image/upload/perro.jpg',
            'https://res.cloudinary.com/demo/image/upload/../../secret.jpg',
            'https://res.cloudinary.com/demo/video/upload/v1/video.mp4',
            'data:image/png;base64,iVBORw0KGgo=',
        ]
        for url_bad in urls_invalidas:
            with self.subTest(url=url_bad):
                self.assertIsNone(validar_url_cloudinary_segura(url_bad))

        # Bloqueo DNS anti-SSRF cuando el hostname resuelve a IPs internas/loopback/metadata
        with patch('peticiones.services.imagen_hash_service.socket.getaddrinfo') as mock_dns:
            for ip_privada in ('127.0.0.1', '10.0.0.15', '172.16.0.1', '192.168.1.5', '169.254.169.254', '::1'):
                mock_dns.return_value = [(socket.AF_INET6 if ':' in ip_privada else socket.AF_INET, 1, 6, '', (ip_privada, 443))]
                self.assertFalse(_validar_destino_dns('res.cloudinary.com', 443))

        with patch('peticiones.services.imagen_hash_service.requests.get') as mock_get, \
             patch('peticiones.services.imagen_hash_service._validar_destino_dns', return_value=False):
            self.assertIsNone(descargar_imagen_segura(url_valida))
            mock_get.assert_not_called()

        # Bloqueo de redirecciones inseguras (hacia HTTP, dominio no permitido o IP local)
        with patch('peticiones.services.imagen_hash_service._validar_destino_dns', return_value=True), \
             patch('peticiones.services.imagen_hash_service.requests.get') as mock_get:
            mock_get.return_value = _FakeHttpResponse(
                status_code=302,
                headers={'Location': 'http://127.0.0.1/internal.jpg'},
            )
            self.assertIsNone(descargar_imagen_segura(url_valida, usar_miniatura=False))
            self.assertEqual(mock_get.call_count, 1)

        # Bloqueo por Content-Type no imagen y exceso de tamaño
        with patch('peticiones.services.imagen_hash_service._validar_destino_dns', return_value=True), \
             patch('peticiones.services.imagen_hash_service.requests.get') as mock_get:
            mock_get.return_value = _FakeHttpResponse(b'<html>no</html>', status_code=200, content_type='text/html')
            self.assertIsNone(descargar_imagen_segura(url_valida, usar_miniatura=False))

            mock_get.return_value = _FakeHttpResponse(
                b'x' * 500,
                status_code=200,
                headers={'Content-Type': 'image/jpeg', 'Content-Length': '9999999'},
            )
            self.assertIsNone(descargar_imagen_segura(url_valida, max_bytes=1024, usar_miniatura=False))

    @override_settings(DUPLICADOS_UMBRAL_HAMMING=8, DUPLICADOS_MAX_IMAGENES_POR_PETICION=2)
    def test_validacion_umbral_hamming_y_comparar_conjuntos(self):
        h_a = calcular_dhash_desde_bytes(self.bytes_a)
        h_b = calcular_dhash_desde_bytes(self.bytes_b)
        comp = comparar_conjuntos_hashes([h_b, h_a], [h_a, h_b])
        self.assertTrue(comp['comparado'])
        self.assertTrue(comp['coincidencia_imagen'])
        self.assertEqual(comp['distancia_hamming'], 0)
        self.assertEqual(comp['similitud_imagen'], 1.0)

        req_bad = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {'descripcion': 'Perro herido frente al parque', 'umbral_hamming': 65},
            format='json',
        )
        force_authenticate(req_bad, user=self.juridico)
        resp_bad = self.view(req_bad)
        self.assertEqual(resp_bad.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('peticiones.services.imagen_hash_service._validar_destino_dns', return_value=True)
    @patch('peticiones.services.imagen_hash_service.requests.get')
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_endpoint_compara_multiples_fotos_conserva_mejor_coincidencia_y_deduplica_urls(
        self, mock_pet_objects, mock_get, _mock_dns
    ):
        url_orig_1 = 'https://res.cloudinary.com/vet/image/upload/orig_1.jpg'
        url_orig_2 = 'https://res.cloudinary.com/vet/image/upload/orig_2.jpg'
        url_cand_1_match = 'https://res.cloudinary.com/vet/image/upload/cand1_mod.jpg'
        url_cand_1_diff = 'https://res.cloudinary.com/vet/image/upload/cand1_otro.jpg'
        url_cand_2_diff = 'https://res.cloudinary.com/vet/image/upload/cand2_diff.jpg'

        # Misma URL en Peticiones.foto y EvidenciaPeticiones.ruta_archivo se deduplica y cachea
        cand_match_img = _crear_mock_peticion(
            id_peticion=201,
            numero_radicado='PET-2026-00201',
            descripcion='Perro herido cerca al parque Caldas',
            fecha=datetime(2026, 11, 12, 10, 0, 0),
            estado_nombre='En revisión',
            foto=url_cand_1_diff,
            evidencias=[url_cand_1_diff, url_cand_1_match],
            similitud=0.72,
        )
        cand_diff_img = _crear_mock_peticion(
            id_peticion=202,
            numero_radicado='PET-2026-00202',
            descripcion='Perro herido atropellado frente al parque Caldas',
            fecha=datetime(2026, 11, 12, 9, 0, 0),
            estado_nombre='Asignada',
            foto=url_cand_2_diff,
            similitud=0.82,
        )

        qs_txt = mock_pet_objects.select_related.return_value
        (
            qs_txt.prefetch_related.return_value
            .filter.return_value
            .exclude.return_value
            .annotate.return_value
            .filter.return_value
            .annotate.return_value
            .filter.return_value
            .order_by.return_value
            .__getitem__.return_value
        ) = [cand_diff_img, cand_match_img]

        mapa_bytes = {
            'orig_1.jpg': self.bytes_b,
            'orig_2.jpg': self.bytes_a,
            'cand1_otro.jpg': self.bytes_c,
            'cand1_mod.jpg': self.bytes_a_mod,
            'cand2_diff.jpg': self.bytes_c,
        }

        def fake_get(url, **_kwargs):
            for nombre, contenido in mapa_bytes.items():
                if url.endswith(nombre):
                    return _FakeHttpResponse(contenido, status_code=200)
            return _FakeHttpResponse(b'', status_code=404)

        mock_get.side_effect = fake_get

        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {
                'descripcion': 'Perro herido atropellado frente al parque Caldas',
                'foto': url_orig_1,
                'fotos': [url_orig_1, url_orig_2],
            },
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['parametros']['imagen_activa'])
        self.assertEqual(response.data['parametros']['umbral_hamming'], 10)
        cands = response.data['posibles_duplicados']
        self.assertEqual(len(cands), 2)

        # El candidato 201 coincide en texto + imagen (tier 2) y sube sobre 202 (solo texto, foto diferente)
        self.assertEqual([c['id_peticion'] for c in cands], [201, 202])
        self.assertTrue(cands[0]['coincidencia_imagen'])
        self.assertIsNotNone(cands[0]['distancia_hamming'])
        self.assertLessEqual(cands[0]['distancia_hamming'], 10)
        self.assertGreater(cands[0]['similitud_imagen'], 0.8)
        self.assertEqual(cands[0]['criterios_coincidencia'], ['texto', 'imagen'])
        self.assertAlmostEqual(
            cands[0]['score_combinado'],
            round((0.72 + cands[0]['similitud_imagen']) / 2.0, 4),
            places=4,
        )

        # El candidato 202 tiene foto distinta: se compara, no coincide visualmente y conserva texto
        self.assertFalse(cands[1]['coincidencia_imagen'])
        self.assertGreater(cands[1]['distancia_hamming'], 10)
        self.assertEqual(cands[1]['criterios_coincidencia'], ['texto'])
        self.assertEqual(mock_get.call_count, 5)  # 5 URLs únicas (url_orig_1 y url_cand_1_diff cacheadas/deduplicadas)

    @patch('peticiones.services.imagen_hash_service._validar_destino_dns', return_value=True)
    @patch('peticiones.services.imagen_hash_service.requests.get')
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_fotos_diferentes_retornan_coincidencia_imagen_false(
        self, mock_pet_objects, mock_get, _mock_dns
    ):
        cand_diff = _crear_mock_peticion(
            id_peticion=205,
            numero_radicado='PET-2026-00205',
            descripcion='Perro herido atropellado en parque principal',
            fecha=datetime(2026, 11, 12, 11, 0, 0),
            estado_nombre='En revisión',
            foto='https://res.cloudinary.com/vet/image/upload/cand_b.jpg',
            similitud=0.80,
        )
        qs_txt = mock_pet_objects.select_related.return_value
        (
            qs_txt.prefetch_related.return_value
            .filter.return_value
            .exclude.return_value
            .annotate.return_value
            .filter.return_value
            .annotate.return_value
            .filter.return_value
            .order_by.return_value
            .__getitem__.return_value
        ) = [cand_diff]

        def fake_get(url, **_kwargs):
            if url.endswith('orig_a.jpg'):
                return _FakeHttpResponse(self.bytes_a, status_code=200)
            return _FakeHttpResponse(self.bytes_b, status_code=200)

        mock_get.side_effect = fake_get

        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {
                'descripcion': 'Perro herido atropellado en el parque principal',
                'foto': 'https://res.cloudinary.com/vet/image/upload/orig_a.jpg',
            },
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cand = response.data['posibles_duplicados'][0]
        self.assertFalse(cand['coincidencia_imagen'])
        self.assertGreater(cand['distancia_hamming'], 10)
        self.assertLess(cand['similitud_imagen'], 0.85)
        self.assertEqual(cand['criterios_coincidencia'], ['texto'])

    @patch('peticiones.services.imagen_hash_service._validar_destino_dns', return_value=True)
    @patch('peticiones.services.imagen_hash_service.requests.get')
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_fallo_cloudinary_o_url_no_autorizada_no_afecta_texto_ni_gps(
        self, mock_pet_objects, mock_get, _mock_dns
    ):
        cand = _crear_mock_peticion(
            id_peticion=210,
            numero_radicado='PET-2026-00210',
            descripcion='Canino herido en avenida Panamericana norte',
            fecha=datetime(2026, 11, 12, 12, 0, 0),
            estado_nombre='En revisión',
            foto='https://res.cloudinary.com/vet/image/upload/cand_timeout.jpg',
            similitud=0.86,
            latitud=Decimal('2.4448500'),
            longitud=Decimal('-76.6147390'),
        )
        qs_txt = MagicMock()
        qs_txt.prefetch_related.return_value.filter.return_value.exclude.return_value.annotate.return_value.filter.return_value.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = [cand]
        qs_gps = MagicMock()
        qs_gps.prefetch_related.return_value.filter.return_value.filter.return_value.annotate.return_value.order_by.return_value.__getitem__.return_value = [cand]

        # Caso 1: Fallo de red / timeout en Cloudinary
        mock_pet_objects.select_related.side_effect = [qs_txt, qs_gps]
        mock_get.side_effect = requests.Timeout('Cloudinary timeout')

        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {
                'descripcion': 'Canino herido en la avenida Panamericana norte',
                'latitud': 2.4448140,
                'longitud': -76.6147390,
                'foto': 'https://res.cloudinary.com/vet/image/upload/orig_timeout.jpg',
            },
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['parametros']['imagen_activa'])
        self.assertEqual(response.data['total_candidatos'], 1)
        res_cand = response.data['posibles_duplicados'][0]
        self.assertTrue(res_cand['coincidencia_texto'])
        self.assertTrue(res_cand['coincidencia_gps'])
        self.assertFalse(res_cand['coincidencia_imagen'])
        self.assertIsNone(res_cand['distancia_hamming'])
        self.assertIsNone(res_cand['similitud_imagen'])
        self.assertEqual(res_cand['criterios_coincidencia'], ['texto', 'gps'])
        self.assertTrue(res_cand['posible_duplicado'])

        # Caso 2: URL de dominio no autorizado se ignora sin lanzar error ni llamar requests.get
        mock_get.reset_mock()
        mock_pet_objects.select_related.side_effect = [qs_txt, qs_gps]
        request_bad_url = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {
                'descripcion': 'Canino herido en la avenida Panamericana norte',
                'latitud': 2.4448140,
                'longitud': -76.6147390,
                'foto': 'https://evil-domain.com/maliciosa.jpg',
            },
            format='json',
        )
        force_authenticate(request_bad_url, user=self.juridico)
        resp_bad_url = self.view(request_bad_url)
        self.assertEqual(resp_bad_url.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_bad_url.data['total_candidatos'], 1)
        mock_get.assert_not_called()

    @override_settings(DUPLICADOS_MAX_CANDIDATOS_IMAGEN=1)
    @patch('peticiones.services.imagen_hash_service._validar_destino_dns', return_value=True)
    @patch('peticiones.services.imagen_hash_service.requests.get')
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_solo_compara_candidatos_preseleccionados_y_respeta_limite_candidatos_imagen(
        self, mock_pet_objects, mock_get, _mock_dns
    ):
        # Si no hay texto válido ni GPS, no escanea BD ni descarga imágenes
        request_solo_foto = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {'foto': 'https://res.cloudinary.com/vet/image/upload/sola.jpg'},
            format='json',
        )
        force_authenticate(request_solo_foto, user=self.juridico)
        resp_solo_foto = self.view(request_solo_foto)
        self.assertEqual(resp_solo_foto.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_solo_foto.data['total_candidatos'], 0)
        mock_pet_objects.select_related.assert_not_called()
        mock_get.assert_not_called()

        # Si hay 2 candidatos preseleccionados con foto y límite=1, solo descarga del 1ro
        cand_1 = _crear_mock_peticion(
            id_peticion=301,
            numero_radicado='PET-2026-00301',
            descripcion='Perro herido parque central',
            fecha=datetime(2026, 11, 12, 14, 0, 0),
            estado_nombre='En revisión',
            foto='https://res.cloudinary.com/vet/image/upload/c1.jpg',
            similitud=0.90,
        )
        cand_2 = _crear_mock_peticion(
            id_peticion=302,
            numero_radicado='PET-2026-00302',
            descripcion='Perro herido parque sur',
            fecha=datetime(2026, 11, 12, 13, 0, 0),
            estado_nombre='En revisión',
            foto='https://res.cloudinary.com/vet/image/upload/c2.jpg',
            similitud=0.60,
        )
        qs_txt = mock_pet_objects.select_related.return_value
        qs_txt.prefetch_related.return_value.filter.return_value.exclude.return_value.annotate.return_value.filter.return_value.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = [
            cand_1, cand_2
        ]
        mock_get.return_value = _FakeHttpResponse(self.bytes_a, status_code=200)

        request_lim = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {
                'descripcion': 'Perro herido parque central',
                'foto': 'https://res.cloudinary.com/vet/image/upload/orig.jpg',
            },
            format='json',
        )
        force_authenticate(request_lim, user=self.juridico)
        resp_lim = self.view(request_lim)
        self.assertEqual(resp_lim.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_get.call_count, 2)  # solo orig.jpg y c1.jpg (c2.jpg omitida por límite=1)
        self.assertTrue(resp_lim.data['posibles_duplicados'][0]['coincidencia_imagen'])
        self.assertFalse(resp_lim.data['posibles_duplicados'][1]['coincidencia_imagen'])
        self.assertIsNone(resp_lim.data['posibles_duplicados'][1]['distancia_hamming'])

    @patch('peticiones.services.imagen_hash_service._validar_destino_dns', return_value=True)
    @patch('peticiones.services.imagen_hash_service.requests.get')
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_consulta_por_id_peticion_extrae_foto_y_evidencias_referencia(
        self, mock_pet_objects, mock_get, _mock_dns
    ):
        mock_pet_objects.filter.return_value.values.return_value.first.return_value = {
            'id_peticion': 400,
            'descripcion': 'Gato abandonado en via publica barrio norte',
            'id_ubicacion__latitud': None,
            'id_ubicacion__longitud': None,
            'foto': 'https://res.cloudinary.com/vet/image/upload/ref_foto.jpg',
        }
        mock_pet_objects.filter.return_value.values_list.return_value = [
            'https://res.cloudinary.com/vet/image/upload/ref_evid.jpg',
        ]
        cand = _crear_mock_peticion(
            id_peticion=401,
            numero_radicado='PET-2026-00401',
            descripcion='Gato abandonado en via publica norte',
            fecha=datetime(2026, 11, 12, 15, 0, 0),
            estado_nombre='En revisión',
            foto='https://res.cloudinary.com/vet/image/upload/cand_401.jpg',
            similitud=0.84,
        )
        qs_txt = mock_pet_objects.select_related.return_value
        qs_txt.prefetch_related.return_value.filter.return_value.exclude.return_value.annotate.return_value.filter.return_value.exclude.return_value.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = [cand]

        def fake_get(url, **_kwargs):
            if url.endswith('ref_foto.jpg'):
                return _FakeHttpResponse(self.bytes_b, status_code=200)
            if url.endswith('ref_evid.jpg'):
                return _FakeHttpResponse(self.bytes_a, status_code=200)
            if url.endswith('cand_401.jpg'):
                return _FakeHttpResponse(self.bytes_a_mod, status_code=200)
            return _FakeHttpResponse(b'', status_code=404)

        mock_get.side_effect = fake_get

        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {'id_peticion': 400},
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['parametros']['imagen_activa'])
        self.assertEqual(response.data['posibles_duplicados'][0]['id_peticion'], 401)
        self.assertTrue(response.data['posibles_duplicados'][0]['coincidencia_imagen'])
        self.assertEqual(response.data['posibles_duplicados'][0]['criterios_coincidencia'], ['texto', 'imagen'])


