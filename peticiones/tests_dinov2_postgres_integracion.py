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

        env = os.environ.copy()
        env.update({
            'IA_HOST': '127.0.0.1',
            'IA_PORT': str(puerto),
            'IA_INTERNAL_SHARED_SECRET': cls._ia_secret,
            'IA_MODEL_NAME': 'facebook/dinov2-small',
            'IA_DEVICE': 'cpu',
            'IA_PRELOAD_MODEL': 'true',
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
            self.assertEqual(cand['foto'], MINIATURA_MISMO_1)
            self.assertTrue(cand['tiene_fotografia'])

            cuerpo = resp.content.decode('utf-8')
            self.assertNotIn(URL_MISMO_1, cuerpo)
            self.assertNotIn(pet_a.numero_radicado, cuerpo)
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
        # 1) Animal diferente dentro de 200 m incluso con descripción idéntica -> NO coincide
        self._crear_peticion(
            radicado='RAD-DINO-DIF',
            descripcion='Perro atropellado en el parque principal frente a la iglesia central',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=URL_DIFERENTE,
            responsable=self.peticionario_a,
        )
        # 2) Mismo animal fuera de 200 m (5 km) -> NO coincide
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
            resp_sin_foto = client_b.post(
                URL_CIUDADANO,
                {
                    'descripcion': 'Perro atropellado en el parque principal frente a la iglesia central',
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                },
                format='json',
            )

        for resp in (resp_dif, resp_sin_foto):
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertFalse(resp.data['advertencia_duplicado'])
            self.assertFalse(resp.data['posible_duplicado'])
            self.assertEqual(resp.data['total_candidatos'], 0)
            self.assertEqual(resp.data['posibles_duplicados'], [])

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

        # A) Fallo descargando imagen desde Cloudinary
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
        self.assertFalse(resp_cloud.data['posible_duplicado'])
        self.assertEqual(resp_cloud.data['total_candidatos'], 0)

        # B) Microservicio IA caído / no responde -> respaldo controlado sin error 500
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
        self.assertFalse(resp_modelo.data['posible_duplicado'])
        self.assertEqual(resp_modelo.data['total_candidatos'], 0)

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
