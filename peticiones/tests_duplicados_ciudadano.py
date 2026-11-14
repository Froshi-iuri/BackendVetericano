"""
Pruebas del endpoint ciudadano POST /api/peticiones/detectar-duplicados-ciudadano/.
"""

import json
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIRequestFactory, force_authenticate

from peticiones.api.duplicados_ciudadano import (
    DetectarDuplicadosCiudadanoView,
    DuplicadosCiudadanoRafagaThrottle,
    DuplicadosCiudadanoSostenidoThrottle,
)
from peticiones.api.views import DetectarDuplicadosPeticionView
from users.api.permissions import IsJuridicoOrAdmin


class _DummyUser:
    def __init__(self, id_usuario, rol_id, activo=True, nombre='Ciudadano', apellido='Prueba'):
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


def _respuesta_interna(candidatos):
    return {
        'advertencia_duplicado': len(candidatos) > 0,
        'posible_duplicado': len(candidatos) > 0,
        'total_candidatos': len(candidatos),
        'posibles_duplicados': candidatos,
        'mensaje': 'Mensaje interno',
        'parametros': {'umbral_similitud': 0.35, 'radio_metros': 200.0},
    }


def _candidato_interno(**overrides):
    base = {
        'id_peticion': 999,
        'numero_radicado': 'PET-2026-00999',
        'descripcion': 'Descripcion confidencial del tercero en la denuncia',
        'fecha': '2026-11-10T10:00:00',
        'similitud': 0.72,
        'similitud_texto': 0.72,
        'porcentaje_similitud': 72.0,
        'coincidencia_texto': True,
        'distancia_metros': 85.5,
        'coincidencia_gps': True,
        'coincidencia_imagen': False,
        'distancia_hamming': None,
        'similitud_imagen': None,
        'posible_duplicado': True,
        'criterios_coincidencia': ['texto', 'gps'],
        'score_combinado': 0.80,
        'id_estado': 1,
        'estado': 'En revisión',
        'ubicacion': {
            'direccion': 'Calle Falsa 123 Barrio Secreto',
            'latitud': 2.444814,
            'longitud': -76.614739,
        },
        'foto': 'https://res.cloudinary.com/vet/image/upload/v1700000/perro_secreto.jpg',
        'fotos': ['https://res.cloudinary.com/vet/image/upload/v1700000/perro_secreto.jpg'],
        'tiene_fotografia': True,
        'usuario': {'nombre': 'Juan Perez', 'telefono': '3001234567'},
        'asignacion': {'funcionario': 'Dra. Ana'},
    }
    base.update(overrides)
    return base


