"""
Pruebas unitarias para la integración de DINOv2 Small en la detección de posibles
reportes duplicados y su respaldo perceptual/textual sin alterar el registro de peticiones.
"""

import io
import math
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from peticiones.api.duplicados_ciudadano import DetectarDuplicadosCiudadanoView
from peticiones.api.views import DetectarDuplicadosPeticionView, IniciarPeticionView
from peticiones.models import EstadoPeticiones, Peticiones, TipoPeticion, Ubicaciones
from peticiones.services import dinov2_service, duplicados_service, imagen_hash_service
from peticiones.services.alertas_duplicados_service import detectar_y_registrar_duplicados_al_iniciar


class _DummyUser:
    def __init__(self, uid: int, rol_id: int):
        self.id_usuario = uid
        self.pk = uid
        self.id_rol_id = rol_id
        self.id_rol = MagicMock(id_rol=rol_id)
        self.activo = True
        self.is_authenticated = True
        self.is_anonymous = False
        self.nombre = 'Usuario'
        self.apellido = 'Prueba'
        self.email = f'user{uid}@cba.gov.co'


class _FakeTensor3D:
    """Tensor mínimo sin dependencia de torch para pruebas aisladas de embeddings CLS."""

    def __init__(self, vec384):
        self._data = list(vec384)
        self.ndim = 3

    def __getitem__(self, item):
        if isinstance(item, tuple) and len(item) == 3:
            return _FakeTensor2D(self._data)
        return _FakeTensor2D(self._data)


class _FakeTensor2D:
    def __init__(self, vec384):
        self._data = list(vec384)
        self.ndim = 2

    def __getitem__(self, idx):
        return _FakeTensor1D(self._data)


class _FakeTensor1D:
    def __init__(self, vec384):
        self._data = list(vec384)
        self.ndim = 1

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return list(self._data)


class _FakeNoGrad:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def _crear_bytes_imagen(fmt='JPEG', size=(64, 64), color=(180, 120, 60)) -> bytes:
    buf = io.BytesIO()
    img = Image.new('RGB', size, color=color)
    img.save(buf, format=fmt)
    return buf.getvalue()


def _vector_unitario(indice_activo: int, peso_secundario: float = 0.0, indice_secundario: int = 1) -> list:
    vec = [0.0] * 384
    vec[indice_activo] = 1.0
    if peso_secundario:
        vec[indice_secundario] = float(peso_secundario)
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec]


def _peticion_falsa(
    *,
    id_peticion: int,
    descripcion: str,
    latitud: float = None,
    longitud: float = None,
    foto: str = None,
    similitud_texto: float = 0.0,
):
    ubicacion = None
    if latitud is not None and longitud is not None:
        ubicacion = Ubicaciones(
            id_ubicacion=id_peticion + 1000,
            direccion=f'Carrera {id_peticion}',
            latitud=Decimal(str(latitud)),
            longitud=Decimal(str(longitud)),
        )
    pet = Peticiones(
        id_peticion=id_peticion,
        numero_radicado=f'PET-2026-{id_peticion:05d}',
        id_ubicacion=ubicacion,
        id_estado=EstadoPeticiones(id_estado=1, nombre='En revisión'),
        descripcion=descripcion,
        prioridad='MEDIA',
        foto=foto,
        fecha=datetime(2026, 11, 10, 12, 0, tzinfo=dt_timezone.utc),
    )
    pet.similitud = similitud_texto
    pet._prefetched_objects_cache = {'evidenciapeticiones_set': []}
    return pet


def _mock_queryset(resultados):
    qs = MagicMock()
    qs.select_related.return_value = qs
    qs.prefetch_related.return_value = qs
    qs.filter.return_value = qs
    qs.exclude.return_value = qs
    qs.annotate.return_value = qs
    qs.distinct.return_value = qs
    qs.order_by.return_value = qs
    qs.__getitem__.side_effect = lambda sl: resultados[: sl.stop] if isinstance(sl, slice) else resultados[sl]
    qs.__iter__.side_effect = lambda: iter(resultados)
    return qs


class Dinov2ConfiguracionYDesactivadoTests(SimpleTestCase):
    def test_bandera_dinov2_desactivada_por_defecto_y_radio_200m(self):
        cfg_dinov2 = dinov2_service.obtener_config_dinov2()
        cfg_dup = duplicados_service.obtener_config_duplicados()

        self.assertFalse(cfg_dinov2['ia_activa'])
        self.assertFalse(dinov2_service.es_dinov2_activo())
        self.assertEqual(cfg_dinov2['model_name'], 'facebook/dinov2-small')
        self.assertEqual(cfg_dinov2['dimension'], 384)
        self.assertEqual(cfg_dinov2['umbral_dinov2'], 0.45)
        self.assertEqual(cfg_dup['radio_metros'], 200.0)
        self.assertTrue(cfg_dinov2['fallback_dhash'])

    @override_settings(DUPLICADOS_IA_DINOV2_ACTIVA=False)
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_solo_foto_sin_texto_ni_gps_no_activa_dinov2_cuando_bandera_apagada(self, mock_objects):
        res = duplicados_service.buscar_peticiones_similares_por_descripcion(
            'corto',
            foto='https://res.cloudinary.com/demo/image/upload/v1/perro.jpg',
        )
        self.assertFalse(res['posible_duplicado'])
        self.assertEqual(res['total_candidatos'], 0)
        self.assertFalse(res['parametros']['imagen_activa'])
        self.assertNotIn('dinov2_activo', res['parametros'])
        mock_objects.select_related.assert_not_called()


