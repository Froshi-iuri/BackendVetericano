"""
Pruebas de integración aisladas contra PostgreSQL local (`test_vetericano`) para
validar la interacción completa entre Django, PostgreSQL, DINOv2 Small real
(`facebook/dinov2-small`), fotografías reales, autorización de fotos y el endpoint
ciudadano POST /api/peticiones/detectar-duplicados-ciudadano/.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import requests

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from inventario.tests.base import VetericanoTransactionTestCase
from peticiones.models import (
    EstadoPeticiones,
    EvidenciaPeticiones,
    PeticionFotoAutorizada,
    PeticionImagenEmbedding,
    PeticionPosibleDuplicado,
    Peticiones,
    TipoPeticion,
    Ubicaciones,
)
from peticiones.services import dinov2_service
from peticiones.services.alertas_duplicados_service import detectar_y_registrar_duplicados_al_iniciar
from users.models import Rol, Sede, Usuarios

URL_CIUDADANO = '/api/peticiones/detectar-duplicados-ciudadano/'
URL_INICIAR = '/api/peticiones/iniciar/'

FOTOS_DIR = Path(settings.BASE_DIR) / 'scripts' / 'fotos_prueba_dinov2'
FOTO_MISMO_1 = FOTOS_DIR / 'mismoa.png'
FOTO_MISMO_2 = FOTOS_DIR / 'mismoanimal.png'
FOTO_DIFERENTE = FOTOS_DIR / 'diferente.png'

URL_MISMO_1 = 'https://res.cloudinary.com/vet-test/image/upload/v1700000101/mismo_animal_1.png'
URL_MISMO_2 = 'https://res.cloudinary.com/vet-test/image/upload/v1700000102/mismo_animal_2.png'
URL_DIFERENTE = 'https://res.cloudinary.com/vet-test/image/upload/v1700000103/animal_diferente.png'
URL_NO_AUTORIZADA = 'https://res.cloudinary.com/vet-test/image/upload/v1700000104/mismo_no_autorizada.png'
URL_REVOCADA = 'https://res.cloudinary.com/vet-test/image/upload/v1700000105/mismo_revocada.png'

MINIATURA_MISMO_1 = (
    'https://res.cloudinary.com/vet-test/image/upload/'
    'e_blur_faces/c_fill,g_auto,w_480,h_360,q_auto,f_auto/v1700000101/mismo_animal_1.png'
)

LAT_BASE = 2.4448000
LON_BASE = -76.6147000
# ~30 metros al norte
LAT_CERCA_30M = 2.4450700
LON_CERCA_30M = -76.6147000
# ~5 km al norte (fuera del radio de 200 m)
LAT_LEJOS_5KM = 2.4898000
LON_LEJOS_5KM = -76.6147000


VETERIANO_IA_DIR = Path(settings.BASE_DIR) / 'VetericanoIA'
VETERIANO_IA_PYTHON = VETERIANO_IA_DIR / 'envs' / 'Scripts' / 'python.exe'


def _entorno_listo() -> bool:
    return (
        connection.vendor == 'postgresql'
        and FOTO_MISMO_1.exists()
        and FOTO_MISMO_2.exists()
        and FOTO_DIFERENTE.exists()
        and VETERIANO_IA_PYTHON.exists()
    )


def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


def _simular_descarga_cloudinary_real(url: str, **kwargs):
    mapeo = {
        URL_MISMO_1: FOTO_MISMO_1,
        URL_NO_AUTORIZADA: FOTO_MISMO_1,
        URL_REVOCADA: FOTO_MISMO_1,
        URL_MISMO_2: FOTO_MISMO_2,
        URL_DIFERENTE: FOTO_DIFERENTE,
    }
    ruta = mapeo.get(url)
    if ruta is None:
        return None
    return ruta.read_bytes()


@skipUnless(_entorno_listo(), 'Requiere PostgreSQL local aislado, fotos reales y entorno VetericanoIA')
class PostgresDinov2IntegracionTests(VetericanoTransactionTestCase):
    """Pruebas de integración Django -> HTTP -> FastAPI VetericanoIA -> PostgreSQL."""

    _ia_proc = None
    _ia_url = None
    _ia_secret = None
    _override_ctx = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        puerto = _puerto_libre()
        cls._ia_secret = secrets.token_urlsafe(24)
        cls._ia_url = f'http://127.0.0.1:{puerto}'

        db_cfg = connection.settings_dict
        if (
            db_cfg.get('NAME') == 'test_vetericano'
            and str(db_cfg.get('HOST', '')).lower() in ('127.0.0.1', 'localhost', '::1')
        ):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    ALTER TABLE public.peticion_posible_duplicado
                        ADD COLUMN IF NOT EXISTS metodo_imagen varchar(40) NULL,
                        ADD COLUMN IF NOT EXISTS id_peticion_duplicada integer NULL,
                        ADD COLUMN IF NOT EXISTS porcentaje_proximidad_gps double precision NULL,
                        ADD COLUMN IF NOT EXISTS porcentaje_similitud_visual double precision NULL,
                        ADD COLUMN IF NOT EXISTS radio_gps_metros double precision NULL DEFAULT 200.0,
                        ADD COLUMN IF NOT EXISTS similitud_visual_coseno double precision NULL;
                    """
                )

        env = os.environ.copy()
        env.update({
            'IA_HOST': '127.0.0.1',
            'IA_PORT': str(puerto),
            'IA_INTERNAL_SHARED_SECRET': cls._ia_secret,
            'IA_MODEL_NAME': 'facebook/dinov2-small',
            'IA_DEVICE': 'cpu',
            'IA_PRELOAD_MODEL': 'true',
            'IA_TORCH_NUM_THREADS': '1',
            'IA_MAX_CONCURRENT_INFERENCES': '1',
            'IA_MAX_PENDING_QUEUE': '4',
            'IA_MAX_QUEUE_WAIT_SECONDS': '10.0',
        })
        cls._ia_proc = subprocess.Popen(
            [str(VETERIANO_IA_PYTHON), 'run_server.py'],
            cwd=str(VETERIANO_IA_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + 60.0
        listo = False
        while time.monotonic() < deadline:
            if cls._ia_proc.poll() is not None:
                raise RuntimeError('El microservicio VetericanoIA finalizó inesperadamente.')
            try:
                r = requests.get(f'{cls._ia_url}/health/', timeout=1.5)
                if r.status_code == 200 and r.json().get('modelo_cargado'):
                    listo = True
                    break
            except requests.RequestException:
                pass
            time.sleep(0.4)

        if not listo:
            cls.tearDownClass()
            raise RuntimeError('Timeout esperando /health/ listo de VetericanoIA en pruebas.')

        cls._override_ctx = override_settings(
            DUPLICADOS_DINOV2_BACKEND='microservicio',
            DUPLICADOS_DINOV2_MICROSERVICIO_URL=cls._ia_url,
            DUPLICADOS_DINOV2_MICROSERVICIO_SECRET=cls._ia_secret,
            DUPLICADOS_DINOV2_MICROSERVICIO_TIMEOUT_SEGUNDOS=15.0,
        )
        cls._override_ctx.enable()

    @classmethod
    def tearDownClass(cls):
        if cls._override_ctx is not None:
            cls._override_ctx.disable()
            cls._override_ctx = None
        if cls._ia_proc is not None:
            try:
                cls._ia_proc.terminate()
                cls._ia_proc.wait(timeout=10)
            except Exception:
                try:
                    cls._ia_proc.kill()
                except Exception:
                    pass
            cls._ia_proc = None
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        cache.clear()
        self.sede = Sede.objects.create(
            nombre='Sede DINOv2 PG',
            direccion='Cra 5 # 10-20',
            ciudad='Popayan',
            telefono='3002223344',
            codigo='DINO-PG-01',
        )
        self.rol_ciudadano = Rol.objects.get(pk=4)
        self.tipo = TipoPeticion.objects.get(pk=1)
        self.estado = EstadoPeticiones.objects.get(pk=1)

        self.peticionario_a = self._crear_usuario('Ciudadano A', 'dinov2.a@local.test')
        self.peticionario_b = self._crear_usuario('Ciudadano B', 'dinov2.b@local.test')

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _crear_usuario(self, nombre: str, email: str) -> Usuarios:
        return Usuarios.objects.create(
            id_rol=self.rol_ciudadano,
            id_sede=self.sede,
            nombre=nombre,
            email=email,
            password='hash',
            activo=True,
        )

    def _cliente_jwt(self, usuario: Usuarios) -> APIClient:
        refresh = RefreshToken.for_user(usuario)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(refresh.access_token)}')
        return client

    def _crear_peticion(
        self,
        *,
        radicado: str,
        descripcion: str,
        latitud: float,
        longitud: float,
        foto: str | None = None,
        evidencia: str | None = None,
        responsable: Usuarios | None = None,
    ) -> Peticiones:
        ubicacion = Ubicaciones.objects.create(
            direccion='Sector de prueba veterinaria',
            latitud=latitud,
            longitud=longitud,
        )
        pet = Peticiones.objects.create(
            numero_radicado=radicado,
            id_tipo=self.tipo,
            id_ubicacion=ubicacion,
            id_estado=self.estado,
            responsable=responsable or self.peticionario_a,
            descripcion=descripcion,
            prioridad='ALTA',
            foto=foto,
        )
        if evidencia:
            EvidenciaPeticiones.objects.create(
                id_peticion=pet,
                ruta_archivo=evidencia,
                descripcion='Evidencia fotográfica de prueba',
            )
        return pet

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_CIUDADANO_FOTO_ACTIVA=True,
    )
    def test_01_coincidencia_real_imagen_gps_devuelve_metricas_foto_autorizada_y_cache_pg(self):
        pet_a = Peticiones.objects.create(
            id_peticion=98766,
            numero_radicado='RAD-DINO-PG-001',
            id_tipo=self.tipo,
            id_ubicacion=Ubicaciones.objects.create(
                direccion='Sector de prueba veterinaria',
                latitud=LAT_BASE,
                longitud=LON_BASE,
            ),
            id_estado=self.estado,
            responsable=self.peticionario_a,
            descripcion='Reporte previo en zona norte sin texto compartido con la nueva solicitud',
            prioridad='ALTA',
            foto=URL_MISMO_1,
        )
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet_a,
            ruta_archivo=URL_MISMO_1,
            autorizado_por=self.peticionario_a,
            version_texto='v1',
        )

        client_b = self._cliente_jwt(self.peticionario_b)
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ) as mock_descarga:
            resp = client_b.post(
                URL_CIUDADANO,
                {
                    'descripcion': 'Avistamiento diferente redactado con otras palabras totalmente aparte',
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )

            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertTrue(resp.data['advertencia_duplicado'])
            self.assertTrue(resp.data['posible_duplicado'])
            self.assertEqual(resp.data['total_candidatos'], 1)

            cand = resp.data['posibles_duplicados'][0]
            self.assertTrue(cand['coincidencia_imagen'])
            self.assertTrue(cand['coincidencia_gps'])
            self.assertEqual(cand['cercania'], 'muy_cerca')
            self.assertGreaterEqual(cand['porcentaje_similitud_visual'], 45.0)
            self.assertIn(cand['similitud_visual'], ('media', 'alta'))
            self.assertFalse(cand['es_certeza_identidad'])
            self.assertTrue(cand['foto'].startswith('http://testserver/api/peticiones/duplicados/miniatura/?t='))
            self.assertTrue(cand['tiene_fotografia'])

            from urllib.parse import parse_qs, urlparse
            from peticiones.services.duplicados_ciudadano_service import (
                _hash_url_miniatura,
                decodificar_token_miniatura_ciudadano,
            )
            token = parse_qs(urlparse(cand['foto']).query)['t'][0]
            datos_tok = decodificar_token_miniatura_ciudadano(token)
            self.assertEqual(datos_tok['p'], pet_a.id_peticion)
            self.assertEqual(datos_tok['h'], _hash_url_miniatura(URL_MISMO_1))
            self.assertEqual(datos_tok['u'], self.peticionario_b.id_usuario)

            cuerpo = resp.content.decode('utf-8')
            self.assertNotIn(URL_MISMO_1, cuerpo)
            self.assertNotIn('res.cloudinary.com', cuerpo)
            self.assertNotIn('mismo_animal_1.png', cuerpo)
            self.assertNotIn(pet_a.numero_radicado, cuerpo)
            self.assertNotIn('id_peticion', cuerpo)
            self.assertNotIn(str(pet_a.id_peticion), cuerpo)

            # Con DUPLICADOS_DINOV2_CACHE_BD_ACTIVA=False (default), no debe escribir en tabla 0007
            self.assertEqual(PeticionImagenEmbedding.objects.count(), 0)
            # Django NO debe haber importado PyTorch ni Transformers durante la consulta
            self.assertNotIn('torch', sys.modules)
            self.assertNotIn('transformers', sys.modules)

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_CIUDADANO_FOTO_ACTIVA=True,
        DUPLICADOS_DINOV2_CACHE_BD_ACTIVA=True,
    )
    def test_06_cache_bd_embeddings_postgres_reutiliza_sin_redescargar(self):
        pet_a = self._crear_peticion(
            radicado='RAD-DINO-CACHE-PG',
            descripcion='Reporte para validar persistencia y lectura de embeddings en PostgreSQL',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_MISMO_1,
            responsable=self.peticionario_a,
        )
        client_b = self._cliente_jwt(self.peticionario_b)
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ) as mock_descarga:
            resp_1 = client_b.post(
                URL_CIUDADANO,
                {
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )
            self.assertEqual(resp_1.status_code, status.HTTP_200_OK)
            self.assertTrue(resp_1.data['posible_duplicado'])

            cache_pg = PeticionImagenEmbedding.objects.filter(
                peticion=pet_a,
                url_imagen=URL_MISMO_1,
            ).first()
            self.assertIsNotNone(cache_pg)
            self.assertEqual(cache_pg.dimensiones, 384)
            self.assertEqual(len(cache_pg.embedding_vector), 384)
            self.assertAlmostEqual(sum(float(v) * float(v) for v in cache_pg.embedding_vector), 1.0, places=4)
            self.assertEqual(PeticionImagenEmbedding.objects.count(), 1)

            urls_descargadas_1 = [call.args[0] for call in mock_descarga.call_args_list]
            self.assertIn(URL_MISMO_1, urls_descargadas_1)
            mock_descarga.reset_mock()

            resp_2 = client_b.post(
                URL_CIUDADANO,
                {
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )
            self.assertEqual(resp_2.status_code, status.HTTP_200_OK)
            self.assertTrue(resp_2.data['posible_duplicado'])
            urls_descargadas_2 = [call.args[0] for call in mock_descarga.call_args_list]
            self.assertEqual(urls_descargadas_2, [URL_MISMO_2])
            self.assertNotIn(URL_MISMO_1, urls_descargadas_2)
            self.assertEqual(PeticionImagenEmbedding.objects.count(), 1)

            # Verificar idempotencia frente a espacios y re-guardado sobre el mismo par (peticion, url, modelo)
            mock_descarga.reset_mock()
            emb_reutilizado = dinov2_service.obtener_embedding_desde_url(
                f'  {URL_MISMO_1}  ',
                id_peticion=pet_a.id_peticion,
                usar_cache_bd=True,
            )
            self.assertEqual(len(emb_reutilizado), 384)
            mock_descarga.assert_not_called()
            dinov2_service._guardar_embedding_bd(
                URL_MISMO_1,
                cache_pg.modelo_ia,
                emb_reutilizado,
                id_peticion=pet_a.id_peticion,
            )
            self.assertEqual(PeticionImagenEmbedding.objects.count(), 1)

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_CIUDADANO_FOTO_ACTIVA=True,
    )
    def test_02_fotos_no_autorizadas_o_revocadas_nunca_se_exponen_a_otro_peticionario(self):
        self._crear_peticion(
            radicado='RAD-DINO-NOAUT',
            descripcion='Caso previo con fotografía confidencial no autorizada',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_NO_AUTORIZADA,
            responsable=self.peticionario_a,
        )
        pet_rev = self._crear_peticion(
            radicado='RAD-DINO-REV',
            descripcion='Caso previo con autorización posteriormente revocada',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_REVOCADA,
            responsable=self.peticionario_a,
        )
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet_rev,
            ruta_archivo=URL_REVOCADA,
            autorizado_por=self.peticionario_a,
            version_texto='v1',
            revocada_en=timezone.now(),
        )

        client_b = self._cliente_jwt(self.peticionario_b)
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ):
            resp = client_b.post(
                URL_CIUDADANO,
                {
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['posible_duplicado'])
        self.assertEqual(resp.data['total_candidatos'], 2)

        cuerpo = resp.content.decode('utf-8')
        self.assertNotIn(URL_NO_AUTORIZADA, cuerpo)
        self.assertNotIn(URL_REVOCADA, cuerpo)
        self.assertNotIn('mismo_no_autorizada.png', cuerpo)
        self.assertNotIn('mismo_revocada.png', cuerpo)

        for cand in resp.data['posibles_duplicados']:
            self.assertGreaterEqual(cand['porcentaje_similitud_visual'], 45.0)
            self.assertIn(cand['similitud_visual'], ('media', 'alta'))
            self.assertEqual(cand['cercania'], 'muy_cerca')
            self.assertIsNone(cand['foto'])
            self.assertFalse(cand['tiene_fotografia'])

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
    )
    def test_03_sin_coincidencias_animal_diferente_lejos_y_solo_texto_gps(self):
        # 1) Animal diferente dentro de 200 m -> SÍ genera advertencia por GPS <= 200 m
        #    usando DINOv2/descripción como información adicional (sin bloquear).
        self._crear_peticion(
            radicado='RAD-DINO-DIF',
            descripcion='Perro atropellado en el parque principal frente a la iglesia central',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_DIFERENTE,
            responsable=self.peticionario_a,
        )
        # 2) Mismo animal fuera de 200 m (5 km) -> NUNCA coincide aunque la foto sea idéntica
        self._crear_peticion(
            radicado='RAD-DINO-LEJOS',
            descripcion='Mismo animal muy lejos en otra zona del municipio',
            latitud=LAT_LEJOS_5KM,
            longitud=LON_LEJOS_5KM,
            foto=URL_MISMO_1,
            responsable=self.peticionario_a,
        )

        client_b = self._cliente_jwt(self.peticionario_b)
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ):
            # Consulta junto a RAD-DINO-DIF (30 m): advierte por GPS <= 200 m, aunque animal sea distinto
            resp_dif = client_b.post(
                URL_CIUDADANO,
                {
                    'descripcion': 'Perro atropellado en el parque principal frente a la iglesia central',
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )
            # Consulta sin foto dentro de 200 m: advierte por GPS <= 200 m sin similitud visual
            resp_sin_foto = client_b.post(
                URL_CIUDADANO,
                {
                    'descripcion': 'Perro atropellado en el parque principal frente a la iglesia central',
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                },
                format='json',
            )
            # Consulta cerca de RAD-DINO-LEJOS pero desplazada > 200 m (5 km desde BASE):
            # verificamos desde coordenadas sin nada dentro de 200 m pero foto idéntica a RAD-DINO-DIF
            resp_lejos = client_b.post(
                URL_CIUDADANO,
                {
                    'descripcion': 'Animal en otro barrio distante',
                    'latitud': LAT_LEJOS_5KM + 0.01,
                    'longitud': LON_LEJOS_5KM,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )

        self.assertEqual(resp_dif.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_dif.data['advertencia_duplicado'])
        self.assertTrue(resp_dif.data['posible_duplicado'])
        self.assertEqual(resp_dif.data['total_candidatos'], 1)
        self.assertTrue(resp_dif.data['posibles_duplicados'][0]['coincidencia_gps'])
        self.assertFalse(resp_dif.data['posibles_duplicados'][0]['coincidencia_imagen'])

        self.assertEqual(resp_sin_foto.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_sin_foto.data['advertencia_duplicado'])
        self.assertEqual(resp_sin_foto.data['total_candidatos'], 1)
        self.assertEqual(resp_sin_foto.data['posibles_duplicados'][0]['similitud_visual'], 'no_disponible')
        self.assertIsNone(resp_sin_foto.data['posibles_duplicados'][0]['porcentaje_similitud_visual'])

        self.assertEqual(resp_lejos.status_code, status.HTTP_200_OK)
        self.assertFalse(resp_lejos.data['advertencia_duplicado'])
        self.assertEqual(resp_lejos.data['total_candidatos'], 0)

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_DINOV2_FALLBACK_DHASH=True,
    )
    def test_04_fallo_cloudinary_y_fallo_modelo_dinov2_resilientes(self):
        self._crear_peticion(
            radicado='RAD-DINO-ERR',
            descripcion='Perro herido reportado previamente para prueba de resiliencia',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_MISMO_1,
            responsable=self.peticionario_a,
        )
        client_b = self._cliente_jwt(self.peticionario_b)

        # A) Fallo descargando imagen desde Cloudinary -> igual advierte por GPS <= 200 m
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            return_value=None,
        ):
            resp_cloud = client_b.post(
                URL_CIUDADANO,
                {
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )
        self.assertEqual(resp_cloud.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_cloud.data['posible_duplicado'])
        self.assertEqual(resp_cloud.data['total_candidatos'], 1)
        cand_cloud = resp_cloud.data['posibles_duplicados'][0]
        self.assertEqual(cand_cloud['similitud_visual'], 'no_disponible')
        self.assertIsNone(cand_cloud['porcentaje_similitud_visual'])

        # B) Microservicio IA caído / no responde -> advierte por GPS <= 200 m sin error 500
        #    y señala que la similitud visual no está disponible
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ), override_settings(
            DUPLICADOS_DINOV2_MICROSERVICIO_URL='http://127.0.0.1:59999',
            DUPLICADOS_DINOV2_MICROSERVICIO_TIMEOUT_SEGUNDOS=0.8,
        ):
            resp_modelo = client_b.post(
                URL_CIUDADANO,
                {
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )
        self.assertEqual(resp_modelo.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_modelo.data['posible_duplicado'])
        self.assertEqual(resp_modelo.data['total_candidatos'], 1)
        cand_mod = resp_modelo.data['posibles_duplicados'][0]
        self.assertTrue(cand_mod['coincidencia_gps'])
        self.assertEqual(cand_mod['similitud_visual'], 'no_disponible')
        self.assertIsNone(cand_mod['porcentaje_similitud_visual'])
        self.assertIn('no disponible', cand_mod['advertencia_metrica_visual'].lower())

        # C) Radicación /iniciar/ nunca se bloquea si falla DINOv2
        with patch(
            'peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion',
            side_effect=dinov2_service.ModelDinov2NoDisponibleError('Fallo simulado en iniciar'),
        ):
            resp_iniciar = client_b.post(
                URL_INICIAR,
                {
                    'id_tipo': self.tipo.id_tipo,
                    'descripcion': 'Nueva petición con fallo simulado del detector visual',
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )
        self.assertEqual(resp_iniciar.status_code, status.HTTP_201_CREATED)

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True)
    def test_05_detector_automatico_iniciar_conserva_sistema_anterior_incluso_con_dinov2_activo(self):
        pet_base = self._crear_peticion(
            radicado='RAD-AUTO-BASE',
            descripcion='Perro atropellado en la esquina del parque caldas con herida visible',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_MISMO_1,
            responsable=self.peticionario_a,
        )
        pet_nueva = self._crear_peticion(
            radicado='RAD-AUTO-NUEVA',
            descripcion='Perro atropellado en la esquina del parque caldas con herida visible',
            latitud=LAT_CERCA_30M,
            longitud=LON_CERCA_30M,
            foto=URL_MISMO_2,
            responsable=self.peticionario_b,
        )

        # Con DUPLICADOS_IA_DINOV2_ACTIVA=True, el detector automático de registro conserva
        # explícitamente el flujo texto + GPS (usar_dinov2=False) sin exigir imágenes,
        # mientras el endpoint ciudadano sigue usando DINOv2 (>= 45 % + GPS <= 200 m).
        with override_settings(
            DUPLICADOS_IA_DINOV2_ACTIVA=True,
            DUPLICADOS_UMBRAL_DINOV2=0.45,
            DUPLICADOS_RADIO_GPS_METROS=200.0,
        ):
            res_auto = detectar_y_registrar_duplicados_al_iniciar(
                pet_nueva,
                notificar=False,
                registrar_historial=False,
            )
            self.assertTrue(res_auto.ejecutada)
            self.assertGreaterEqual(res_auto.candidatos_detectados, 1)
            self.assertTrue(
                PeticionPosibleDuplicado.objects.filter(
                    peticion_menor_id=min(pet_base.id_peticion, pet_nueva.id_peticion),
                    peticion_mayor_id=max(pet_base.id_peticion, pet_nueva.id_peticion),
                ).exists()
            )

            # También mediante HTTP POST /api/peticiones/iniciar/ el hook on_commit registra la alerta
            client_b = self._cliente_jwt(self.peticionario_b)
            resp_iniciar = client_b.post(
                URL_INICIAR,
                {
                    'id_tipo': self.tipo.id_tipo,
                    'descripcion': 'Perro atropellado en la esquina del parque caldas con herida visible',
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                },
                format='json',
            )
            self.assertEqual(resp_iniciar.status_code, status.HTTP_201_CREATED)
            pet_http_id = resp_iniciar.data['id_peticion']
            self.assertTrue(
                PeticionPosibleDuplicado.objects.filter(
                    peticion_origen_id=pet_http_id,
                ).exists()
            )

            # Simultáneamente, el endpoint ciudadano sigue usando DINOv2 (>= 45 % + GPS <= 200 m)
            with patch(
                'peticiones.services.imagen_hash_service.descargar_imagen_segura',
                side_effect=_simular_descarga_cloudinary_real,
            ):
                resp_ciudadano = client_b.post(
                    URL_CIUDADANO,
                    {
                        'latitud': LAT_CERCA_30M,
                        'longitud': LON_CERCA_30M,
                        'foto': URL_MISMO_2,
                    },
                    format='json',
                )
            self.assertEqual(resp_ciudadano.status_code, status.HTTP_200_OK)
            self.assertTrue(resp_ciudadano.data['posible_duplicado'])
            cand = resp_ciudadano.data['posibles_duplicados'][0]
            self.assertEqual(cand['metodo_imagen'], 'dinov2_small')
            self.assertGreaterEqual(cand['porcentaje_similitud_visual'], 45.0)
            self.assertEqual(cand['cercania'], 'muy_cerca')

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_DINOV2_CACHE_BD_ACTIVA=True,
    )
    def test_07_limites_gps_0_200_201_comparacion_visual_web_y_resolucion_admin(self):
        import math

        metros_por_grado_lat = 6371000.0 * math.pi / 180.0
        lat_200 = round(LAT_BASE + (199.95 / metros_por_grado_lat), 7)
        lat_201 = round(LAT_BASE + (201.10 / metros_por_grado_lat), 7)

        pet_0m = self._crear_peticion(
            radicado='RAD-LIM-0M',
            descripcion='Caso exacto a 0 metros',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_MISMO_1,
            responsable=self.peticionario_a,
        )
        pet_200m = self._crear_peticion(
            radicado='RAD-LIM-200M',
            descripcion='Caso limite inclusive a 200 metros',
            latitud=lat_200,
            longitud=LON_BASE,
            foto=URL_DIFERENTE,
            responsable=self.peticionario_a,
        )
        pet_201m = self._crear_peticion(
            radicado='RAD-LIM-201M',
            descripcion='Caso excluido a 201 metros con misma fotografia',
            latitud=lat_201,
            longitud=LON_BASE,
            foto=URL_MISMO_1,
            responsable=self.peticionario_a,
        )
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet_0m,
            ruta_archivo=URL_MISMO_1,
            autorizado_por=self.peticionario_a,
            version_texto='v1',
        )

        # 1) Endpoint ciudadano a 0m/200m vs >200m
        client_b = self._cliente_jwt(self.peticionario_b)
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ):
            resp_ciudadano = client_b.post(
                URL_CIUDADANO,
                {
                    'latitud': LAT_BASE,
                    'longitud': LON_BASE,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )
        self.assertEqual(resp_ciudadano.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_ciudadano.data['posible_duplicado'])
        # Solo pet_0m y pet_200m entran (201m queda fuera aunque la foto sea idéntica)
        self.assertEqual(resp_ciudadano.data['total_candidatos'], 2)
        c0, c1 = resp_ciudadano.data['posibles_duplicados']
        self.assertEqual(c0['cercania'], 'muy_cerca')
        self.assertTrue(c0['tiene_fotografia'])
        self.assertIsNotNone(c0['foto'])
        self.assertTrue(c0['foto'].startswith('http://testserver/api/peticiones/duplicados/miniatura/?t='))
        self.assertEqual(c1['cercania'], 'cerca')
        self.assertFalse(c1['tiene_fotografia'])
        self.assertIsNone(c1['foto'])

        # 2) Flujo Administrador/Jurídico: alerta, "Calcular con DINOv2" y resolución conservando historial
        rol_juridico = Rol.objects.filter(pk=2).first() or Rol.objects.create(id_rol=2, nombre='Jurídico')
        juridico = Usuarios.objects.create(
            id_rol=rol_juridico,
            id_sede=self.sede,
            nombre='Juridico Prueba',
            email='juridico.int@local.test',
            password='hash',
            activo=True,
        )
        rol_vet = Rol.objects.filter(pk=3).first() or Rol.objects.create(id_rol=3, nombre='Veterinario')
        vet = Usuarios.objects.create(
            id_rol=rol_vet,
            id_sede=self.sede,
            nombre='Vet Prueba',
            email='vet.int@local.test',
            password='hash',
            activo=True,
        )

        # Alerta entre pet_0m y pet_201m (solo para verificar comparación visual web con DINOv2)
        # Usamos pet_0m y pet_nueva_30m para par real con alerta <= 200 m
        pet_30m = self._crear_peticion(
            radicado='RAD-WEB-30M',
            descripcion='Mismo perro cerca al punto',
            latitud=LAT_CERCA_30M,
            longitud=LON_CERCA_30M,
            foto=URL_MISMO_2,
            responsable=self.peticionario_b,
        )
        p_menor, p_mayor = PeticionPosibleDuplicado.normalizar_par_peticiones(pet_0m, pet_30m)
        alerta = PeticionPosibleDuplicado.objects.create(
            peticion_menor=p_menor,
            peticion_mayor=p_mayor,
            peticion_origen=pet_30m,
            distancia_metros=30.0,
            coincidencia_gps=True,
            score_combinado=0.85,
            criterios_coincidencia=['gps'],
        )

        client_j = self._cliente_jwt(juridico)
        # Error 503 controlado si VetericanoIA no responde
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ), override_settings(
            DUPLICADOS_DINOV2_MICROSERVICIO_URL='http://127.0.0.1:59999',
            DUPLICADOS_DINOV2_MICROSERVICIO_TIMEOUT_SEGUNDOS=0.8,
        ):
            resp_503 = client_j.post(
                f'/api/peticiones/duplicados/alertas/{alerta.id_alerta}/comparar-visual/',
                {},
                format='json',
            )
        self.assertEqual(resp_503.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

        # Cálculo DINOv2 real exitoso en web -> muestra similitud visual y proximidad GPS
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ):
            resp_visual = client_j.post(
                f'/api/peticiones/duplicados/alertas/{alerta.id_alerta}/comparar-visual/',
                {},
                format='json',
            )
        self.assertEqual(resp_visual.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_visual.data['comparado'])
        self.assertTrue(resp_visual.data['dinov2_calculada'])
        self.assertGreaterEqual(resp_visual.data['porcentaje_similitud_visual'], 45.0)
        self.assertIsNotNone(resp_visual.data['distancia_gps_metros'])
        self.assertGreaterEqual(
            PeticionImagenEmbedding.objects.filter(peticion_id__in=[pet_0m.id_peticion, pet_30m.id_peticion]).count(),
            2,
        )

        # Resolución administrativa: elegir pet_0m como original -> pet_30m queda Duplicada sin borrar BD
        total_antes = Peticiones.objects.count()
        EstadoPeticiones.objects.get_or_create(nombre='Duplicada', defaults={'descripcion': 'Duplicada'})
        resp_res = client_j.post(
            f'/api/peticiones/duplicados/alertas/{alerta.id_alerta}/resolver/',
            {
                'estado_revision': 'CONFIRMADO',
                'peticion_principal': pet_0m.id_peticion,
                'observacion': 'Confirmado mismo animal',
            },
            format='json',
        )
        self.assertEqual(resp_res.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_res.data['estado_nuevo'], 'CONFIRMADO')
        self.assertEqual(resp_res.data['peticion_original_id'], pet_0m.id_peticion)
        self.assertEqual(resp_res.data['peticion_duplicada_id'], pet_30m.id_peticion)
        # No borra registros de PostgreSQL
        self.assertEqual(Peticiones.objects.count(), total_antes)
        pet_30m.refresh_from_db()
        self.assertEqual(pet_30m.id_estado.nombre.lower(), 'duplicada')

        # Excluida del listado operativo por defecto y del flujo de asignación
        resp_listar = client_j.get('/api/peticiones/listar/')
        self.assertEqual(resp_listar.status_code, status.HTTP_200_OK)
        ids_listar = {item['id_peticion'] for item in resp_listar.data}
        self.assertIn(pet_0m.id_peticion, ids_listar)
        self.assertNotIn(pet_30m.id_peticion, ids_listar)

        resp_asignar = client_j.patch(
            f'/api/peticiones/{pet_30m.id_peticion}/asignar/',
            {'asignado_a': vet.id_usuario},
            format='json',
        )
        self.assertEqual(resp_asignar.status_code, status.HTTP_409_CONFLICT)

        resp_dash = client_j.get('/api/peticiones/dashboard-juridico/')
        self.assertEqual(resp_dash.status_code, status.HTTP_200_OK)
        self.assertLessEqual(resp_dash.data['peticiones']['pendientes'], total_antes - 1)

        # Al confirmarse pet_30m como copia duplicada, incluso con foto autorizada previa no se expone al ciudadano
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet_30m,
            ruta_archivo=URL_MISMO_2,
            autorizado_por=self.peticionario_b,
            version_texto='v1',
        )
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ):
            resp_post_conf = client_b.post(
                URL_CIUDADANO,
                {
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )
        self.assertEqual(resp_post_conf.status_code, status.HTTP_200_OK)
        # pet_0m sí expone miniatura; pet_30m (duplicada confirmada) permanece privada
        con_foto = [c for c in resp_post_conf.data['posibles_duplicados'] if c['tiene_fotografia']]
        sin_foto = [c for c in resp_post_conf.data['posibles_duplicados'] if not c['tiene_fotografia']]
        self.assertEqual(len(con_foto), 1)
        self.assertGreaterEqual(len(sin_foto), 1)

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_CIUDADANO_FOTO_ACTIVA=True,
        DUPLICADOS_AUTO_HABILITAR_FOTOS_PETICIONARIO=True,
    )
    def test_08_dinov2_real_sin_casilla_proxy_sin_exif_y_revocacion_inmediata(self):
        import io
        from urllib.parse import urlparse
        from PIL import Image

        client_a = self._cliente_jwt(self.peticionario_a)
        client_b = self._cliente_jwt(self.peticionario_b)

        # 1) Ciudadano A radica sin casilla `fotos_autorizadas_duplicados` (política activa habilita imagen)
        resp_iniciar = client_a.post(
            URL_INICIAR,
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Avistamiento canino sin casilla para flujo DINOv2 real',
                'latitud': LAT_BASE,
                'longitud': LON_BASE,
                'foto': URL_MISMO_1,
                'fotos': [URL_MISMO_1],
            },
            format='json',
        )
        self.assertEqual(resp_iniciar.status_code, status.HTTP_201_CREATED)
        pet_id = resp_iniciar.data['id_peticion']

        # 2) Ciudadano B reporta foto diferente del mismo animal cerca -> DINOv2 detecta >= 45% + entrega proxy
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ):
            resp_det = client_b.post(
                URL_CIUDADANO,
                {
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )

        self.assertEqual(resp_det.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_det.data['posible_duplicado'])
        cand = resp_det.data['posibles_duplicados'][0]
        self.assertTrue(cand['coincidencia_imagen'])
        self.assertEqual(cand['metodo_imagen'], 'dinov2_small')
        self.assertGreaterEqual(cand['porcentaje_similitud_visual'], 45.0)
        self.assertTrue(cand['tiene_fotografia'])
        url_proxy = cand['foto']
        self.assertTrue(url_proxy.startswith('http://testserver/api/peticiones/duplicados/miniatura/?t='))
        self.assertNotIn('res.cloudinary.com', resp_det.content.decode('utf-8'))

        # 3) Proxy entrega miniatura JPEG sin EXIF a partir de la foto real
        ruta_proxy = f"{urlparse(url_proxy).path}?{urlparse(url_proxy).query}"
        with Image.open(FOTO_MISMO_1) as img_orig:
            exif = Image.Exif()
            exif[271] = 'TelefonoTestEXIF'
            buf = io.BytesIO()
            img_orig.convert('RGB').save(buf, format='JPEG', exif=exif.tobytes())
            bytes_blur = buf.getvalue()

        with patch(
            'peticiones.services.duplicados_ciudadano_service._descargar_bytes_url_validada',
            return_value=bytes_blur,
        ):
            resp_proxy = APIClient().get(ruta_proxy)

        self.assertEqual(resp_proxy.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_proxy['Content-Type'], 'image/jpeg')
        with Image.open(io.BytesIO(resp_proxy.content)) as img_res:
            self.assertEqual(img_res.format, 'JPEG')
            self.assertLessEqual(img_res.size[0], 480)
            self.assertLessEqual(img_res.size[1], 360)
            self.assertEqual(len(img_res.getexif()), 0)

        # 4) Ciudadano A revoca -> el token previo falla (404) pero DINOv2 sigue detectando texto/GPS/imagen
        resp_rev = client_a.post(f'/api/peticiones/{pet_id}/duplicados/revocar-foto/', {}, format='json')
        self.assertEqual(resp_rev.status_code, status.HTTP_200_OK)
        self.assertEqual(APIClient().get(ruta_proxy).status_code, status.HTTP_404_NOT_FOUND)

        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_simular_descarga_cloudinary_real,
        ):
            resp_det_post_rev = client_b.post(
                URL_CIUDADANO,
                {
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )
        self.assertEqual(resp_det_post_rev.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_det_post_rev.data['posible_duplicado'])
        cand_rev = resp_det_post_rev.data['posibles_duplicados'][0]
        self.assertTrue(cand_rev['coincidencia_imagen'])
        self.assertGreaterEqual(cand_rev['porcentaje_similitud_visual'], 45.0)
        self.assertIsNone(cand_rev['foto'])
        self.assertFalse(cand_rev['tiene_fotografia'])

    @override_settings(
        DUPLICADOS_IA_DINOV2_ACTIVA=True,
        DUPLICADOS_UMBRAL_DINOV2=0.45,
        DUPLICADOS_RADIO_GPS_METROS=200.0,
        DUPLICADOS_CIUDADANO_FOTO_ACTIVA=True,
        DUPLICADOS_AUTO_HABILITAR_FOTOS_PETICIONARIO=True,
    )
    def test_09_e2e_dos_ciudadanos_dinov2_politica_automatica_y_peticiones_antiguas(self):
        import io
        from urllib.parse import urlparse
        from PIL import Image

        # A) Peticiones antiguas en la misma zona:
        # 1) Antigua sin fila en PeticionFotoAutorizada -> debe seguir PRIVADA (ausencia != permiso)
        self._crear_peticion(
            radicado='RAD-OLD-NOAUT',
            descripcion='Reporte antiguo sin registro de autorizacion',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_NO_AUTORIZADA,
            responsable=self.peticionario_a,
        )
        # 2) Antigua con autorización revocada -> debe seguir PRIVADA
        pet_old_rev = self._crear_peticion(
            radicado='RAD-OLD-REV',
            descripcion='Reporte antiguo con revocacion expresa',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_REVOCADA,
            responsable=self.peticionario_a,
        )
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet_old_rev,
            ruta_archivo=URL_REVOCADA,
            autorizado_por=self.peticionario_a,
            version_texto='v1',
            revocada_en=timezone.now(),
        )
        # 3) Antigua con autorización v1 vigente -> sigue visible vía proxy seguro
        pet_old_v1 = self._crear_peticion(
            radicado='RAD-OLD-V1',
            descripcion='Reporte antiguo con autorizacion expresa vigente',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_MISMO_1,
            responsable=self.peticionario_a,
        )
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet_old_v1,
            ruta_archivo=URL_MISMO_1,
            autorizado_por=self.peticionario_a,
            version_texto='v1',
        )

        # B) Ciudadano 1 radica nueva petición con foto sin enviar casilla `fotos_autorizadas_duplicados`
        client_1 = self._cliente_jwt(self.peticionario_a)
        client_2 = self._cliente_jwt(self.peticionario_b)
        url_nueva_1 = 'https://res.cloudinary.com/vet-test/image/upload/v1700000199/e2e_nueva_c1.png'

        def _descarga_e2e(url: str, **kwargs):
            if url == url_nueva_1:
                return FOTO_MISMO_1.read_bytes()
            return _simular_descarga_cloudinary_real(url, **kwargs)

        resp_c1 = client_1.post(
            URL_INICIAR,
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Canino reportado por ciudadano uno sin casilla',
                'latitud': LAT_BASE,
                'longitud': LON_BASE,
                'foto': url_nueva_1,
                'fotos': [url_nueva_1],
            },
            format='json',
        )
        self.assertEqual(resp_c1.status_code, status.HTTP_201_CREATED)
        pet_nueva_id = resp_c1.data['id_peticion']
        reg_aut = PeticionFotoAutorizada.objects.filter(id_peticion_id=pet_nueva_id).first()
        self.assertIsNotNone(reg_aut)
        self.assertEqual(reg_aut.version_texto, 'politica_sis')

        # C) Ciudadano 2 reporta un animal similar cerca -> DINOv2 real detecta coincidencia y devuelve miniatura
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=_descarga_e2e,
        ):
            resp_c2 = client_2.post(
                URL_CIUDADANO,
                {
                    'descripcion': 'Perro parecido visto en el mismo sector',
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                    'foto': URL_MISMO_2,
                },
                format='json',
            )

        self.assertEqual(resp_c2.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_c2.data['posible_duplicado'])
        cuerpo = resp_c2.content.decode('utf-8')
        self.assertNotIn('res.cloudinary.com', cuerpo)
        self.assertNotIn(URL_NO_AUTORIZADA, cuerpo)
        self.assertNotIn(URL_REVOCADA, cuerpo)
        self.assertNotIn(url_nueva_1, cuerpo)

        candidatos = resp_c2.data['posibles_duplicados']
        con_foto = [c for c in candidatos if c['tiene_fotografia']]
        sin_foto = [c for c in candidatos if not c['tiene_fotografia']]
        # Exactamente las 2 habilitadas (nueva bajo `politica_sis` y antigua `v1`) muestran proxy URL;
        # las 2 antiguas privadas/revocadas mantienen `foto=None` y `tiene_fotografia=False`
        self.assertEqual(len(con_foto), 2)
        self.assertEqual(len(sin_foto), 2)
        for c in con_foto:
            self.assertTrue(c['coincidencia_imagen'])
            self.assertTrue(c['coincidencia_gps'])
            self.assertGreaterEqual(c['porcentaje_similitud_visual'], 45.0)
            self.assertTrue(c['foto'].startswith('http://testserver/api/peticiones/duplicados/miniatura/?t='))

        # D) Descarga desde proxy para la nueva petición entrega JPEG limpio sin EXIF
        with Image.open(FOTO_MISMO_1) as img_src:
            buf = io.BytesIO()
            img_src.convert('RGB').save(buf, format='JPEG')
            blur_bytes = buf.getvalue()

        for c in con_foto:
            ruta_proxy = f"{urlparse(c['foto']).path}?{urlparse(c['foto']).query}"
            with patch(
                'peticiones.services.duplicados_ciudadano_service._descargar_bytes_url_validada',
                return_value=blur_bytes,
            ):
                r_img = APIClient().get(ruta_proxy)
            self.assertEqual(r_img.status_code, status.HTTP_200_OK)
            self.assertEqual(r_img['Content-Type'], 'image/jpeg')
            with Image.open(io.BytesIO(r_img.content)) as im:
                self.assertEqual(len(im.getexif()), 0)
                self.assertLessEqual(im.size[0], 480)
                self.assertLessEqual(im.size[1], 360)