class DetectarDuplicadosCiudadanoPermisosTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosCiudadanoView.as_view()
        self.url = '/api/peticiones/detectar-duplicados-ciudadano/'
        self.payload = {
            'descripcion': 'Perro herido frente al parque principal del barrio',
            'latitud': 2.444814,
            'longitud': -76.614739,
        }

    def tearDown(self):
        cache.clear()

    def test_sin_autenticacion_retorna_401(self):
        request = self.factory.post(self.url, self.payload, format='json')
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_roles_no_peticionario_retornan_403(self):
        for rol_id in (1, 2, 3, 5):
            with self.subTest(rol_id=rol_id):
                request = self.factory.post(self.url, self.payload, format='json')
                force_authenticate(request, user=_DummyUser(id_usuario=10 + rol_id, rol_id=rol_id, activo=True))
                response = self.view(request)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_peticionario_inactivo_retorna_403(self):
        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=_DummyUser(id_usuario=40, rol_id=4, activo=False))
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_peticionario_activo_retorna_200(self, mock_detector):
        mock_detector.return_value = _respuesta_interna([])
        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=_DummyUser(id_usuario=41, rol_id=4, activo=True))
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class DetectarDuplicadosCiudadanoValidacionTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosCiudadanoView.as_view()
        self.url = '/api/peticiones/detectar-duplicados-ciudadano/'
        self.peticionario = _DummyUser(id_usuario=42, rol_id=4, activo=True)

    def tearDown(self):
        cache.clear()

    def test_payload_invalido_retorna_400(self):
        casos_invalidos = [
            {'latitud': 2.44, 'longitud': -76.61},  # falta descripcion
            {'descripcion': 'Perro herido cerca al parque', 'longitud': -76.61},  # falta latitud
            {'descripcion': 'Perro herido cerca al parque', 'latitud': 2.44},  # falta longitud
            {'descripcion': '   corto   ', 'latitud': 2.44, 'longitud': -76.61},  # < 10 tras strip
            {'descripcion': '              ', 'latitud': 2.44, 'longitud': -76.61},  # solo espacios
            {'descripcion': 'a' * 1001, 'latitud': 2.44, 'longitud': -76.61},  # > 1000
            {'descripcion': 123456789012, 'latitud': 2.44, 'longitud': -76.61},  # no str
            {'descripcion': 'Perro herido cerca al parque', 'latitud': -90.0001, 'longitud': -76.61},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': 90.0001, 'longitud': -76.61},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': 2.44, 'longitud': -180.0001},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': 2.44, 'longitud': 180.0001},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': 'abc', 'longitud': -76.61},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': 2.44, 'longitud': 'xyz'},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': 'NaN', 'longitud': -76.61},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': 2.44, 'longitud': 'inf'},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': '-Infinity', 'longitud': -76.61},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': True, 'longitud': -76.61},
            {'descripcion': 'Perro herido cerca al parque', 'latitud': 2.44, 'longitud': False},
        ]
        for idx, payload in enumerate(casos_invalidos):
            with self.subTest(caso=idx, payload=payload):
                cache.clear()
                request = self.factory.post(self.url, payload, format='json')
                force_authenticate(request, user=self.peticionario)
                response = self.view(request)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_descripcion_exacta_1000_tras_strip_y_bordes_gps_validos_200(self, mock_detector):
        mock_detector.return_value = _respuesta_interna([])
        payload = {
            'descripcion': '   ' + ('x' * 1000) + '   ',
            'latitud': -90.0,
            'longitud': 180.0,
        }
        request = self.factory.post(self.url, payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_detector.call_args.kwargs['descripcion'], 'x' * 1000)

    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_campos_extra_se_ignoran_y_parametros_son_fijos(self, mock_detector):
        mock_detector.return_value = _respuesta_interna([])
        payload = {
            'descripcion': '  Perro herido frente al parque central  ',
            'latitud': 2.444814,
            'longitud': -76.614739,
            'umbral': 0.01,
            'radio_metros': 50000,
            'limite': 50,
            'dias': 3650,
            'min_longitud': 1,
            'umbral_hamming': 64,
            'foto': 'https://res.cloudinary.com/vet/image/upload/inyeccion.jpg',
            'fotos': ['https://res.cloudinary.com/vet/image/upload/inyeccion2.jpg'],
            'id_peticion': 123,
            'excluir_id_peticion': 456,
        }
        request = self.factory.post(self.url, payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_detector.assert_called_once_with(
            descripcion='Perro herido frente al parque central',
            latitud=2.444814,
            longitud=-76.614739,
            coordenadas_proporcionadas=True,
            limite=5,
            incluir_imagenes=False,
            incluir_evidencias=True,
        )


class DetectarDuplicadosCiudadanoRespuestaTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosCiudadanoView.as_view()
        self.url = '/api/peticiones/detectar-duplicados-ciudadano/'
        self.peticionario = _DummyUser(id_usuario=43, rol_id=4, activo=True)
        self.payload = {
            'descripcion': 'Perro herido frente al parque principal',
            'latitud': 2.444814,
            'longitud': -76.614739,
        }

    def tearDown(self):
        cache.clear()

    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_respuesta_vacia(self, mock_detector):
        mock_detector.return_value = _respuesta_interna([])
        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                'advertencia_duplicado': False,
                'posible_duplicado': False,
                'total_candidatos': 0,
                'mensaje': 'No se encontraron reportes similares.',
                'posibles_duplicados': [],
            },
        )

    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas', return_value={})
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_lista_blanca_exacta_y_ausencia_de_datos_sensibles(self, mock_detector, _mock_auth):
        mock_detector.return_value = _respuesta_interna([_candidato_interno()])
        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()),
            {'advertencia_duplicado', 'posible_duplicado', 'total_candidatos', 'mensaje', 'posibles_duplicados'},
        )
        self.assertTrue(response.data['advertencia_duplicado'])
        self.assertTrue(response.data['posible_duplicado'])
        self.assertEqual(response.data['total_candidatos'], 1)
        self.assertEqual(response.data['mensaje'], 'Se encontraron reportes similares.')

        candidato = response.data['posibles_duplicados'][0]
        self.assertEqual(
            set(candidato.keys()),
            {'coincidencia_texto', 'coincidencia_gps', 'cercania', 'similitud', 'foto', 'tiene_fotografia'},
        )

        serializado = json.dumps(response.data)
        for prohibido in (
            'id_peticion',
            'numero_radicado',
            'descripcion',
            'direccion',
            'latitud',
            'longitud',
            'distancia_metros',
            'porcentaje_similitud',
            'similitud_texto',
            'score_combinado',
            'estado',
            'ubicacion',
            'fotos',
            'parametros',
            'usuario',
            'asignacion',
            'PET-2026-00999',
            'Calle Falsa 123',
            'Descripcion confidencial',
            'Juan Perez',
            'Dra. Ana',
            'perro_secreto.jpg',
        ):
            self.assertNotIn(prohibido, serializado)

    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas', return_value={})
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_recorte_a_5_candidatos_y_orden_conservado(self, mock_detector, _mock_auth):
        candidatos = [
            _candidato_interno(id_peticion=1, distancia_metros=10.0, similitud_texto=0.90),
            _candidato_interno(id_peticion=2, distancia_metros=80.0, similitud_texto=0.70),
            _candidato_interno(id_peticion=3, distancia_metros=20.0, similitud_texto=0.40),
            _candidato_interno(id_peticion=4, distancia_metros=None, coincidencia_gps=False, similitud_texto=0.65),
            _candidato_interno(id_peticion=5, coincidencia_texto=False, distancia_metros=45.0, coincidencia_gps=True),
            _candidato_interno(id_peticion=6, distancia_metros=5.0, similitud_texto=0.99),
            _candidato_interno(id_peticion=7, distancia_metros=5.0, similitud_texto=0.99),
        ]
        mock_detector.return_value = _respuesta_interna(candidatos)

        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_candidatos'], 5)
        self.assertEqual(len(response.data['posibles_duplicados']), 5)
        self.assertEqual(
            [(c['cercania'], c['similitud']) for c in response.data['posibles_duplicados']],
            [
                ('muy_cerca', 'alta'),
                ('cerca', 'alta'),
                ('muy_cerca', 'media'),
                ('no_disponible', 'alta'),
                ('muy_cerca', 'no_disponible'),
            ],
        )

    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas', return_value={})
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_fronteras_categorias(self, mock_detector, _mock_auth):
        candidatos = [
            _candidato_interno(coincidencia_gps=True, distancia_metros=50.0, coincidencia_texto=True, similitud_texto=0.60),
            _candidato_interno(coincidencia_gps=True, distancia_metros=50.01, coincidencia_texto=True, similitud_texto=0.59),
            _candidato_interno(coincidencia_gps=True, distancia_metros=0.0, coincidencia_texto=False, similitud_texto=0.90),
            _candidato_interno(coincidencia_gps=False, distancia_metros=10.0, coincidencia_texto=True, similitud_texto=0.50),
        ]
        mock_detector.return_value = _respuesta_interna(candidatos)

        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        res = response.data['posibles_duplicados']
        self.assertEqual((res[0]['cercania'], res[0]['similitud']), ('muy_cerca', 'alta'))
        self.assertEqual((res[1]['cercania'], res[1]['similitud']), ('cerca', 'media'))
        self.assertEqual((res[2]['cercania'], res[2]['similitud']), ('muy_cerca', 'no_disponible'))
        self.assertEqual((res[3]['cercania'], res[3]['similitud']), ('no_disponible', 'media'))


class DetectarDuplicadosCiudadanoFotoTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosCiudadanoView.as_view()
        self.url = '/api/peticiones/detectar-duplicados-ciudadano/'
        self.peticionario = _DummyUser(id_usuario=44, rol_id=4, activo=True)
        self.payload = {
            'descripcion': 'Perro herido frente al parque principal',
            'latitud': 2.444814,
            'longitud': -76.614739,
        }

    def tearDown(self):
        cache.clear()

    @override_settings(DUPLICADOS_CIUDADANO_FOTO_ACTIVA=False)
    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas')
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_flag_apagada_oculta_foto_y_pasa_incluir_evidencias_false(self, mock_detector, mock_auth):
        url_orig = 'https://res.cloudinary.com/vet/image/upload/v1700/perro.jpg'
        mock_detector.return_value = _respuesta_interna([_candidato_interno(foto=url_orig, fotos=[url_orig])])

        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(mock_detector.call_args.kwargs['incluir_evidencias'])
        mock_auth.assert_not_called()
        cand = response.data['posibles_duplicados'][0]
        self.assertIsNone(cand['foto'])
        self.assertFalse(cand['tiene_fotografia'])
        self.assertNotIn(url_orig, json.dumps(response.data))

    @override_settings(DUPLICADOS_CIUDADANO_FOTO_ACTIVA=True)
    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas')
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_flag_encendida_genera_miniatura_y_descarta_urls_inseguras(self, mock_detector, mock_auth):
        url_valida = 'https://res.cloudinary.com/vet/image/upload/v1700/perro_valido.jpg'
        urls_invalidas = [
            'https://res.cloudinary.com/vet/video/upload/v1700/video.mp4',
            'https://res.cloudinary.com/vet/video/upload/v1700/foto.jpg',
            'http://res.cloudinary.com/vet/image/upload/v1700/perro.jpg',
            'https://evil.example.com/vet/image/upload/v1700/perro.jpg',
            'https://res.cloudinary.com/vet/image/upload/w_300/v1700/perro.jpg',
            'https://res.cloudinary.com/vet/image/upload/c_fill,w_300/v1700/perro.jpg',
            'https://res.cloudinary.com/vet/image/upload/e_blur_faces/c_fill,g_auto,w_480,h_360,q_auto,f_auto/v1700/perro.jpg',
            'https://res.cloudinary.com/vet/image/upload/s--firma--/v1700/perro.jpg',
            'https://res.cloudinary.com/vet/image/upload/v1700/perro.jpg?token=123',
            'https://res.cloudinary.com/vet/image/upload/v1700/perro.jpg#frag',
            'https://user:pass@res.cloudinary.com/vet/image/upload/v1700/perro.jpg',
        ]
        # Incluso si figuraran autorizadas en BD, las URLs inseguras o ya transformadas se descartan
        mock_auth.return_value = {999: set(urls_invalidas) | {url_valida}}
        candidatos = [_candidato_interno(foto=u, fotos=[u]) for u in urls_invalidas]
        candidato_con_fallback = _candidato_interno(foto=urls_invalidas[0], fotos=[urls_invalidas[1], url_valida])

        # Tandas de inválidas (máximo 5 por respuesta) -> todas None
        for idx in range(0, len(candidatos), 5):
            lote = candidatos[idx:idx + 5]
            mock_detector.return_value = _respuesta_interna(lote)
            request = self.factory.post(self.url, self.payload, format='json')
            force_authenticate(request, user=self.peticionario)
            resp_invalidas = self.view(request)
            self.assertEqual(resp_invalidas.status_code, status.HTTP_200_OK)
            self.assertTrue(mock_detector.call_args.kwargs['incluir_evidencias'])
            for c in resp_invalidas.data['posibles_duplicados']:
                self.assertIsNone(c['foto'])
                self.assertFalse(c['tiene_fotografia'])

        # Candidato con fallback válido autorizado en `fotos` -> miniatura derivada
        mock_detector.return_value = _respuesta_interna([candidato_con_fallback])
        request2 = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request2, user=self.peticionario)
        resp_valida = self.view(request2)
        self.assertEqual(resp_valida.status_code, status.HTTP_200_OK)
        ultimo = resp_valida.data['posibles_duplicados'][0]
        esperada = (
            'https://res.cloudinary.com/vet/image/upload/'
            'e_blur_faces/c_fill,g_auto,w_480,h_360,q_auto,f_auto/v1700/perro_valido.jpg'
        )
        self.assertEqual(ultimo['foto'], esperada)
        self.assertTrue(ultimo['tiene_fotografia'])
        serializado = json.dumps(resp_valida.data)
        self.assertNotIn(url_valida, serializado)
        self.assertNotIn('id_peticion', serializado)
        self.assertNotIn('999', serializado)

    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas', return_value={})
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_no_autorizada_retorna_foto_none_y_tiene_fotografia_false(self, mock_detector, mock_auth):
        url_orig = 'https://res.cloudinary.com/vet/image/upload/v1700/perro_no_aut.jpg'
        mock_detector.return_value = _respuesta_interna([_candidato_interno(foto=url_orig, fotos=[url_orig])])

        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_auth.assert_called_once_with([999])
        cand = response.data['posibles_duplicados'][0]
        self.assertIsNone(cand['foto'])
        self.assertFalse(cand['tiene_fotografia'])
        self.assertNotIn(url_orig, json.dumps(response.data))

    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas')
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_autorizada_para_otra_url_o_otra_peticion_retorna_none(self, mock_detector, mock_auth):
        url_candidato = 'https://res.cloudinary.com/vet/image/upload/v1700/perro_candidato.jpg'
        url_otra = 'https://res.cloudinary.com/vet/image/upload/v1700/perro_otra_url.jpg'
        mock_auth.return_value = {
            999: {url_otra},
            888: {url_candidato},
        }
        mock_detector.return_value = _respuesta_interna([_candidato_interno(id_peticion=999, foto=url_candidato, fotos=[url_candidato])])

        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cand = response.data['posibles_duplicados'][0]
        self.assertIsNone(cand['foto'])
        self.assertFalse(cand['tiene_fotografia'])
        self.assertNotIn(url_candidato, json.dumps(response.data))

    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas', side_effect=RuntimeError('DB caída'))
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_fallo_consulta_autorizaciones_retorna_none_sin_excepcion(self, mock_detector, _mock_auth):
        url_orig = 'https://res.cloudinary.com/vet/image/upload/v1700/perro_error.jpg'
        mock_detector.return_value = _respuesta_interna([_candidato_interno(foto=url_orig, fotos=[url_orig])])

        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=self.peticionario)
        with self.assertLogs('peticiones.services.duplicados_ciudadano_service', level='ERROR'):
            response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cand = response.data['posibles_duplicados'][0]
        self.assertIsNone(cand['foto'])
        self.assertFalse(cand['tiene_fotografia'])
        self.assertNotIn(url_orig, json.dumps(response.data))

    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas')
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_candidatos_con_foto_primero_conservando_orden_estable(self, mock_detector, mock_auth):
        url_no_aut_1 = 'https://res.cloudinary.com/vet/image/upload/v1700/sin_aut_1.jpg'
        url_aut_3 = 'https://res.cloudinary.com/vet/image/upload/v1700/con_aut_3.jpg'
        url_no_aut_4 = 'https://res.cloudinary.com/vet/image/upload/v1700/sin_aut_4.jpg'
        esperada_3 = (
            'https://res.cloudinary.com/vet/image/upload/'
            'e_blur_faces/c_fill,g_auto,w_480,h_360,q_auto,f_auto/v1700/con_aut_3.jpg'
        )
        mock_auth.return_value = {103: {url_aut_3}}

        candidatos = [
            _candidato_interno(
                id_peticion=101,
                coincidencia_gps=True,
                distancia_metros=10.0,
                coincidencia_texto=True,
                similitud_texto=0.90,
                foto=url_no_aut_1,
                fotos=[url_no_aut_1],
            ),
            _candidato_interno(
                id_peticion=102,
                coincidencia_gps=True,
                distancia_metros=80.0,
                coincidencia_texto=True,
                similitud_texto=0.70,
                foto=None,
                fotos=[],
            ),
            _candidato_interno(
                id_peticion=103,
                coincidencia_gps=True,
                distancia_metros=80.0,
                coincidencia_texto=True,
                similitud_texto=0.40,
                foto=url_aut_3,
                fotos=[url_aut_3],
            ),
            _candidato_interno(
                id_peticion=104,
                coincidencia_gps=False,
                distancia_metros=None,
                coincidencia_texto=True,
                similitud_texto=0.45,
                foto=url_no_aut_4,
                fotos=[url_no_aut_4],
            ),
        ]
        mock_detector.return_value = _respuesta_interna(candidatos)

        request = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(request, user=self.peticionario)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_candidatos'], 4)
        mock_auth.assert_called_once_with([101, 102, 103, 104])

        res = response.data['posibles_duplicados']
        self.assertEqual(
            [(c['cercania'], c['similitud'], c['tiene_fotografia']) for c in res],
            [
                ('cerca', 'media', True),
                ('muy_cerca', 'alta', False),
                ('cerca', 'alta', False),
                ('no_disponible', 'media', False),
            ],
        )
        self.assertEqual(res[0]['foto'], esperada_3)
        self.assertIsNone(res[1]['foto'])
        self.assertIsNone(res[2]['foto'])
        self.assertIsNone(res[3]['foto'])