class Dinov2EmbeddingsYSimilitudCosenoTests(SimpleTestCase):
    def tearDown(self):
        dinov2_service.limpiar_cache_modelo_dinov2()

    @override_settings(DUPLICADOS_DINOV2_BACKEND='local')
    @patch('peticiones.services.dinov2_service._cargar_modelo_dinov2')
    def test_extraccion_embedding_384_dimensiones_y_similitud_coseno(self, mock_cargar):
        vec_ref = _vector_unitario(0, peso_secundario=0.25, indice_secundario=2)
        fake_processor = MagicMock(return_value={'pixel_values': MagicMock()})
        fake_model = MagicMock(return_value=SimpleNamespace(last_hidden_state=_FakeTensor3D(vec_ref)))
        fake_torch = SimpleNamespace(no_grad=_FakeNoGrad)
        mock_cargar.return_value = (fake_processor, fake_model, fake_torch, 'cpu')

        img_bytes = _crear_bytes_imagen('JPEG', (64, 64))
        emb = dinov2_service.extraer_embedding_dinov2_desde_bytes(img_bytes)

        self.assertIsNotNone(emb)
        self.assertEqual(len(emb), 384)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in emb)), 1.0, places=5)
        mock_cargar.assert_called_once()

    def test_comparar_imagenes_similares_vs_diferentes_declara_no_identidad(self):
        emb_perro_a = _vector_unitario(10)
        # Similitud ~0.5070 (50.7%) supera el umbral calibrado 0.45 (45%)
        emb_perro_a_otro_angulo = _vector_unitario(10, peso_secundario=1.7, indice_secundario=11)
        # Similitud ~0.2425 (24.25%) queda por debajo de 0.45
        emb_animal_diferente = _vector_unitario(10, peso_secundario=4.0, indice_secundario=99)

        comp_sim = dinov2_service.comparar_conjuntos_embeddings(
            [emb_perro_a],
            [emb_perro_a_otro_angulo],
            umbral_coseno=0.45,
        )
        self.assertTrue(comp_sim['comparado'])
        self.assertTrue(comp_sim['coincidencia_imagen'])
        self.assertGreater(comp_sim['porcentaje_similitud_visual'], 45.0)
        self.assertLess(comp_sim['porcentaje_similitud_visual'], 60.0)
        self.assertFalse(comp_sim['es_certeza_identidad'])
        self.assertEqual(comp_sim['tipo_metrica_visual'], 'similitud_visual_coseno_no_identidad')
        self.assertIn('no constituye certeza', comp_sim['advertencia_metrica_visual'].lower())

        comp_diff = dinov2_service.comparar_conjuntos_embeddings(
            [emb_perro_a],
            [emb_animal_diferente],
            umbral_coseno=0.45,
        )
        self.assertTrue(comp_diff['comparado'])
        self.assertFalse(comp_diff['coincidencia_imagen'])
        self.assertLess(comp_diff['porcentaje_similitud_visual'], 45.0)
        self.assertFalse(comp_diff['es_certeza_identidad'])

    @override_settings(DUPLICADOS_DINOV2_CACHE_BD_ACTIVA=True)
    @patch('peticiones.services.imagen_hash_service.descargar_imagen_segura')
    @patch('peticiones.models.PeticionImagenEmbedding.objects')
    def test_cache_bd_embeddings_reutiliza_registro_sin_descargar_cloudinary(
        self,
        mock_emb_objects,
        mock_descarga,
    ):
        vec_guardado = _vector_unitario(7)
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.order_by.return_value = qs
        qs.first.return_value = SimpleNamespace(embedding_vector=vec_guardado)
        mock_emb_objects.filter.return_value = qs

        url = 'https://res.cloudinary.com/demo/image/upload/v1/cache_bd.jpg'
        emb = dinov2_service.obtener_embedding_desde_url(url, id_peticion=55)

        self.assertIsNotNone(emb)
        self.assertEqual(len(emb), 384)
        mock_emb_objects.filter.assert_called_once_with(
            url_imagen=url,
            modelo_ia='facebook/dinov2-small',
        )
        qs.filter.assert_called_once_with(peticion_id=55)
        qs.order_by.assert_called_once_with('-fecha_creacion')
        mock_descarga.assert_not_called()


