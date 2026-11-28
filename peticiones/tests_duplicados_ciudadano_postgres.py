"""
Pruebas de integración reales contra PostgreSQL local aislado para el endpoint
ciudadano POST /api/peticiones/detectar-duplicados-ciudadano/.

Valida pg_trgm + Haversine reales, autenticación JWT real (SimpleJWT +
CustomJWTAuthentication), throttling por usuario, privacidad por lista blanca,
cero escrituras en BD, no regresiones en el endpoint interno y comportamiento del
feature flag DUPLICADOS_CIUDADANO_FOTO_ACTIVA.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from inventario.tests.base import VetericanoTransactionTestCase
from peticiones.api.duplicados_ciudadano import DuplicadosCiudadanoRafagaThrottle
from peticiones.models import (
    EstadoPeticiones,
    EvidenciaPeticiones,
    HistorialAsignacionPeticion,
    HistorialPeticiones,
    NotificacionJuridico,
    NotificacionPeticion,
    PeticionFotoAutorizada,
    PeticionPosibleDuplicado,
    Peticiones,
    TipoPeticion,
    Ubicaciones,
)
from users.models import Rol, Sede, Usuarios

URL_CIUDADANO = '/api/peticiones/detectar-duplicados-ciudadano/'
URL_INTERNO = '/api/peticiones/detectar-duplicados/'
URL_INICIAR = '/api/peticiones/iniciar/'

CLAVES_RESPUESTA_INICIAR = {
    'mensaje',
    'id_peticion',
    'numero_radicado',
    'id_tipo',
    'id_ubicacion',
    'id_estado',
    'estado',
    'prioridad',
    'asignado_a',
    'correo_urgente_enviado',
    'foto',
    'fotos',
    'total_fotos',
}

LAT_BASE = 2.4448000
LON_BASE = -76.6147000
# ~30 metros al norte de (LAT_BASE, LON_BASE)
LAT_CERCA_30M = 2.4450700
LON_CERCA_30M = -76.6147000
# ~5 km al norte de (LAT_BASE, LON_BASE)
LAT_LEJOS_5KM = 2.4898000
LON_LEJOS_5KM = -76.6147000


def _es_postgresql() -> bool:
    return connection.vendor == 'postgresql'


@skipUnless(_es_postgresql(), 'Requiere PostgreSQL local aislado')
class PostgresDetectarDuplicadosCiudadanoTests(VetericanoTransactionTestCase):
    """Pruebas de integración reales en PostgreSQL para el detector ciudadano."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.sede = Sede.objects.create(
            nombre='Sede Ciudadano PG',
            direccion='Cra 5 # 10-20',
            ciudad='Popayan',
            telefono='3001112233',
            codigo='CIUD-PG-01',
        )
        self.rol_admin = Rol.objects.get(pk=1)
        self.rol_juridico = Rol.objects.get(pk=2)
        self.rol_vet = Rol.objects.get(pk=3)
        self.rol_ciudadano = Rol.objects.get(pk=4)
        self.rol_super = Rol.objects.filter(pk=5).first() or Rol.objects.create(
            id_rol=5,
            nombre_rol='super_administrador',
        )

        self.ciudadano = self._crear_usuario(self.rol_ciudadano, 'Ciudadano Uno', 'ciudadano1@local.test')
        self.tipo = TipoPeticion.objects.get(pk=1)
        self.estado = EstadoPeticiones.objects.get(pk=1)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _crear_usuario(self, rol: Rol, nombre: str, email: str, *, activo: bool = True) -> Usuarios:
        return Usuarios.objects.create(
            id_rol=rol,
            id_sede=self.sede,
            nombre=nombre,
            email=email,
            password='hash',
            activo=activo,
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
        latitud: float = LAT_BASE,
        longitud: float = LON_BASE,
        direccion: str = 'Parque Principal Popayan',
        foto: str | None = None,
        responsable: Usuarios | None = None,
    ) -> Peticiones:
        ubicacion = Ubicaciones.objects.create(
            direccion=direccion,
            latitud=latitud,
            longitud=longitud,
        )
        return Peticiones.objects.create(
            numero_radicado=radicado,
            id_tipo=self.tipo,
            id_ubicacion=ubicacion,
            id_estado=self.estado,
            responsable=responsable or self.ciudadano,
            descripcion=descripcion,
            prioridad='ALTA',
            foto=foto,
        )

    def test_01_coincidencia_real(self):
        self._crear_peticion(
            radicado='RAD-CIUD-001',
            descripcion='Perro herido atropellado frente al parque principal con la pata trasera sangrando',
            latitud=LAT_BASE,
            longitud=LON_BASE,
        )
        client = self._cliente_jwt(self.ciudadano)
        resp = client.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal pata trasera sangrando',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['advertencia_duplicado'])
        self.assertTrue(resp.data['posible_duplicado'])
        self.assertEqual(resp.data['total_candidatos'], 1)
        self.assertEqual(len(resp.data['posibles_duplicados']), 1)

        cand = resp.data['posibles_duplicados'][0]
        self.assertTrue(cand['coincidencia_texto'])
        self.assertTrue(cand['coincidencia_gps'])
        self.assertEqual(cand['cercania'], 'muy_cerca')
        self.assertIn(cand['similitud'], ('alta', 'media'))

    def test_02_privacidad_real(self):
        # Peticiones previas para asegurar un id_peticion distinto al conteo 1
        self._crear_peticion(
            radicado='RAD-FILLER-1',
            descripcion='Reporte antiguo de ejemplo en otro sector apartado del municipio',
            latitud=2.5500000,
            longitud=-76.5500000,
        )
        pet_a = self._crear_peticion(
            radicado='RAD-PRIV-987654',
            descripcion='Perro herido atropellado frente al parque principal collarin_azulado microchip_xzq991',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            direccion='Calle Secreta 45 # 12-89 Barrio Confidencial',
        )

        client = self._cliente_jwt(self.ciudadano)
        resp = client.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_candidatos'], 1)

        cuerpo_json = resp.content.decode('utf-8')
        parsed = json.loads(cuerpo_json)

        self.assertEqual(
            set(parsed.keys()),
            {
                'advertencia_duplicado',
                'posible_duplicado',
                'total_candidatos',
                'mensaje',
                'posibles_duplicados',
            },
        )
        cand = parsed['posibles_duplicados'][0]
        self.assertEqual(
            set(cand.keys()),
            {
                'coincidencia_texto',
                'coincidencia_gps',
                'cercania',
                'similitud',
                'foto',
                'tiene_fotografia',
            },
        )

        self.assertNotIn(pet_a.numero_radicado, cuerpo_json)
        self.assertNotIn('id_peticion', cuerpo_json)
        self.assertNotIn(str(pet_a.id_peticion), cuerpo_json)
        self.assertNotIn('collarin_azulado', cuerpo_json)
        self.assertNotIn('microchip_xzq991', cuerpo_json)
        self.assertNotIn('Calle Secreta 45', cuerpo_json)
        self.assertNotIn('Barrio Confidencial', cuerpo_json)
        self.assertNotIn('2.4448', cuerpo_json)
        self.assertNotIn('-76.6147', cuerpo_json)

    def test_03_sin_coincidencias(self):
        self._crear_peticion(
            radicado='RAD-SIN-001',
            descripcion='Perro herido atropellado frente al parque principal con la pata trasera sangrando',
            latitud=LAT_BASE,
            longitud=LON_BASE,
        )

        client = self._cliente_jwt(self.ciudadano)
        resp = client.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Solicitud de jornada de esterilizacion programada en vereda distante',
                'latitud': LAT_LEJOS_5KM,
                'longitud': LON_LEJOS_5KM,
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_candidatos'], 0)
        self.assertEqual(resp.data['posibles_duplicados'], [])
        self.assertFalse(resp.data['advertencia_duplicado'])
        self.assertFalse(resp.data['posible_duplicado'])

    def test_04_solo_gps(self):
        self._crear_peticion(
            radicado='RAD-GPS-001',
            descripcion='Gato naranja atrapado en el tejado de la escuela comunal del barrio',
            latitud=LAT_BASE,
            longitud=LON_BASE,
        )

        client = self._cliente_jwt(self.ciudadano)
        resp = client.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Solicitud urgente de vacunacion antirrabica domiciliaria para caninos',
                'latitud': LAT_BASE,
                'longitud': LON_BASE,
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_candidatos'], 1)
        self.assertTrue(resp.data['advertencia_duplicado'])

        cand = resp.data['posibles_duplicados'][0]
        self.assertTrue(cand['coincidencia_gps'])
        self.assertFalse(cand['coincidencia_texto'])
        self.assertEqual(cand['similitud'], 'no_disponible')
        self.assertEqual(cand['cercania'], 'muy_cerca')

    def test_05_maximo_cinco_candidatos(self):
        for i in range(1, 8):
            self._crear_peticion(
                radicado=f'RAD-MAX-{i:03d}',
                descripcion=f'Perro herido atropellado frente al parque principal caso numero {i}',
                latitud=LAT_BASE,
                longitud=LON_BASE,
            )

        client = self._cliente_jwt(self.ciudadano)
        resp = client.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_candidatos'], 5)
        self.assertEqual(len(resp.data['posibles_duplicados']), 5)

    def test_06_ventana_fija_30_dias(self):
        pet_antigua = self._crear_peticion(
            radicado='RAD-OLD-001',
            descripcion='Perro herido atropellado frente al parque principal con la pata sangrando',
            latitud=LAT_BASE,
            longitud=LON_BASE,
        )
        Peticiones.objects.filter(pk=pet_antigua.pk).update(
            fecha=timezone.now() - timedelta(days=60)
        )

        client = self._cliente_jwt(self.ciudadano)
        resp = client.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal con la pata sangrando',
                'latitud': LAT_BASE,
                'longitud': LON_BASE,
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_candidatos'], 0)
        self.assertEqual(resp.data['posibles_duplicados'], [])
        self.assertFalse(resp.data['advertencia_duplicado'])

    def test_07_parametros_del_cliente_ignorados(self):
        self._crear_peticion(
            radicado='RAD-IGN-5KM',
            descripcion='Gato naranja atrapado en el tejado de la escuela comunal norte',
            latitud=LAT_LEJOS_5KM,
            longitud=LON_LEJOS_5KM,
        )
        pet_antigua = self._crear_peticion(
            radicado='RAD-IGN-60D',
            descripcion='Solicitud urgente de vacunacion antirrabica domiciliaria en vereda',
            latitud=LAT_BASE,
            longitud=LON_BASE,
        )
        Peticiones.objects.filter(pk=pet_antigua.pk).update(
            fecha=timezone.now() - timedelta(days=60)
        )

        client = self._cliente_jwt(self.ciudadano)
        resp = client.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Solicitud urgente de vacunacion antirrabica domiciliaria en vereda',
                'latitud': LAT_BASE,
                'longitud': LON_BASE,
                'umbral': 0.01,
                'radio_metros': 50000,
                'dias': 3650,
                'limite': 50,
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_candidatos'], 0)
        self.assertEqual(resp.data['posibles_duplicados'], [])
        self.assertNotIn('parametros', resp.data)

    def test_08_cero_escrituras_en_bd(self):
        pet_a = self._crear_peticion(
            radicado='RAD-RO-001',
            descripcion='Perro herido atropellado frente al parque principal con la pata trasera sangrando',
            latitud=LAT_BASE,
            longitud=LON_BASE,
        )
        snap_a = (pet_a.descripcion, pet_a.id_estado_id, pet_a.asignado_a_id)

        modelos_monitoreados = (
            Peticiones,
            Ubicaciones,
            EvidenciaPeticiones,
            PeticionFotoAutorizada,
            PeticionPosibleDuplicado,
            NotificacionJuridico,
            NotificacionPeticion,
            HistorialPeticiones,
            HistorialAsignacionPeticion,
        )
        conteos_antes = {m.__name__: m.objects.count() for m in modelos_monitoreados}

        client = self._cliente_jwt(self.ciudadano)
        resp = client.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal pata trasera sangrando',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_candidatos'], 1)

        conteos_despues = {m.__name__: m.objects.count() for m in modelos_monitoreados}
        self.assertEqual(conteos_despues, conteos_antes)

        pet_a.refresh_from_db()
        self.assertEqual((pet_a.descripcion, pet_a.id_estado_id, pet_a.asignado_a_id), snap_a)

    def test_09_matriz_autenticacion_real(self):
        payload = {
            'descripcion': 'Perro herido atropellado frente al parque principal',
            'latitud': LAT_BASE,
            'longitud': LON_BASE,
        }

        # Sin token -> 401
        anon_client = APIClient()
        self.assertEqual(
            anon_client.post(URL_CIUDADANO, payload, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        # Ciudadano desactivado tras emitir el token -> 401 o 403
        ciudadano_desact = self._crear_usuario(
            self.rol_ciudadano,
            'Ciudadano Desactivado',
            'ciudadano.desact@local.test',
            activo=True,
        )
        client_desact = self._cliente_jwt(ciudadano_desact)
        Usuarios.objects.filter(pk=ciudadano_desact.pk).update(activo=False)
        resp_desact = client_desact.post(URL_CIUDADANO, payload, format='json')
        self.assertIn(
            resp_desact.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

        # Roles 1, 2, 3, 5 -> 403
        for rol, nombre, email in (
            (self.rol_admin, 'Admin PG', 'admin.mat@local.test'),
            (self.rol_juridico, 'Juridico PG', 'juridico.mat@local.test'),
            (self.rol_vet, 'Vet PG', 'vet.mat@local.test'),
            (self.rol_super, 'Super PG', 'super.mat@local.test'),
        ):
            usr = self._crear_usuario(rol, nombre, email)
            resp_rol = self._cliente_jwt(usr).post(URL_CIUDADANO, payload, format='json')
            self.assertEqual(
                resp_rol.status_code,
                status.HTTP_403_FORBIDDEN,
                f'Rol {rol.id_rol} debe recibir 403',
            )

        # Ciudadano activo (rol 4) -> 200
        resp_ok = self._cliente_jwt(self.ciudadano).post(URL_CIUDADANO, payload, format='json')
        self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)

    def test_10_throttling_real_por_usuario(self):
        segundo_ciudadano = self._crear_usuario(
            self.rol_ciudadano,
            'Ciudadano Dos',
            'ciudadano2@local.test',
        )
        payload = {
            'descripcion': 'Perro herido atropellado frente al parque principal',
            'latitud': LAT_BASE,
            'longitud': LON_BASE,
        }

        client1 = self._cliente_jwt(self.ciudadano)
        client2 = self._cliente_jwt(segundo_ciudadano)

        with patch.dict(
            DuplicadosCiudadanoRafagaThrottle.THROTTLE_RATES,
            {'duplicados_ciudadano_rafaga': '2/min'},
        ):
            r1 = client1.post(URL_CIUDADANO, payload, format='json')
            r2 = client1.post(URL_CIUDADANO, payload, format='json')
            r3 = client1.post(URL_CIUDADANO, payload, format='json')
            r_otro = client2.post(URL_CIUDADANO, payload, format='json')

        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(r3.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(r_otro.status_code, status.HTTP_200_OK)

    def test_11_endpoint_interno_intacto(self):
        pet_a = self._crear_peticion(
            radicado='RAD-INT-001',
            descripcion='Perro herido atropellado frente al parque principal con la pata trasera sangrando',
            latitud=LAT_BASE,
            longitud=LON_BASE,
        )
        juridico = self._crear_usuario(
            self.rol_juridico,
            'Juridico Interno',
            'juridico.int@local.test',
        )

        payload = {
            'descripcion': 'Perro herido atropellado frente al parque principal',
            'latitud': LAT_CERCA_30M,
            'longitud': LON_CERCA_30M,
        }

        resp_juridico = self._cliente_jwt(juridico).post(URL_INTERNO, payload, format='json')
        self.assertEqual(resp_juridico.status_code, status.HTTP_200_OK)
        self.assertIn('parametros', resp_juridico.data)
        self.assertEqual(resp_juridico.data['total_candidatos'], 1)
        cand = resp_juridico.data['posibles_duplicados'][0]
        self.assertEqual(cand['id_peticion'], pet_a.id_peticion)
        self.assertEqual(cand['numero_radicado'], pet_a.numero_radicado)

        resp_ciudadano = self._cliente_jwt(self.ciudadano).post(URL_INTERNO, payload, format='json')
        self.assertEqual(resp_ciudadano.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(DUPLICADOS_CIUDADANO_FOTO_ACTIVA=False)
    def test_12_flag_apagado_oculta_foto_aunque_exista_autorizacion(self):
        url_foto = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/animal_test.jpg'
        pet = self._crear_peticion(
            radicado='RAD-FOTO-OFF',
            descripcion='Perro herido atropellado frente al parque principal con la pata trasera sangrando',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=url_foto,
        )
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet,
            ruta_archivo=url_foto,
            autorizado_por=self.ciudadano,
            version_texto='v1',
        )

        client = self._cliente_jwt(self.ciudadano)
        resp = client.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_candidatos'], 1)
        cand = resp.data['posibles_duplicados'][0]
        self.assertIsNone(cand['foto'])
        self.assertFalse(cand['tiene_fotografia'])

        cuerpo_json = resp.content.decode('utf-8')
        self.assertNotIn(url_foto, cuerpo_json)
        self.assertNotIn('animal_test.jpg', cuerpo_json)

    def test_13_foto_activada_autorizada_miniatura_y_exclusion_video_sin_red(self):
        url_foto = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/animal_test.jpg'
        url_miniatura_esperada = (
            'https://res.cloudinary.com/vet-test/image/upload/'
            'c_limit,w_1280,h_1280,q_auto,f_auto/v1700000000/animal_test.jpg'
        )
        url_video = 'https://res.cloudinary.com/vet-test/video/upload/v1700000000/clip.mp4'

        pet_img = self._crear_peticion(
            radicado='RAD-FOTO-ON',
            descripcion='Perro herido atropellado frente al parque principal con la pata trasera sangrando',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=url_foto,
        )
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet_img,
            ruta_archivo=url_foto,
            autorizado_por=self.ciudadano,
            version_texto='v1',
        )

        pet_video = self._crear_peticion(
            radicado='RAD-VIDEO-ON',
            descripcion='Felino atrapado en alcantarilla profunda del barrio universitario',
            latitud=LAT_LEJOS_5KM,
            longitud=LON_LEJOS_5KM,
            foto=None,
        )
        EvidenciaPeticiones.objects.create(
            id_peticion=pet_video,
            ruta_archivo=url_video,
            descripcion='Video de evidencia',
        )
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet_video,
            ruta_archivo=url_video,
            autorizado_por=self.ciudadano,
            version_texto='v1',
        )

        client = self._cliente_jwt(self.ciudadano)
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=AssertionError('No debe descargar imágenes desde Cloudinary'),
        ), patch(
            'peticiones.services.duplicados_service.calcular_hashes_urls',
            side_effect=AssertionError('No debe calcular dHash en consulta ciudadana'),
        ), patch(
            'requests.get',
            side_effect=AssertionError('No debe realizar requests.get'),
        ):
            resp_img = client.post(
                URL_CIUDADANO,
                {
                    'descripcion': 'Perro herido atropellado frente al parque principal',
                    'latitud': LAT_CERCA_30M,
                    'longitud': LON_CERCA_30M,
                },
                format='json',
            )
            resp_vid = client.post(
                URL_CIUDADANO,
                {
                    'descripcion': 'Felino atrapado en alcantarilla profunda del barrio universitario',
                    'latitud': LAT_LEJOS_5KM,
                    'longitud': LON_LEJOS_5KM,
                },
                format='json',
            )

        self.assertEqual(resp_img.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_img.data['total_candidatos'], 1)
        cand_img = resp_img.data['posibles_duplicados'][0]
        self.assertEqual(cand_img['foto'], url_miniatura_esperada)
        self.assertTrue(cand_img['tiene_fotografia'])
        self.assertNotIn(url_foto, resp_img.content.decode('utf-8'))

        self.assertEqual(resp_vid.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_vid.data['total_candidatos'], 1)
        cand_vid = resp_vid.data['posibles_duplicados'][0]
        self.assertIsNone(cand_vid['foto'])
        self.assertFalse(cand_vid['tiene_fotografia'])
        self.assertNotIn(url_video, resp_vid.content.decode('utf-8'))

    def test_14_flujo_real_jwt_iniciar_con_autorizacion_y_consulta_ciudadano_b(self):
        url_img_a = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/animal_a.jpg'
        url_video = 'https://res.cloudinary.com/vet-test/video/upload/v1700000000/clip.mp4'
        url_miniatura_esperada = (
            'https://res.cloudinary.com/vet-test/image/upload/'
            'c_limit,w_1280,h_1280,q_auto,f_auto/v1700000000/animal_a.jpg'
        )
        ciudadano_b = self._crear_usuario(
            self.rol_ciudadano,
            'Ciudadano Dos',
            'ciudadano.b.real@local.test',
        )

        # Relleno previo para que id_peticion sea distinto al conteo 1
        self._crear_peticion(
            radicado='RAD-FILL-AUT-1',
            descripcion='Reporte apartado en otra zona rural muy distante del casco urbano',
            latitud=2.5500000,
            longitud=-76.5500000,
        )

        client_a = self._cliente_jwt(self.ciudadano)
        resp_iniciar = client_a.post(
            URL_INICIAR,
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Perro herido atropellado frente al parque principal animal_a_unico',
                'latitud': LAT_BASE,
                'longitud': LON_BASE,
                'foto': url_img_a,
                'fotos': [url_img_a, url_video],
                'fotos_autorizadas_duplicados': [url_img_a],
            },
            format='json',
        )

        self.assertEqual(resp_iniciar.status_code, status.HTTP_201_CREATED)
        pet_id = resp_iniciar.data['id_peticion']
        radicado_a = resp_iniciar.data['numero_radicado']

        autorizaciones = list(PeticionFotoAutorizada.objects.filter(id_peticion_id=pet_id))
        self.assertEqual(len(autorizaciones), 1)
        self.assertEqual(autorizaciones[0].ruta_archivo, url_img_a)
        self.assertEqual(autorizaciones[0].autorizado_por_id, self.ciudadano.id_usuario)
        self.assertEqual(autorizaciones[0].version_texto, 'v1')
        self.assertIsNone(autorizaciones[0].revocada_en)

        modelos_monitoreados = (
            Peticiones,
            Ubicaciones,
            EvidenciaPeticiones,
            PeticionFotoAutorizada,
            PeticionPosibleDuplicado,
            NotificacionJuridico,
            NotificacionPeticion,
            HistorialPeticiones,
            HistorialAsignacionPeticion,
        )
        conteos_antes = {m.__name__: m.objects.count() for m in modelos_monitoreados}

        client_b = self._cliente_jwt(ciudadano_b)
        resp_b = client_b.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal animal_a_unico',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )

        self.assertEqual(resp_b.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_b.data['total_candidatos'], 1)
        cand = resp_b.data['posibles_duplicados'][0]
        self.assertEqual(cand['foto'], url_miniatura_esperada)
        self.assertTrue(cand['tiene_fotografia'])

        cuerpo_b = resp_b.content.decode('utf-8')
        self.assertNotIn(url_img_a, cuerpo_b)
        self.assertNotIn(radicado_a, cuerpo_b)
        self.assertNotIn('id_peticion', cuerpo_b)
        self.assertNotIn(str(pet_id), cuerpo_b)

        conteos_despues = {m.__name__: m.objects.count() for m in modelos_monitoreados}
        self.assertEqual(conteos_despues, conteos_antes)

        # Caso adicional: petición SIN autorización creada antes (más relevante)
        # y petición CON autorización creada después -> el candidato con foto va primero.
        url_img_sin_aut = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/orden_sin_aut.jpg'
        url_img_con_aut = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/orden_con_aut.jpg'
        url_miniatura_orden = (
            'https://res.cloudinary.com/vet-test/image/upload/'
            'c_limit,w_1280,h_1280,q_auto,f_auto/v1700000000/orden_con_aut.jpg'
        )
        desc_consulta_orden = (
            'Felino atrapado en alcantarilla profunda del barrio universitario caso_orden_sin_foto'
        )

        resp_sin_aut = client_a.post(
            URL_INICIAR,
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': desc_consulta_orden,
                'latitud': LAT_LEJOS_5KM,
                'longitud': LON_LEJOS_5KM,
                'foto': url_img_sin_aut,
                'fotos': [url_img_sin_aut],
            },
            format='json',
        )
        self.assertEqual(resp_sin_aut.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            PeticionFotoAutorizada.objects.filter(id_peticion_id=resp_sin_aut.data['id_peticion']).count(),
            0,
        )

        resp_con_aut = client_a.post(
            URL_INICIAR,
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Felino atrapado en alcantarilla profunda del barrio universitario con herida leve',
                'latitud': round(LAT_LEJOS_5KM + 0.0008, 6),
                'longitud': LON_LEJOS_5KM,
                'foto': url_img_con_aut,
                'fotos': [url_img_con_aut],
                'fotos_autorizadas_duplicados': [url_img_con_aut],
            },
            format='json',
        )
        self.assertEqual(resp_con_aut.status_code, status.HTTP_201_CREATED, resp_con_aut.data)
        self.assertEqual(
            PeticionFotoAutorizada.objects.filter(id_peticion_id=resp_con_aut.data['id_peticion']).count(),
            1,
        )

        resp_orden = client_b.post(
            URL_CIUDADANO,
            {
                'descripcion': desc_consulta_orden,
                'latitud': LAT_LEJOS_5KM,
                'longitud': LON_LEJOS_5KM,
            },
            format='json',
        )
        self.assertEqual(resp_orden.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_orden.data['total_candidatos'], 2)

        primero, segundo = resp_orden.data['posibles_duplicados']
        self.assertTrue(primero['tiene_fotografia'])
        self.assertEqual(primero['foto'], url_miniatura_orden)
        self.assertEqual(primero['cercania'], 'cerca')

        self.assertFalse(segundo['tiene_fotografia'])
        self.assertIsNone(segundo['foto'])
        self.assertEqual(segundo['cercania'], 'muy_cerca')

        cuerpo_orden = resp_orden.content.decode('utf-8')
        self.assertNotIn(url_img_sin_aut, cuerpo_orden)
        self.assertNotIn(url_img_con_aut, cuerpo_orden)

    def test_15_iniciar_sin_campo_y_con_null_regresion_claves_y_cero_autorizaciones(self):
        url_img_a = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/animal_a.jpg'
        ciudadano_b = self._crear_usuario(
            self.rol_ciudadano,
            'Ciudadano Tres',
            'ciudadano.b.legacy@local.test',
        )
        client_a = self._cliente_jwt(self.ciudadano)
        client_b = self._cliente_jwt(ciudadano_b)

        # Cliente antiguo sin campo `fotos_autorizadas_duplicados`
        resp_legacy = client_a.post(
            URL_INICIAR,
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Perro herido frente al parque principal sin campo consentimiento',
                'latitud': LAT_BASE,
                'longitud': LON_BASE,
                'foto': url_img_a,
                'fotos': [url_img_a],
            },
            format='json',
        )
        self.assertEqual(resp_legacy.status_code, status.HTTP_201_CREATED)
        self.assertEqual(set(resp_legacy.data.keys()), CLAVES_RESPUESTA_INICIAR)
        self.assertEqual(
            PeticionFotoAutorizada.objects.filter(id_peticion_id=resp_legacy.data['id_peticion']).count(),
            0,
        )

        resp_b = client_b.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido frente al parque principal sin campo consentimiento',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )
        self.assertEqual(resp_b.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_b.data['total_candidatos'], 1)
        self.assertIsNone(resp_b.data['posibles_duplicados'][0]['foto'])
        self.assertFalse(resp_b.data['posibles_duplicados'][0]['tiene_fotografia'])

        # Cliente enviando `fotos_autorizadas_duplicados: null`
        resp_null = client_a.post(
            URL_INICIAR,
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Gato atrapado en techo de colegio con campo nulo',
                'latitud': LAT_LEJOS_5KM,
                'longitud': LON_LEJOS_5KM,
                'foto': url_img_a,
                'fotos_autorizadas_duplicados': None,
            },
            format='json',
        )
        self.assertEqual(resp_null.status_code, status.HTTP_201_CREATED)
        self.assertEqual(set(resp_null.data.keys()), CLAVES_RESPUESTA_INICIAR)
        self.assertEqual(
            PeticionFotoAutorizada.objects.filter(id_peticion_id=resp_null.data['id_peticion']).count(),
            0,
        )

    def test_16_iniciar_ignora_video_y_url_no_enviada_en_misma_peticion(self):
        url_img_a = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/animal_a.jpg'
        url_video = 'https://res.cloudinary.com/vet-test/video/upload/v1700000000/clip.mp4'
        url_ajena = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/no_enviada.jpg'

        client_a = self._cliente_jwt(self.ciudadano)
        resp = client_a.post(
            URL_INICIAR,
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Canino desorientado en el parque principal con collar roto',
                'latitud': LAT_BASE,
                'longitud': LON_BASE,
                'foto': url_img_a,
                'fotos': [url_img_a, url_video],
                'fotos_autorizadas_duplicados': [url_video, url_ajena],
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            PeticionFotoAutorizada.objects.filter(id_peticion_id=resp.data['id_peticion']).count(),
            0,
        )

    def test_17_iniciar_juridico_enviando_autorizacion_se_ignora(self):
        url_img_a = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/animal_a.jpg'
        juridico = self._crear_usuario(
            self.rol_juridico,
            'Juridico Creador',
            'juridico.creador@local.test',
        )
        client_jur = self._cliente_jwt(juridico)
        resp = client_jur.post(
            URL_INICIAR,
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Reporte creado por juridico con campo fotos autorizadas',
                'latitud': LAT_BASE,
                'longitud': LON_BASE,
                'foto': url_img_a,
                'fotos': [url_img_a],
                'fotos_autorizadas_duplicados': [url_img_a],
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            PeticionFotoAutorizada.objects.filter(id_peticion_id=resp.data['id_peticion']).count(),
            0,
        )

    def test_18_peticion_historica_orm_y_autorizacion_revocada_retornan_foto_none(self):
        url_img_a = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/animal_hist.jpg'
        pet_hist = self._crear_peticion(
            radicado='RAD-HIST-001',
            descripcion='Perro herido atropellado frente al parque principal caso historico',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=url_img_a,
        )
        client_b = self._cliente_jwt(
            self._crear_usuario(self.rol_ciudadano, 'Ciudadano Hist', 'ciud.hist@local.test')
        )

        resp_sin_fila = client_b.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal caso historico',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )
        self.assertEqual(resp_sin_fila.status_code, status.HTTP_200_OK)
        cand1 = resp_sin_fila.data['posibles_duplicados'][0]
        self.assertIsNone(cand1['foto'])
        self.assertFalse(cand1['tiene_fotografia'])

        # Ahora con fila revocada -> también foto None
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet_hist,
            ruta_archivo=url_img_a,
            autorizado_por=self.ciudadano,
            version_texto='v1',
            revocada_en=timezone.now(),
        )
        resp_revocada = client_b.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal caso historico',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )
        self.assertEqual(resp_revocada.status_code, status.HTTP_200_OK)
        cand2 = resp_revocada.data['posibles_duplicados'][0]
        self.assertIsNone(cand2['foto'])
        self.assertFalse(cand2['tiene_fotografia'])

    def test_19_autorizacion_por_url_especifica_solo_muestra_la_autorizada(self):
        url_no_aut = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/no_autorizada.jpg'
        url_si_aut = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/si_autorizada.jpg'
        miniatura_si = (
            'https://res.cloudinary.com/vet-test/image/upload/'
            'c_limit,w_1280,h_1280,q_auto,f_auto/v1700000000/si_autorizada.jpg'
        )

        pet = self._crear_peticion(
            radicado='RAD-URL-ESP',
            descripcion='Perro herido atropellado frente al parque principal multiples evidencias',
            latitud=LAT_BASE,
            longitud=LON_BASE,
            foto=url_no_aut,
        )
        EvidenciaPeticiones.objects.create(
            id_peticion=pet,
            ruta_archivo=url_no_aut,
            descripcion='Evidencia #1',
        )
        EvidenciaPeticiones.objects.create(
            id_peticion=pet,
            ruta_archivo=url_si_aut,
            descripcion='Evidencia #2',
        )
        PeticionFotoAutorizada.objects.create(
            id_peticion=pet,
            ruta_archivo=url_si_aut,
            autorizado_por=self.ciudadano,
            version_texto='v1',
        )

        client_b = self._cliente_jwt(
            self._crear_usuario(self.rol_ciudadano, 'Ciudadano Esp', 'ciud.esp@local.test')
        )
        resp = client_b.post(
            URL_CIUDADANO,
            {
                'descripcion': 'Perro herido atropellado frente al parque principal multiples evidencias',
                'latitud': LAT_CERCA_30M,
                'longitud': LON_CERCA_30M,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_candidatos'], 1)
        cand = resp.data['posibles_duplicados'][0]
        self.assertEqual(cand['foto'], miniatura_si)
        self.assertTrue(cand['tiene_fotografia'])

        cuerpo = resp.content.decode('utf-8')
        self.assertNotIn(url_no_aut, cuerpo)
        self.assertNotIn('no_autorizada.jpg', cuerpo)
        self.assertNotIn(url_si_aut, cuerpo)

    def test_20_fallo_al_guardar_autorizacion_no_aborta_peticion(self):
        url_img_a = 'https://res.cloudinary.com/vet-test/image/upload/v1700000000/animal_fail_safe.jpg'
        client_a = self._cliente_jwt(self.ciudadano)

        with patch(
            'peticiones.services.foto_autorizada_service.PeticionFotoAutorizada.objects.get_or_create',
            side_effect=RuntimeError('Error simulado al persistir consentimiento'),
        ):
            with self.assertLogs('peticiones.services.foto_autorizada_service', level='ERROR'):
                resp = client_a.post(
                    URL_INICIAR,
                    {
                        'id_tipo': self.tipo.id_tipo,
                        'descripcion': 'Perro herido frente al parque con fallo controlado de consentimiento',
                        'latitud': LAT_BASE,
                        'longitud': LON_BASE,
                        'foto': url_img_a,
                        'fotos': [url_img_a],
                        'fotos_autorizadas_duplicados': [url_img_a],
                    },
                    format='json',
                )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        pet_id = resp.data['id_peticion']
        self.assertTrue(Peticiones.objects.filter(pk=pet_id).exists())
        self.assertEqual(
            PeticionFotoAutorizada.objects.filter(id_peticion_id=pet_id).count(),
            0,
        )