class DetectarDuplicadosCiudadanoThrottleSinEscriturasYCompatTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.view = DetectarDuplicadosCiudadanoView.as_view()
        self.url = '/api/peticiones/detectar-duplicados-ciudadano/'
        self.peticionario = _DummyUser(id_usuario=45, rol_id=4, activo=True)
        self.payload = {
            'descripcion': 'Perro herido frente al parque principal',
            'latitud': 2.444814,
            'longitud': -76.614739,
        }

    def tearDown(self):
        cache.clear()

    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_throttling_rafaga_retorna_429(self, mock_detector):
        mock_detector.return_value = _respuesta_interna([])
        with patch.dict(
            DuplicadosCiudadanoRafagaThrottle.THROTTLE_RATES,
            {'duplicados_ciudadano_rafaga': '2/min', 'duplicados_ciudadano_sostenido': '60/hour'},
        ):
            for _ in range(2):
                req = self.factory.post(self.url, self.payload, format='json')
                force_authenticate(req, user=self.peticionario)
                resp = self.view(req)
                self.assertEqual(resp.status_code, status.HTTP_200_OK)

            req_excedido = self.factory.post(self.url, self.payload, format='json')
            force_authenticate(req_excedido, user=self.peticionario)
            resp_429 = self.view(req_excedido)
            self.assertEqual(resp_429.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_throttling_sostenido_retorna_429(self, mock_detector):
        mock_detector.return_value = _respuesta_interna([])
        with patch.dict(
            DuplicadosCiudadanoSostenidoThrottle.THROTTLE_RATES,
            {'duplicados_ciudadano_rafaga': '10/min', 'duplicados_ciudadano_sostenido': '2/hour'},
        ):
            for _ in range(2):
                req = self.factory.post(self.url, self.payload, format='json')
                force_authenticate(req, user=self.peticionario)
                resp = self.view(req)
                self.assertEqual(resp.status_code, status.HTTP_200_OK)

            req_excedido = self.factory.post(self.url, self.payload, format='json')
            force_authenticate(req_excedido, user=self.peticionario)
            resp_429 = self.view(req_excedido)
            self.assertEqual(resp_429.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @patch('django.db.models.query.QuerySet.bulk_create')
    @patch('django.db.models.query.QuerySet.create')
    @patch('django.db.models.base.Model.save')
    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas', return_value={})
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_no_realiza_escrituras_en_bd(self, mock_detector, _mock_auth, mock_save, mock_create, mock_bulk_create):
        mock_detector.return_value = _respuesta_interna([_candidato_interno()])
        req = self.factory.post(self.url, self.payload, format='json')
        force_authenticate(req, user=self.peticionario)
        resp = self.view(req)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_save.assert_not_called()
        mock_create.assert_not_called()
        mock_bulk_create.assert_not_called()

    def test_compatibilidad_endpoint_interno_y_rutas(self):
        self.assertEqual(
            DetectarDuplicadosPeticionView.permission_classes,
            [IsAuthenticated, IsJuridicoOrAdmin],
        )
        self.assertEqual(getattr(DetectarDuplicadosPeticionView, 'throttle_classes', []), [])

        url_ciudadano = reverse('detectar-duplicados-ciudadano-peticion')
        url_interno = reverse('detectar-duplicados-peticion')
        self.assertEqual(url_ciudadano, '/api/peticiones/detectar-duplicados-ciudadano/')
        self.assertEqual(url_interno, '/api/peticiones/detectar-duplicados/')
        self.assertEqual(resolve(url_ciudadano).func.view_class, DetectarDuplicadosCiudadanoView)
        self.assertEqual(resolve(url_interno).func.view_class, DetectarDuplicadosPeticionView)