class Dinov2ValidacionCloudinaryYErroresTests(SimpleTestCase):
    def test_url_cloudinary_construye_inferencia_sin_alterar_firmadas(self):
        url_orig = 'https://res.cloudinary.com/vetericano/image/upload/v17000/mascota.jpg'
        url_inf = imagen_hash_service.construir_url_inferencia_cloudinary(url_orig, ancho=256, alto=256)
        self.assertIn('/image/upload/w_256,h_256,c_limit,f_jpg/', url_inf)

        url_firmada = 'https://res.cloudinary.com/vetericano/image/upload/s--ABC123--/v1/mascota.jpg'
        self.assertEqual(
            imagen_hash_service.construir_url_inferencia_cloudinary(url_firmada, ancho=256, alto=256),
            url_firmada,
        )

    def test_decodificar_y_validar_imagen_rechaza_formatos_tamano_y_dimension_invalidos(self):
        self.assertIsNone(imagen_hash_service.decodificar_y_validar_imagen_bytes(b''))
        self.assertIsNone(imagen_hash_service.decodificar_y_validar_imagen_bytes(b'<html>404</html>'))

        bmp_bytes = _crear_bytes_imagen('BMP', (64, 64))
        self.assertIsNone(imagen_hash_service.decodificar_y_validar_imagen_bytes(bmp_bytes))

        tiny_png = _crear_bytes_imagen('PNG', (8, 8))
        self.assertIsNone(imagen_hash_service.decodificar_y_validar_imagen_bytes(tiny_png))

        ok_png = _crear_bytes_imagen('PNG', (64, 64))
        self.assertIsNone(imagen_hash_service.decodificar_y_validar_imagen_bytes(ok_png, max_bytes=32))
        img_ok = imagen_hash_service.decodificar_y_validar_imagen_bytes(ok_png, max_bytes=100_000)
        self.assertIsNotNone(img_ok)
        self.assertEqual(img_ok.mode, 'RGB')

    @patch('peticiones.services.imagen_hash_service.descargar_imagen_segura', return_value=None)
    def test_urls_invalidas_o_error_descarga_cloudinary_no_bloquean(self, mock_descarga):
        self.assertIsNone(dinov2_service.obtener_embedding_desde_url('http://res.cloudinary.com/demo/image/upload/a.jpg'))
        self.assertIsNone(dinov2_service.obtener_embedding_desde_url('https://evil.com/image/upload/a.jpg'))
        self.assertIsNone(
            dinov2_service.obtener_embedding_desde_url(
                'https://res.cloudinary.com/demo/video/upload/v1/clip.mp4'
            )
        )
        self.assertIsNone(
            dinov2_service.obtener_embedding_desde_url(
                'https://res.cloudinary.com/demo/image/upload/v1/timeout.jpg'
            )
        )
        mock_descarga.assert_called_once()


class Dinov2PrioridadImagenGpsYRespaldoTests(SimpleTestCase):
    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
    )
    @patch('peticiones.services.dinov2_service.calcular_embeddings_urls')
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_prioriza_imagen_y_gps_200m_con_descripcion_complementaria(
        self,
        mock_objects,
        mock_calc_emb,
    ):
        url_origen = 'https://res.cloudinary.com/demo/image/upload/v1/perro_origen.jpg'
        url_mismo = 'https://res.cloudinary.com/demo/image/upload/v1/perro_mismo.jpg'
        url_otro = 'https://res.cloudinary.com/demo/image/upload/v1/gato_otro.jpg'

        cand_visual_gps = _peticion_falsa(
            id_peticion=101,
            descripcion='Ayuda urgente barrio centro',
            latitud=2.444900,
            longitud=-76.614739,
            foto=url_mismo,
            similitud_texto=0.05,
        )
        cand_solo_texto_gps = _peticion_falsa(
            id_peticion=102,
            descripcion='Cachorro cafe herido frente al parque central de Popayan',
            latitud=2.444820,
            longitud=-76.614740,
            foto=url_otro,
            similitud_texto=0.92,
        )
        cand_fuera_200m = _peticion_falsa(
            id_peticion=103,
            descripcion='Cachorro cafe herido frente al parque central de Popayan',
            latitud=2.455000,  # ~1.1 km (> 200 m)
            longitud=-76.614739,
            foto=url_mismo,
            similitud_texto=0.95,
        )

        qs_gps = _mock_queryset([cand_visual_gps, cand_solo_texto_gps, cand_fuera_200m])
        mock_objects.select_related.return_value = qs_gps

        emb_perro = _vector_unitario(5)
        emb_perro_otro_angulo = _vector_unitario(5, peso_secundario=1.7, indice_secundario=6)  # ~50.7% (>= 45%)
        emb_otro_animal = _vector_unitario(5, peso_secundario=4.0, indice_secundario=88)       # ~24.25% (< 45%)
        mapa = {
            url_origen: [emb_perro],
            url_mismo: [emb_perro_otro_angulo],
            url_otro: [emb_otro_animal],
        }
        mock_calc_emb.side_effect = lambda urls, **kw: mapa[list(urls)[0]]

        res = duplicados_service.buscar_peticiones_similares_por_descripcion(
            'Cachorro cafe herido frente al parque central de Popayan',
            latitud=2.444814,
            longitud=-76.614739,
            foto=url_origen,
        )

        self.assertTrue(res['posible_duplicado'])
        self.assertTrue(res['advertencia_duplicado'])
        self.assertTrue(res['parametros']['dinov2_activo'])
        self.assertEqual(res['parametros']['umbral_dinov2'], 0.45)
        self.assertEqual(res['parametros']['metodo_imagen'], 'dinov2_small')
        self.assertEqual(res['parametros']['radio_metros'], 200.0)
        # Tanto #101 (visual + GPS <= 200 m) como #102 (solo GPS <= 200 m) se incluyen;
        # #103 (fuera de 200 m con foto idéntica) queda excluido por regla GPS obligatoria.
        self.assertEqual(res['total_candidatos'], 2)
        mock_objects.select_related.assert_called_once()

        primero, segundo = res['posibles_duplicados']
        self.assertEqual(primero['id_peticion'], 101)
        self.assertTrue(primero['posible_duplicado'])
        self.assertTrue(primero['coincidencia_imagen'])
        self.assertTrue(primero['coincidencia_gps'])
        self.assertGreater(primero['porcentaje_similitud_visual'], 45.0)
        self.assertFalse(primero['es_certeza_identidad'])
        self.assertEqual(primero['metodo_imagen'], 'dinov2_small')

        self.assertEqual(segundo['id_peticion'], 102)
        self.assertTrue(segundo['posible_duplicado'])
        self.assertTrue(segundo['coincidencia_gps'])
        self.assertFalse(segundo['coincidencia_imagen'])

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
    )
    @patch('peticiones.services.dinov2_service.calcular_embeddings_urls')
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_exige_imagen_y_gps_200m_no_activa_solo_texto_gps_ni_imagen_sola(
        self,
        mock_objects,
        mock_calc_emb,
    ):
        url_origen = 'https://res.cloudinary.com/demo/image/upload/v1/foto_consulta.jpg'
        url_similar = 'https://res.cloudinary.com/demo/image/upload/v1/foto_similar.jpg'
        url_distinta = 'https://res.cloudinary.com/demo/image/upload/v1/foto_distinta.jpg'

        # 1) Solo imagen sin GPS -> no consulta BD ni activa alerta
        res_sin_gps = duplicados_service.buscar_peticiones_similares_por_descripcion(
            'Perro cafe herido cerca al parque central',
            foto=url_origen,
        )
        self.assertFalse(res_sin_gps['posible_duplicado'])
        self.assertFalse(res_sin_gps['advertencia_duplicado'])
        self.assertEqual(res_sin_gps['total_candidatos'], 0)
        mock_objects.select_related.assert_not_called()

        # 2) Texto + GPS sin fotografía -> sí advierte cuando hay candidatos en GPS <= 200 m
        cand_sin_foto_en_gps = _peticion_falsa(
            id_peticion=201,
            descripcion='Perro cafe herido cerca al parque central',
            latitud=2.444820,
            longitud=-76.614740,
            similitud_texto=0.95,
        )
        mock_objects.select_related.return_value = _mock_queryset([cand_sin_foto_en_gps])
        res_sin_foto = duplicados_service.buscar_peticiones_similares_por_descripcion(
            'Perro cafe herido cerca al parque central',
            latitud=2.444814,
            longitud=-76.614739,
        )
        self.assertTrue(res_sin_foto['posible_duplicado'])
        self.assertTrue(res_sin_foto['advertencia_duplicado'])
        self.assertEqual(res_sin_foto['total_candidatos'], 1)
        self.assertIsNone(res_sin_foto['posibles_duplicados'][0]['porcentaje_similitud_visual'])

        # 3) GPS <= 200m + descripción idéntica, pero animal distinto (< 45%) -> alerta GPS con info visual
        cand_distinta_en_gps = _peticion_falsa(
            id_peticion=202,
            descripcion='Perro cafe herido cerca al parque central',
            latitud=2.444820,
            longitud=-76.614740,
            foto=url_distinta,
            similitud_texto=0.98,
        )
        mock_objects.select_related.return_value = _mock_queryset([cand_distinta_en_gps])
        mapa = {
            url_origen: [_vector_unitario(1)],
            url_similar: [_vector_unitario(1, peso_secundario=1.7, indice_secundario=2)],
            url_distinta: [_vector_unitario(1, peso_secundario=4.0, indice_secundario=200)],
        }
        mock_calc_emb.side_effect = lambda urls, **kw: mapa[list(urls)[0]]

        res_gps_sin_visual = duplicados_service.buscar_peticiones_similares_por_descripcion(
            'Perro cafe herido cerca al parque central',
            latitud=2.444814,
            longitud=-76.614739,
            foto=url_origen,
        )
        self.assertTrue(res_gps_sin_visual['posible_duplicado'])
        self.assertTrue(res_gps_sin_visual['advertencia_duplicado'])
        self.assertEqual(res_gps_sin_visual['total_candidatos'], 1)
        self.assertTrue(res_gps_sin_visual['posibles_duplicados'][0]['coincidencia_gps'])
        self.assertFalse(res_gps_sin_visual['posibles_duplicados'][0]['coincidencia_imagen'])

        # 4) Mismo animal (> 45%) pero fuera del radio de 200m -> sin alerta
        cand_lejos = _peticion_falsa(
            id_peticion=203,
            descripcion='Perro cafe herido cerca al parque central',
            latitud=2.455000,
            longitud=-76.614739,
            foto=url_similar,
            similitud_texto=0.98,
        )
        mock_objects.select_related.return_value = _mock_queryset([cand_lejos])
        res_visual_sin_gps = duplicados_service.buscar_peticiones_similares_por_descripcion(
            'Perro cafe herido cerca al parque central',
            latitud=2.444814,
            longitud=-76.614739,
            foto=url_origen,
        )
        self.assertFalse(res_visual_sin_gps['posible_duplicado'])
        self.assertFalse(res_visual_sin_gps['advertencia_duplicado'])
        self.assertEqual(res_visual_sin_gps['total_candidatos'], 0)

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_DINOV2_FALLBACK_DHASH=True,
    )
    @patch('peticiones.services.duplicados_service.calcular_hashes_urls')
    @patch(
        'peticiones.services.dinov2_service.calcular_embeddings_urls',
        side_effect=dinov2_service.ModelDinov2NoDisponibleError('torch no disponible'),
    )
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_respaldo_dhash_automatico_si_falla_dinov2(
        self,
        mock_objects,
        _mock_dinov2,
        mock_dhash,
    ):
        url_origen = 'https://res.cloudinary.com/demo/image/upload/v1/origen.jpg'
        url_cand = 'https://res.cloudinary.com/demo/image/upload/v1/cand.jpg'
        cand = _peticion_falsa(
            id_peticion=301,
            descripcion='Reporte antiguo cercano',
            latitud=2.444820,
            longitud=-76.614739,
            foto=url_cand,
        )
        mock_objects.select_related.return_value = _mock_queryset([cand])
        mock_dhash.side_effect = [[0xFFFF0000FFFF0000], [0xFFFF0000FFFF0001]]

        res = duplicados_service.buscar_peticiones_similares_por_descripcion(
            'corto',
            latitud=2.444814,
            longitud=-76.614739,
            foto=url_origen,
        )
        self.assertTrue(res['posible_duplicado'])
        self.assertTrue(res['parametros']['imagen_activa'])
        self.assertEqual(res['parametros']['metodo_imagen'], 'dhash_fallback')
        self.assertTrue(res['posibles_duplicados'][0]['coincidencia_imagen'])
        self.assertEqual(res['posibles_duplicados'][0]['distancia_hamming'], 1)
        self.assertEqual(res['posibles_duplicados'][0]['metodo_imagen'], 'dhash_fallback')


class Dinov2EndpointCiudadanoYJuridicoTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.ciudadano = _DummyUser(42, 4)
        self.juridico = _DummyUser(20, 2)

    @override_settings(DUPLICADOS_IA_DINOV2_ACTIVA=True, DUPLICADOS_CIUDADANO_FOTO_ACTIVA=True)
    @patch('peticiones.services.duplicados_ciudadano_service.obtener_urls_autorizadas')
    @patch('peticiones.services.duplicados_ciudadano_service.detectar_duplicados_desde_solicitud')
    def test_ciudadano_endpoint_devuelve_metricas_visuales_gps_y_solo_foto_autorizada(
        self,
        mock_detector,
        mock_autorizadas,
    ):
        url_aut = 'https://res.cloudinary.com/demo/image/upload/v1/autorizada.jpg'
        url_priv = 'https://res.cloudinary.com/demo/image/upload/v1/privada.jpg'
        mock_autorizadas.return_value = {10: {url_aut}}
        mock_detector.return_value = {
            'advertencia_duplicado': True,
            'posible_duplicado': True,
            'total_candidatos': 2,
            'posibles_duplicados': [
                {
                    'id_peticion': 10,
                    'numero_radicado': 'PET-SECRETO-10',
                    'descripcion': 'Confidencial ciudadano 10',
                    'coincidencia_texto': False,
                    'similitud_texto': 0.1,
                    'coincidencia_gps': True,
                    'distancia_metros': 25.0,
                    'coincidencia_imagen': True,
                    'similitud_imagen': 0.91,
                    'porcentaje_similitud_visual': 91.0,
                    'metodo_imagen': 'dinov2_small',
                    'es_certeza_identidad': False,
                    'tipo_metrica_visual': dinov2_service.TIPO_METRICA_VISUAL,
                    'advertencia_metrica_visual': dinov2_service.ADVERTENCIA_METRICA_VISUAL_DEFAULT,
                    'foto': url_aut,
                    'fotos': [url_aut],
                },
                {
                    'id_peticion': 11,
                    'numero_radicado': 'PET-SECRETO-11',
                    'descripcion': 'Confidencial ciudadano 11',
                    'coincidencia_texto': False,
                    'similitud_texto': 0.0,
                    'coincidencia_gps': True,
                    'distancia_metros': 120.0,
                    'coincidencia_imagen': True,
                    'similitud_imagen': 0.5065,
                    'porcentaje_similitud_visual': 50.65,
                    'metodo_imagen': 'dinov2_small',
                    'es_certeza_identidad': False,
                    'tipo_metrica_visual': dinov2_service.TIPO_METRICA_VISUAL,
                    'advertencia_metrica_visual': dinov2_service.ADVERTENCIA_METRICA_VISUAL_DEFAULT,
                    'foto': url_priv,
                    'fotos': [url_priv],
                },
                {
                    'id_peticion': 12,
                    'numero_radicado': 'PET-SECRETO-12',
                    'descripcion': 'Solo GPS sin coincidencia visual debe filtrarse',
                    'coincidencia_texto': True,
                    'similitud_texto': 0.90,
                    'coincidencia_gps': True,
                    'distancia_metros': 10.0,
                    'coincidencia_imagen': False,
                    'similitud_imagen': 0.23,
                    'porcentaje_similitud_visual': 23.0,
                    'metodo_imagen': 'dinov2_small',
                    'es_certeza_identidad': False,
                    'foto': url_aut,
                    'fotos': [url_aut],
                },
            ],
        }

        payload = {
            'descripcion': '',
            'latitud': 2.444814,
            'longitud': -76.614739,
            'foto': 'https://res.cloudinary.com/demo/image/upload/v1/nueva.jpg',
        }
        request = self.factory.post('/api/peticiones/detectar-duplicados-ciudadano/', payload, format='json')
        force_authenticate(request, user=self.ciudadano)
        response = DetectarDuplicadosCiudadanoView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['posible_duplicado'])
        self.assertEqual(response.data['total_candidatos'], 3)

        c0, c1, c2 = response.data['posibles_duplicados']
        self.assertEqual(c0['cercania'], 'muy_cerca')
        self.assertEqual(c0['porcentaje_similitud_visual'], 91.0)
        self.assertEqual(c0['similitud_visual'], 'alta')
        self.assertFalse(c0['es_certeza_identidad'])
        self.assertIn('e_blur_faces/', c0['foto'])
        self.assertTrue(c0['tiene_fotografia'])

        self.assertEqual(c1['cercania'], 'cerca')
        self.assertEqual(c1['porcentaje_similitud_visual'], 50.65)
        self.assertEqual(c1['similitud_visual'], 'media')
        self.assertIsNone(c1['foto'])
        self.assertFalse(c1['tiene_fotografia'])

        self.assertEqual(c2['cercania'], 'muy_cerca')
        self.assertTrue(c2['coincidencia_gps'])
        self.assertFalse(c2['coincidencia_imagen'])
        self.assertIsNone(c2['foto'])
        self.assertFalse(c2['tiene_fotografia'])

        for campo_privado in ('id_peticion', 'numero_radicado', 'descripcion', 'distancia_metros', 'ubicacion'):
            self.assertNotIn(campo_privado, c0)
            self.assertNotIn(campo_privado, c1)

    @patch('peticiones.api.views.detectar_duplicados_desde_solicitud')
    def test_endpoint_interno_reenvia_usar_dinov2_y_umbral_cuando_se_envian(self, mock_detector):
        mock_detector.return_value = {
            'advertencia_duplicado': False,
            'posible_duplicado': False,
            'total_candidatos': 0,
            'posibles_duplicados': [],
            'mensaje': 'Sin duplicados',
            'parametros': {},
        }
        request = self.factory.post(
            '/api/peticiones/detectar-duplicados/',
            {
                'foto': 'https://res.cloudinary.com/demo/image/upload/v1/test.jpg',
                'usar_dinov2': True,
                'umbral_dinov2': 0.82,
            },
            format='json',
        )
        force_authenticate(request, user=self.juridico)
        response = DetectarDuplicadosPeticionView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(mock_detector.call_args.kwargs['usar_dinov2'])
        self.assertEqual(mock_detector.call_args.kwargs['umbral_dinov2'], 0.82)


class Dinov2ResilienciaRegistroPeticionesTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.ciudadano = _DummyUser(10, 4)

    @override_settings(
        DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True,
        DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS=0,
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
    )
    @patch('peticiones.services.alertas_duplicados_service.transaction.atomic', return_value=_FakeNoGrad())
    @patch(
        'peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion',
        side_effect=dinov2_service.ModelDinov2NoDisponibleError('Fallo catastrófico DINOv2'),
    )
    def test_fallo_modelo_no_lanza_excepcion_en_deteccion_al_iniciar(self, _mock_buscar, _mock_atomic):
        pet = _peticion_falsa(
            id_peticion=900,
            descripcion='Perro abandonado con herida en la pata derecha',
            latitud=2.444814,
            longitud=-76.614739,
            foto='https://res.cloudinary.com/demo/image/upload/v1/pet.jpg',
        )
        pet._state.adding = False
        resultado = detectar_y_registrar_duplicados_al_iniciar(pet)
        self.assertFalse(resultado.ejecutada)
        self.assertIn('Fallo catastrófico DINOv2', resultado.error or '')

    @override_settings(
        DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True,
        DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS=0,
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
    )
    @patch('peticiones.services.alertas_duplicados_service.transaction.atomic', return_value=_FakeNoGrad())
    @patch(
        'peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion',
        side_effect=dinov2_service.ModelDinov2NoDisponibleError('DINOv2 caído en producción'),
    )
    @patch('peticiones.api.views.transaction.on_commit')
    @patch('peticiones.api.views.enviar_correo_peticion_urgente_juridico', return_value={'enviado': False})
    @patch('peticiones.api.views.crear_notificaciones_nueva_peticion')
    @patch('peticiones.api.views.registrar_historial_peticion')
    @patch('peticiones.api.views._obtener_estado_por_nombre')
    def test_iniciar_peticion_sigue_respondiendo_201_si_falla_ia(
        self,
        mock_estado,
        _mock_hist,
        _mock_notif,
        _mock_correo,
        mock_on_commit,
        mock_buscar,
        _mock_atomic,
    ):
        estado = EstadoPeticiones(id_estado=1, nombre='En revisión')
        mock_estado.return_value = estado
        pet_guardada = _peticion_falsa(
            id_peticion=777,
            descripcion='Animal en riesgo requiere rescate',
            latitud=2.444814,
            longitud=-76.614739,
            foto='https://res.cloudinary.com/demo/image/upload/v1/pet.jpg',
        )
        pet_guardada._state.adding = False
        pet_guardada.id_tipo = TipoPeticion(id_tipo=1, nombre='Maltrato')
        mock_on_commit.side_effect = lambda fn: fn()

        with patch.object(
            Peticiones,
            'evidenciapeticiones_set',
            new_callable=PropertyMock,
        ) as mock_ev_prop, patch.object(
            IniciarPeticionView,
            'get_serializer',
        ) as mock_get_ser:
            mock_ev_set = MagicMock()
            mock_ev_set.values_list.return_value = [
                'https://res.cloudinary.com/demo/image/upload/v1/pet.jpg'
            ]
            mock_ev_prop.return_value = mock_ev_set
            serializer = MagicMock()
            serializer.is_valid.return_value = True
            serializer.save.return_value = pet_guardada
            mock_get_ser.return_value = serializer

            request = self.factory.post(
                '/api/peticiones/iniciar/',
                {'id_tipo': 1, 'descripcion': 'Animal en riesgo requiere rescate'},
                format='json',
            )
            force_authenticate(request, user=self.ciudadano)
            response = IniciarPeticionView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['id_peticion'], 777)
        mock_buscar.assert_called_once()


class Dinov2MicroservicioClienteTests(SimpleTestCase):
    """Pruebas del cliente HTTP interno hacia VetericanoIA sin cargar PyTorch en Django."""

    @override_settings(
        DUPLICADOS_DINOV2_BACKEND='microservicio',
        DUPLICADOS_DINOV2_MICROSERVICIO_URL='http://127.0.0.1:8001',
        DUPLICADOS_DINOV2_MICROSERVICIO_SECRET='secreto-prueba-local',
        DUPLICADOS_DINOV2_MICROSERVICIO_TIMEOUT_SEGUNDOS=2.0,
    )
    @patch('peticiones.services.dinov2_service._cargar_modelo_dinov2')
    @patch('peticiones.services.ia_client_service.requests.post')
    def test_solicita_embedding_al_microservicio_sin_cargar_modelo_local(self, mock_post, mock_local):
        vec_ref = _vector_unitario(4, peso_secundario=0.5, indice_secundario=9)
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = {'ok': True, 'dimensiones': 384, 'embedding': vec_ref}
        mock_post.return_value = resp_mock

        img_bytes = _crear_bytes_imagen('JPEG', (64, 64))
        emb = dinov2_service.extraer_embedding_dinov2_desde_bytes(img_bytes)

        self.assertIsNotNone(emb)
        self.assertEqual(len(emb), 384)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in emb)), 1.0, places=5)
        mock_local.assert_not_called()
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.args[0], 'http://127.0.0.1:8001/embed/')
        self.assertEqual(
            mock_post.call_args.kwargs['headers']['X-Vetericano-IA-Secret'],
            'secreto-prueba-local',
        )

    @override_settings(
        DUPLICADOS_DINOV2_BACKEND='microservicio',
        DUPLICADOS_DINOV2_MICROSERVICIO_URL='http://127.0.0.1:8001',
        DUPLICADOS_DINOV2_MICROSERVICIO_SECRET='secreto-prueba-local',
    )
    @patch('peticiones.services.ia_client_service.requests.post')
    def test_errores_timeout_red_auth_y_503_lanzan_model_no_disponible(self, mock_post):
        import requests

        img_bytes = _crear_bytes_imagen('PNG', (64, 64))

        mock_post.side_effect = requests.Timeout('Read timed out')
        with self.assertRaises(dinov2_service.ModelDinov2NoDisponibleError):
            dinov2_service.extraer_embedding_dinov2_desde_bytes(img_bytes)

        mock_post.side_effect = requests.ConnectionError('Connection refused')
        with self.assertRaises(dinov2_service.ModelDinov2NoDisponibleError):
            dinov2_service.extraer_embedding_dinov2_desde_bytes(img_bytes)

        for codigo in (503, 500, 401, 403):
            mock_post.side_effect = None
            resp_err = MagicMock()
            resp_err.status_code = codigo
            mock_post.return_value = resp_err
            with self.subTest(status_code=codigo):
                with self.assertRaises(dinov2_service.ModelDinov2NoDisponibleError):
                    dinov2_service.extraer_embedding_dinov2_desde_bytes(img_bytes)

        # HTTP 400 / 413 -> descarta imagen sin activar error de infraestructura
        for codigo_img in (400, 413):
            resp_bad = MagicMock()
            resp_bad.status_code = codigo_img
            mock_post.return_value = resp_bad
            self.assertIsNone(dinov2_service.extraer_embedding_dinov2_desde_bytes(img_bytes))

    @override_settings(
        DUPLICADOS_DINOV2_BACKEND='microservicio',
        DUPLICADOS_DINOV2_MICROSERVICIO_URL='http://127.0.0.1:8001',
        DUPLICADOS_DINOV2_MICROSERVICIO_SECRET='',
    )
    def test_secreto_no_configurado_falla_cerrado(self):
        img_bytes = _crear_bytes_imagen('PNG', (64, 64))
        with self.assertRaises(dinov2_service.ModelDinov2NoDisponibleError):
            dinov2_service.extraer_embedding_dinov2_desde_bytes(img_bytes)

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
        DUPLICADOS_DINOV2_FALLBACK_DHASH=True,
        DUPLICADOS_DINOV2_BACKEND='microservicio',
        DUPLICADOS_DINOV2_MICROSERVICIO_URL='http://127.0.0.1:8001',
        DUPLICADOS_DINOV2_MICROSERVICIO_SECRET='secreto-prueba-local',
    )
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_regla_gps_0m_200m_201m_y_fallback_timeout_no_acumula(self, mock_objects):
        import requests as req_lib
        lat_base = 2.444814
        lon_base = -76.614739
        metros_por_grado_lat = 6371000.0 * math.pi / 180.0
        # 200.0 m exactos (ligeramente por debajo de 200.0 por redondeo flotante para asegurar <= 200.0)
        lat_200 = lat_base + (199.99 / metros_por_grado_lat)
        lat_201 = lat_base + (201.05 / metros_por_grado_lat)

        url_0 = 'https://res.cloudinary.com/demo/image/upload/v1/c0.jpg'
        url_200 = 'https://res.cloudinary.com/demo/image/upload/v1/c200.jpg'
        url_201 = 'https://res.cloudinary.com/demo/image/upload/v1/c201.jpg'
        url_orig = 'https://res.cloudinary.com/demo/image/upload/v1/orig.jpg'

        cand_0m = _peticion_falsa(
            id_peticion=801,
            descripcion='Caso exacto 0 metros',
            latitud=lat_base,
            longitud=lon_base,
            foto=url_0,
        )
        cand_200m = _peticion_falsa(
            id_peticion=802,
            descripcion='Caso limite 200 metros',
            latitud=lat_200,
            longitud=lon_base,
            foto=url_200,
        )
        cand_201m = _peticion_falsa(
            id_peticion=803,
            descripcion='Caso fuera 201 metros con misma foto',
            latitud=lat_201,
            longitud=lon_base,
            foto=url_201,
        )
        mock_objects.select_related.return_value = _mock_queryset([cand_0m, cand_200m, cand_201m])

        img_bytes = _crear_bytes_imagen('JPEG', (64, 64))
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            return_value=img_bytes,
        ), patch(
            'peticiones.services.ia_client_service.requests.post',
            side_effect=req_lib.Timeout('Read timed out'),
        ) as mock_post:
            res = duplicados_service.buscar_peticiones_similares_por_descripcion(
                'Animal reportado cerca',
                latitud=lat_base,
                longitud=lon_base,
                foto=url_orig,
            )

        # No acumula timeouts: tras el primer timeout en VetericanoIA detiene las llamadas IA
        self.assertEqual(mock_post.call_count, 1)
        self.assertTrue(res['posible_duplicado'])
        ids = {c['id_peticion'] for c in res['posibles_duplicados']}
        self.assertIn(801, ids)
        self.assertIn(802, ids)
        self.assertNotIn(803, ids)
        for cand in res['posibles_duplicados']:
            self.assertLessEqual(cand['distancia_metros'], 200.0)
            self.assertIsNone(cand['porcentaje_similitud_visual'])


