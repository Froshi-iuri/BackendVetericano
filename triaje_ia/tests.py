"""
Pruebas unitarias, concurrencia, recuperación y regresión aislada para `triaje_ia`.

Valida sobre base de datos aislada (sin tocar producción):
- Bandera `TRIAJE_N8N_ACTIVO=False` por defecto y aislamiento de `IniciarPeticionView`.
- Despacho no bloqueante, persistente, con bloqueo de registro, lease y reintentos con backoff.
- Concurrencia real en despacho y callbacks, fencing con `despacho_token` y protección
  contra resultados duplicados o desactualizados (`409 Conflict`).
- Recuperación automática de trabajos interrumpidos (`PENDIENTE`, `ENVIANDO` con lease
  expirado, `EN_PROCESO` expirado) tras reinicios y ciclo del recuperador automático.
- Permisos de Admin/Jurídico, exclusión de veterinarios/sedes inactivas, tope de carga
  y límites de asignación existentes (duplicadas, terminales, regla de 5 días).
- Resiliencia total: un fallo de triaje nunca impide registrar una denuncia.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Optional
from unittest.mock import MagicMock, patch

import requests
from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper as SqliteDatabaseWrapper
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from peticiones.api.views import IniciarPeticionView
from peticiones.models import (
    EstadoPeticiones,
    EvidenciaPeticiones,
    HistorialAsignacionPeticion,
    HistorialPeticiones,
    NotificacionJuridico,
    NotificacionPeticion,
    Peticiones,
    PeticionFotoAutorizada,
    PeticionImagenEmbedding,
    PeticionPosibleDuplicado,
    TipoPeticion,
    Ubicaciones,
)
from peticiones.utils import es_prioridad_urgente
from triaje_ia.escenarios_demo import ESCENARIOS_DEMO
from triaje_ia.models import TriajePeticion
from triaje_ia.recovery import (
    _es_proceso_cli_excluido,
    detener_recuperador_automatico,
    iniciar_recuperador_automatico_si_aplica,
)
from triaje_ia.services import (
    _ahora_local,
    calcular_carga_veterinarios,
    despachar_triaje_por_id,
    ejecutar_deteccion_duplicados_triaje,
    encolar_y_despachar_triaje_peticion,
    normalizar_prioridad_sugerida,
    procesar_reintentos_pendientes,
    recuperar_trabajos_interrumpidos,
    registrar_resultado_triaje,
)
from users.constants import (
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_PETICIONARIO,
    ROL_SUPER_ADMINISTRADOR,
    ROL_VETERINARIO,
)
from users.models import Rol, Sede, Usuarios

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

_MODELOS_AISLADOS = (
    Rol,
    Sede,
    Usuarios,
    TipoPeticion,
    EstadoPeticiones,
    Ubicaciones,
    Peticiones,
    EvidenciaPeticiones,
    NotificacionJuridico,
    NotificacionPeticion,
    HistorialAsignacionPeticion,
    HistorialPeticiones,
    PeticionFotoAutorizada,
    PeticionImagenEmbedding,
    PeticionPosibleDuplicado,
    TriajePeticion,
)


def _crear_conexion_sqlite(
    *, file_uri: Optional[str] = None, transaction_mode: Optional[str] = None,
) -> SqliteDatabaseWrapper:
    name = file_uri or ':memory:'
    options = {'uri': True} if (file_uri and str(file_uri).startswith('file:')) else {}
    settings_dict = connections.configure_settings({
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': name,
            'OPTIONS': {
                **options, 'check_same_thread': False, 'timeout': 10, 'transaction_mode': transaction_mode,
            },
        }
    })['default']
    conn = SqliteDatabaseWrapper(settings_dict, alias='default')
    conn.ensure_connection()
    if file_uri:
        with conn.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA busy_timeout=10000;')
    return conn


class _BaseTriajeSqliteTestCase(SimpleTestCase):
    @classmethod
    def _add_databases_failures(cls):
        return None

    @classmethod
    def _remove_databases_failures(cls):
        return None

    def setUp(self):
        super().setUp()
        self._conexion_original = getattr(connections._connections, 'default', None)
        self._sqlite_conn = _crear_conexion_sqlite()
        connections._connections.default = self._sqlite_conn
        self.addCleanup(self._restaurar_conexion)
        self.addCleanup(detener_recuperador_automatico)

        with self._sqlite_conn.schema_editor(atomic=False) as editor:
            for modelo in _MODELOS_AISLADOS:
                editor.create_model(modelo)

        self._crear_datos_base()

    def _crear_datos_base(self):
        self.rol_admin = Rol.objects.create(id_rol=ROL_ADMINISTRADOR, nombre_rol='Administrador')
        self.rol_juridico = Rol.objects.create(id_rol=ROL_JURIDICO, nombre_rol='Jurídico')
        self.rol_vet = Rol.objects.create(id_rol=ROL_VETERINARIO, nombre_rol='Veterinario')
        self.rol_pet = Rol.objects.create(id_rol=ROL_PETICIONARIO, nombre_rol='Peticionario')
        self.rol_super = Rol.objects.create(id_rol=ROL_SUPER_ADMINISTRADOR, nombre_rol='super_administrador')

        self.sede = Sede.objects.create(
            nombre='Sede Principal',
            direccion='Cra 1 # 2-3',
            ciudad='Popayán',
            telefono='3001234567',
            activo=True,
        )
        self.sede_secundaria = Sede.objects.create(
            nombre='Sede Norte',
            direccion='Cra 9 # 15-20',
            ciudad='Popayán',
            telefono='3007654321',
            activo=True,
        )
        self.sede_inactiva = Sede.objects.create(
            nombre='Sede Cerrada',
            direccion='Calle 99 # 1-1',
            ciudad='Popayán',
            telefono='3000000000',
            activo=False,
        )

        self.admin = Usuarios.objects.create(
            id_rol=self.rol_admin, id_sede=self.sede,
            nombre='Admin', apellido='Uno', email='admin@triaje.test', password='x', activo=True,
        )
        self.juridico = Usuarios.objects.create(
            id_rol=self.rol_juridico, id_sede=self.sede,
            nombre='Juridico', apellido='Uno', email='juridico@triaje.test', password='x', activo=True,
        )
        self.super_admin = Usuarios.objects.create(
            id_rol=self.rol_super, id_sede=self.sede,
            nombre='Super', apellido='Admin', email='super@triaje.test', password='x', activo=True,
        )
        self.vet_1 = Usuarios.objects.create(
            id_rol=self.rol_vet, id_sede=self.sede,
            nombre='Laura', apellido='Vet', email='vet1@triaje.test', password='x', activo=True,
        )
        self.vet_2 = Usuarios.objects.create(
            id_rol=self.rol_vet, id_sede=self.sede,
            nombre='Carlos', apellido='Vet', email='vet2@triaje.test', password='x', activo=True,
        )
        self.vet_inactivo = Usuarios.objects.create(
            id_rol=self.rol_vet, id_sede=self.sede,
            nombre='Inactivo', apellido='Vet', email='vet_off@triaje.test', password='x', activo=False,
        )
        self.vet_sede_inactiva = Usuarios.objects.create(
            id_rol=self.rol_vet, id_sede=self.sede_inactiva,
            nombre='SedeOff', apellido='Vet', email='vet_sede_off@triaje.test', password='x', activo=True,
        )
        self.ciudadano = Usuarios.objects.create(
            id_rol=self.rol_pet, id_sede=self.sede,
            nombre='Ciudadano', apellido='Demo', email='ciudadano@triaje.test', password='x', activo=True,
        )

        self.tipo = TipoPeticion.objects.create(nombre='Maltrato Animal', activo=True)
        self.estado_rev = EstadoPeticiones.objects.create(nombre='En revisión', activo=True, orden=1)
        self.estado_asig = EstadoPeticiones.objects.create(nombre='Asignada', activo=True, orden=2)
        self.estado_fin = EstadoPeticiones.objects.create(nombre='Finalizada', activo=True, orden=6)
        self.estado_dup = EstadoPeticiones.objects.create(nombre='Duplicada', activo=True, orden=7)
        self.ubic = Ubicaciones.objects.create(
            direccion='Calle 5 # 10-20',
            latitud=2.4448000,
            longitud=-76.6147000,
        )

    def _restaurar_conexion(self):
        try:
            if getattr(self, '_sqlite_conn', None) is not None:
                self._sqlite_conn.close()
        finally:
            connections._connections.default = self._conexion_original

    def _crear_peticion(
        self,
        *,
        descripcion='Perro herido con sangrado',
        prioridad=None,
        asignado_a=None,
        estado=None,
        fecha_asignacion=None,
        con_evidencia=True,
    ):
        p = Peticiones.objects.create(
            id_tipo=self.tipo,
            id_ubicacion=self.ubic,
            id_estado=estado or self.estado_rev,
            responsable=self.ciudadano,
            asignado_a=asignado_a,
            descripcion=descripcion,
            prioridad=prioridad,
            fecha_asignacion=fecha_asignacion,
            foto='https://res.cloudinary.com/demo/image/upload/perro.jpg' if con_evidencia else None,
        )
        p.numero_radicado = f'PET-2026-{str(p.id_peticion).zfill(5)}'
        p.save(update_fields=['numero_radicado'])
        if con_evidencia:
            EvidenciaPeticiones.objects.create(
                id_peticion=p,
                ruta_archivo='https://res.cloudinary.com/demo/image/upload/perro.jpg',
                descripcion='Evidencia #1 de la petición',
            )
        return p


class TriajeDespachoYResilienciaTests(_BaseTriajeSqliteTestCase):
    @override_settings(TRIAJE_N8N_ACTIVO=False)
    def test_iniciar_peticion_con_triaje_desactivado_conserva_contrato_y_no_envia(self):
        factory = APIRequestFactory()
        payload = {
            'id_tipo': self.tipo.id_tipo,
            'descripcion': 'Animal en aparente estado de abandono en via publica.',
            'direccion': 'Calle 5 # 10-20',
            'latitud': '2.4448000',
            'longitud': '-76.6147000',
            'foto': 'https://res.cloudinary.com/demo/image/upload/perro.jpg',
        }
        request = factory.post('/api/peticiones/iniciar/', payload, format='json')
        force_authenticate(request, user=self.ciudadano)

        with patch('peticiones.api.views.detectar_y_registrar_duplicados_al_iniciar'), \
             patch('triaje_ia.services.requests.post') as mock_post:
            response = IniciarPeticionView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(set(response.data.keys()), CLAVES_RESPUESTA_INICIAR)
        self.assertEqual(TriajePeticion.objects.count(), 0)
        mock_post.assert_not_called()

    @override_settings(TRIAJE_N8N_ACTIVO=True)
    def test_fallo_no_controlado_en_triaje_nunca_impide_registrar_denuncia(self):
        factory = APIRequestFactory()
        payload = {
            'id_tipo': self.tipo.id_tipo,
            'descripcion': 'Perro lesionado en la esquina del parque central.',
            'latitud': '2.4448000',
            'longitud': '-76.6147000',
        }
        request = factory.post('/api/peticiones/iniciar/', payload, format='json')
        force_authenticate(request, user=self.ciudadano)

        with patch('peticiones.api.views.detectar_y_registrar_duplicados_al_iniciar'), \
             patch('triaje_ia.services.encolar_y_despachar_triaje_peticion', side_effect=RuntimeError('Error fatal simulado')):
            response = IniciarPeticionView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Peticiones.objects.count(), 1)

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_WEBHOOK_SECRET='secreto-webhook-test',
        TRIAJE_SERVICE_TOKEN='token-servicio-super-secreto',
        TRIAJE_N8N_ASYNC_THREAD=False,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=True,
    )
    def test_iniciar_peticion_despacha_idempotente_con_dispatch_token_sin_exponer_secretos(self):
        factory = APIRequestFactory()
        payload = {
            'id_tipo': self.tipo.id_tipo,
            'descripcion': 'Perro atropellado con sangrado activo en avenida principal.',
            'direccion': 'Calle 5 # 10-20',
            'latitud': '2.4448000',
            'longitud': '-76.6147000',
            'foto': 'https://res.cloudinary.com/demo/image/upload/herido.jpg',
        }
        request = factory.post('/api/peticiones/iniciar/', payload, format='json')
        force_authenticate(request, user=self.ciudadano)

        mock_resp = MagicMock(status_code=200, text='{"ok":true}')
        with patch('peticiones.api.views.detectar_y_registrar_duplicados_al_iniciar'), \
             patch('triaje_ia.services.requests.post', return_value=mock_resp) as mock_post:
            response = IniciarPeticionView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        id_pet = response.data['id_peticion']
        triaje = TriajePeticion.objects.get(peticion_id=id_pet)
        self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_ENVIADO)
        self.assertEqual(triaje.estado_analisis, TriajePeticion.ANALISIS_EN_PROCESO)
        self.assertEqual(triaje.intentos_envio, 1)
        self.assertTrue(bool(triaje.despacho_token))
        self.assertIsNone(triaje.lease_expira_en)
        mock_post.assert_called_once()

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs['headers']['X-Vetericano-Triaje-Secret'], 'secreto-webhook-test')
        self.assertEqual(kwargs['headers']['X-Idempotency-Key'], triaje.clave_idempotencia)
        self.assertEqual(kwargs['headers']['X-Triaje-Dispatch-Token'], triaje.despacho_token)
        self.assertEqual(kwargs['json']['despacho_token'], triaje.despacho_token)
        self.assertNotIn('token-servicio-super-secreto', str(kwargs['json']))

        # Segundo despacho debe ser idempotente
        with patch('triaje_ia.services.requests.post') as mock_post_2:
            res_rep = encolar_y_despachar_triaje_peticion(id_pet, async_dispatch=False)
        self.assertTrue(res_rep['idempotente'])
        self.assertEqual(TriajePeticion.objects.filter(peticion_id=id_pet).count(), 1)
        mock_post_2.assert_not_called()

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_WEBHOOK_SECRET='secreto-super-confidencial',
        TRIAJE_N8N_MAX_REINTENTOS=3,
        TRIAJE_N8N_BACKOFF_BASE_SEGUNDOS=10.0,
        TRIAJE_N8N_ASYNC_THREAD=False,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=True,
    )
    def test_recuperacion_trabajos_interrumpidos_lease_y_timeout_analisis(self):
        # 1. Trabajo interrumpido en ENVIANDO (lease expirado tras reinicio de worker)
        pet_lease = self._crear_peticion(descripcion='Caso interrumpido en envio')
        triaje_lease = TriajePeticion.objects.create(
            peticion=pet_lease,
            clave_idempotencia=f'triaje-pet-{pet_lease.id_peticion}',
            estado_envio=TriajePeticion.ENVIO_ENVIANDO,
            estado_analisis=TriajePeticion.ANALISIS_PENDIENTE,
            intentos_envio=1,
            max_intentos=3,
            lease_expira_en=_ahora_local() - timedelta(seconds=30),
            despacho_token='token-viejo-worker-caido',
        )

        # 2. Trabajo EN_PROCESO sin callback de n8n tras expirar timeout de análisis
        pet_proc = self._crear_peticion(descripcion='Caso colgado en n8n sin callback')
        triaje_proc = TriajePeticion.objects.create(
            peticion=pet_proc,
            clave_idempotencia=f'triaje-pet-{pet_proc.id_peticion}',
            estado_envio=TriajePeticion.ENVIO_ENVIADO,
            estado_analisis=TriajePeticion.ANALISIS_EN_PROCESO,
            intentos_envio=1,
            max_intentos=3,
            enviado_en=_ahora_local() - timedelta(seconds=600),
            despacho_token='token-n8n-caido',
        )

        # 3. Trabajo PENDIENTE tras reinicio antes de enviar
        pet_pend = self._crear_peticion(descripcion='Caso pendiente tras reinicio')
        triaje_pend = TriajePeticion.objects.create(
            peticion=pet_pend,
            clave_idempotencia=f'triaje-pet-{pet_pend.id_peticion}',
            estado_envio=TriajePeticion.ENVIO_PENDIENTE,
            estado_analisis=TriajePeticion.ANALISIS_PENDIENTE,
            intentos_envio=0,
            max_intentos=3,
        )

        with patch('triaje_ia.services.requests.post', return_value=MagicMock(status_code=200, text='ok')) as mock_post:
            res = recuperar_trabajos_interrumpidos(limite=20)

        self.assertEqual(res['recuperados_lease'], 1)
        self.assertEqual(res['recuperados_analisis'], 1)
        self.assertEqual(res['enviados'], 3)
        self.assertEqual(mock_post.call_count, 3)

        for t in (triaje_lease, triaje_proc, triaje_pend):
            t.refresh_from_db()
            self.assertEqual(t.estado_envio, TriajePeticion.ENVIO_ENVIADO)
            self.assertEqual(t.estado_analisis, TriajePeticion.ANALISIS_EN_PROCESO)
            self.assertIsNone(t.lease_expira_en)

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_MAX_REINTENTOS=2,
        TRIAJE_N8N_BACKOFF_BASE_SEGUNDOS=10.0,
        TRIAJE_N8N_ASYNC_THREAD=False,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=True,
    )
    def test_backoff_y_fallo_definitivo_tras_agotar_reintentos(self):
        pet = self._crear_peticion()
        with patch(
            'triaje_ia.services.requests.post',
            side_effect=requests.Timeout('Timeout Authorization: Bearer secreto-no-guardar'),
        ):
            encolar_y_despachar_triaje_peticion(pet, async_dispatch=False)

        triaje = TriajePeticion.objects.get(peticion=pet)
        self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_FALLO_TEMPORAL)
        self.assertEqual(triaje.intentos_envio, 1)
        self.assertNotIn('secreto-no-guardar', triaje.ultimo_error_envio)

        # Antes del vencimiento del backoff no se reintenta
        with patch('triaje_ia.services.requests.post') as mock_skip:
            res_skip = procesar_reintentos_pendientes()
        self.assertEqual(res_skip['procesados'], 0)
        mock_skip.assert_not_called()

        # Al vencer backoff y seguir fallando -> FALLO_DEFINITIVO
        triaje.proximo_reintento_en = _ahora_local() - timedelta(seconds=1)
        triaje.save(update_fields=['proximo_reintento_en'])
        with patch('triaje_ia.services.requests.post', return_value=MagicMock(status_code=503, text='Down')):
            procesar_reintentos_pendientes()

        triaje.refresh_from_db()
        self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_FALLO_DEFINITIVO)
        self.assertEqual(triaje.intentos_envio, 2)
        self.assertEqual(triaje.ultimo_codigo_http, 503)

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_ASYNC_THREAD=False,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=True,
    )
    def test_concurrencia_en_despacho_solo_envia_una_vez(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'triaje_concurrente.sqlite3')
            conn_main = _crear_conexion_sqlite(file_uri=db_path)
            connections._connections.default = conn_main
            try:
                with conn_main.schema_editor(atomic=False) as editor:
                    for modelo in _MODELOS_AISLADOS:
                        editor.create_model(modelo)

                rol_pet = Rol.objects.create(id_rol=ROL_PETICIONARIO, nombre_rol='Peticionario')
                sede = Sede.objects.create(
                    nombre='Sede Concurrencia', direccion='Cra 1', ciudad='Popayán', telefono='300', activo=True,
                )
                ciudadano = Usuarios.objects.create(
                    id_rol=rol_pet, id_sede=sede, nombre='C', email='c_conc@triaje.test', password='x', activo=True,
                )
                tipo = TipoPeticion.objects.create(nombre='Maltrato', activo=True)
                est = EstadoPeticiones.objects.create(nombre='En revisión', activo=True, orden=1)
                ubic = Ubicaciones.objects.create(direccion='Calle 1', latitud=2.44, longitud=-76.6)
                pet = Peticiones.objects.create(
                    id_tipo=tipo, id_ubicacion=ubic, id_estado=est, responsable=ciudadano,
                    descripcion='Caso concurrente', numero_radicado='PET-2026-99001',
                )
                triaje = TriajePeticion.objects.create(
                    peticion=pet,
                    clave_idempotencia=f'triaje-pet-{pet.id_peticion}',
                    estado_envio=TriajePeticion.ENVIO_PENDIENTE,
                )

                llamadas_http = []
                http_lock = threading.Lock()
                barrera = threading.Barrier(4)

                def _post_lento(*args, **kwargs):
                    with http_lock:
                        llamadas_http.append(kwargs.get('headers', {}).get('X-Triaje-Dispatch-Token'))
                    time.sleep(0.05)
                    return MagicMock(status_code=200, text='ok')

                def _worker(_idx):
                    conn_thread = _crear_conexion_sqlite(file_uri=db_path)
                    connections._connections.default = conn_thread
                    try:
                        barrera.wait(timeout=3.0)
                        return despachar_triaje_por_id(triaje.id_triaje)
                    finally:
                        conn_thread.close()

                with patch('triaje_ia.services.requests.post', side_effect=_post_lento):
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        resultados = list(pool.map(_worker, range(4)))

                self.assertEqual(len(llamadas_http), 1)
                enviados = [r for r in resultados if r.get('enviado')]
                self.assertEqual(len(enviados), 1)
                triaje.refresh_from_db()
                self.assertEqual(triaje.intentos_envio, 1)
                self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_ENVIADO)
            finally:
                conn_main.close()
                connections._connections.default = self._sqlite_conn

    def test_recuperador_automatico_no_arranca_en_cli_y_arranca_en_servidor(self):
        self.assertTrue(_es_proceso_cli_excluido(['manage.py', 'test']))
        self.assertTrue(_es_proceso_cli_excluido(['manage.py', 'makemigrations']))
        self.assertFalse(_es_proceso_cli_excluido(['gunicorn', 'vetericano.wsgi:application']))

        with override_settings(TRIAJE_N8N_ACTIVO=False):
            self.assertFalse(iniciar_recuperador_automatico_si_aplica())

        with override_settings(TRIAJE_N8N_ACTIVO=True, TRIAJE_RECOVERY_INTERVAL_SEGUNDOS=5.0), \
             patch('triaje_ia.recovery._es_proceso_cli_excluido', return_value=False), \
             patch('triaje_ia.recovery._ejecutar_ciclo_recuperacion'):
            arrancado = iniciar_recuperador_automatico_si_aplica()
            self.assertTrue(arrancado)
            detener_recuperador_automatico()


class TriajeEndpointsAutenticacionYSedeTests(_BaseTriajeSqliteTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.peticion = self._crear_peticion(prioridad='Media')

    @override_settings(TRIAJE_SERVICE_TOKEN='token-interno-valido-123')
    def test_endpoints_internos_rechazan_sin_token_o_token_invalido(self):
        url = f'/api/triaje-ia/interno/peticiones/{self.peticion.id_peticion}/resultado/'
        self.assertEqual(
            self.client.post(url, {'prioridad_sugerida': 'ALTA'}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assertEqual(
            self.client.post(
                url,
                {'prioridad_sugerida': 'ALTA'},
                format='json',
                HTTP_X_VETERICANO_TRIAJE_TOKEN='token-incorrecto',
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    @override_settings(TRIAJE_SERVICE_TOKEN='')
    def test_endpoints_internos_fail_closed_si_token_no_configurado(self):
        resp = self.client.get(
            '/api/triaje-ia/interno/veterinarios/carga/',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='cualquiera',
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(TRIAJE_SERVICE_TOKEN='token-interno-valido-123')
    def test_token_servicio_tiene_permisos_minimos_y_no_accede_a_otras_apps(self):
        resp_ok = self.client.get(
            '/api/triaje-ia/interno/veterinarios/carga/',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123',
        )
        self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)

        resp_pet = self.client.get(
            '/api/peticiones/listar/',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123',
        )
        self.assertEqual(resp_pet.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(TRIAJE_SERVICE_TOKEN='token-interno-valido-123')
    def test_callback_obsoleto_y_anti_regresion_retornan_409_e_idempotencia_200(self):
        TriajePeticion.objects.create(
            peticion=self.peticion,
            clave_idempotencia=f'triaje-pet-{self.peticion.id_peticion}',
            despacho_token='token-vigente-2',
            estado_envio=TriajePeticion.ENVIO_ENVIADO,
            estado_analisis=TriajePeticion.ANALISIS_EN_PROCESO,
        )
        url = f'/api/triaje-ia/interno/peticiones/{self.peticion.id_peticion}/resultado/'

        # 1. Callback obsoleto con despacho_token antiguo -> 409 Conflict
        resp_stale = self.client.post(
            url,
            {
                'despacho_token': 'token-viejo-1',
                'estado_analisis': 'COMPLETADO',
                'prioridad_sugerida': 'BAJA',
                'justificacion': 'Ejecucion vieja',
            },
            format='json',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123',
        )
        self.assertEqual(resp_stale.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(resp_stale.data['callback_obsoleto'])

        # 2. Callback válido -> 200 OK y version_resultado = 1
        body_ok = {
            'despacho_token': 'token-vigente-2',
            'n8n_execution_id': 'exec-200',
            'estado_analisis': 'COMPLETADO',
            'prioridad_sugerida': 'Crítica',
            'nivel_confianza': 0.91,
            'justificacion': 'Lesion severa confirmada.',
            'veterinario_recomendado_id': self.vet_2.id_usuario,
        }
        resp_ok = self.client.post(
            url, body_ok, format='json', HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123'
        )
        self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_ok.data['prioridad_sugerida'], 'URGENTE')
        self.assertEqual(resp_ok.data['version_resultado'], 1)
        self.assertTrue(es_prioridad_urgente(resp_ok.data['prioridad_sugerida']))

        # 3. Replay exacto del mismo execution_id -> 200 idempotente (no incrementa version_resultado)
        resp_replay = self.client.post(
            url, body_ok, format='json', HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123'
        )
        self.assertEqual(resp_replay.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_replay.data['version_resultado'], 1)

        # 4. Callback diferente intentando degradar a PARCIAL -> 409 Conflict
        resp_downgrade = self.client.post(
            url,
            {
                'despacho_token': 'token-vigente-2',
                'n8n_execution_id': 'exec-999-otro',
                'estado_analisis': 'PARCIAL',
                'justificacion': 'Intento de degradacion',
            },
            format='json',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123',
        )
        self.assertEqual(resp_downgrade.status_code, status.HTTP_409_CONFLICT)

        # Peticiones original intacta
        self.peticion.refresh_from_db()
        self.assertEqual(self.peticion.prioridad, 'Media')
        self.assertIsNone(self.peticion.asignado_a_id)

    def test_sede_inactiva_tope_carga_y_limites_asignacion_en_recomendacion(self):
        # vet_sede_inactiva pertenece a sede_inactiva (activo=False) -> nunca aparece
        # vet_inactivo tiene activo=False -> nunca aparece
        carga = calcular_carga_veterinarios(id_peticion=self.peticion.id_peticion)
        ids = {v['id_veterinario'] for v in carga['veterinarios']}
        self.assertEqual(ids, {self.vet_1.id_usuario, self.vet_2.id_usuario})
        self.assertTrue(carga['asignacion_permitida'])

        # Poner carga a vet_1 y vet_2 y probar tope TRIAJE_MAX_CASOS_ACTIVOS_VETERINARIO=1
        self._crear_peticion(asignado_a=self.vet_1, estado=self.estado_asig, prioridad='ALTA')
        self._crear_peticion(asignado_a=self.vet_2, estado=self.estado_asig, prioridad='MEDIA')
        with override_settings(TRIAJE_MAX_CASOS_ACTIVOS_VETERINARIO=1):
            carga_tope = calcular_carga_veterinarios(id_peticion=self.peticion.id_peticion)
        self.assertIsNone(carga_tope['veterinario_recomendado'])
        self.assertTrue(all(v['saturado'] for v in carga_tope['veterinarios']))

        # Petición marcada como duplicada -> asignacion_permitida=False y no recomienda veterinario
        pet_dup = self._crear_peticion(estado=self.estado_dup)
        carga_dup = calcular_carga_veterinarios(id_peticion=pet_dup.id_peticion)
        self.assertFalse(carga_dup['asignacion_permitida'])
        self.assertIsNone(carga_dup['veterinario_recomendado'])

        # Petición asignada hace < 5 días -> bloqueada por regla DIAS_PARA_REASIGNAR
        pet_reciente = self._crear_peticion(
            asignado_a=self.vet_1,
            estado=self.estado_asig,
            fecha_asignacion=_ahora_local() - timedelta(days=1),
        )
        carga_reciente = calcular_carga_veterinarios(id_peticion=pet_reciente.id_peticion)
        self.assertFalse(carga_reciente['asignacion_permitida'])
        self.assertIsNone(carga_reciente['veterinario_recomendado'])

        # Si callback intenta recomendar vet sobre petición duplicada, se ignora el veterinario
        triaje_dup = registrar_resultado_triaje(
            pet_dup.id_peticion,
            {'prioridad_sugerida': 'ALTA', 'veterinario_recomendado_id': self.vet_2.id_usuario},
        )
        self.assertIsNone(triaje_dup.veterinario_recomendado_id)

    def test_permisos_roles_admin_juridico_superadmin_veterinario_ciudadano(self):
        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {'prioridad_sugerida': 'ALTA', 'justificacion': 'Caso prioritario.'},
        )
        url = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/'

        for u_denegado in (self.ciudadano, self.vet_1, self.super_admin):
            self.client.force_authenticate(user=u_denegado)
            self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

        for u_permitido in (self.juridico, self.admin):
            self.client.force_authenticate(user=u_permitido)
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(resp.data['prioridad_sugerida'], 'ALTA')

    @override_settings(TRIAJE_SERVICE_TOKEN='token-interno-valido-123')
    def test_reutiliza_deteccion_duplicados_con_dinov2(self):
        pet_ant = self._crear_peticion(descripcion='Perro herido similar')
        fake_det = {
            'posible_duplicado': True,
            'total_candidatos': 1,
            'posibles_duplicados': [
                {
                    'id_peticion': pet_ant.id_peticion,
                    'numero_radicado': pet_ant.numero_radicado,
                    'similitud_texto': 0.72,
                    'coincidencia_texto': True,
                    'distancia_metros': 15.0,
                    'coincidencia_gps': True,
                    'coincidencia_imagen': True,
                    'porcentaje_similitud_visual': 91.5,
                    'metodo_imagen': 'dinov2_small',
                    'criterios_coincidencia': ['texto', 'gps', 'imagen'],
                    'score_combinado': 0.89,
                    'estado': 'En revisión',
                }
            ],
            'parametros': {'dinov2_activo': True, 'metodo_imagen': 'dinov2_small'},
        }
        with patch('triaje_ia.services.detectar_duplicados_desde_solicitud', return_value=fake_det) as mock_det, \
             patch('triaje_ia.services.registrar_alertas_desde_deteccion') as mock_reg:
            mock_reg.return_value = MagicMock(creadas=1, actualizadas=0, respetadas_decision_previa=0)
            res = ejecutar_deteccion_duplicados_triaje(self.peticion.id_peticion, usar_dinov2=True)

        mock_det.assert_called_once_with(
            id_peticion=self.peticion.id_peticion,
            usar_dinov2=True,
            incluir_imagenes=True,
            incluir_evidencias=True,
        )
        self.assertTrue(res['posible_duplicado'])
        self.assertEqual(res['total_candidatos'], 1)
        self.assertEqual(res['posibles_duplicados'][0]['metodo_imagen'], 'dinov2_small')

    def test_normalizacion_prioridades_compatibles(self):
        self.assertEqual(normalizar_prioridad_sugerida('Crítica'), 'URGENTE')
        self.assertEqual(normalizar_prioridad_sugerida('Emergencia'), 'URGENTE')
        self.assertEqual(normalizar_prioridad_sugerida('alta'), 'ALTA')
        self.assertEqual(normalizar_prioridad_sugerida('Normal'), 'MEDIA')
        self.assertEqual(normalizar_prioridad_sugerida('baja'), 'BAJA')
        self.assertIsNone(normalizar_prioridad_sugerida('desconocida'))

    def test_aislamiento_por_sede_en_consulta_y_reintento(self):
        sede_b = Sede.objects.create(
            nombre='Sede Norte Demo',
            direccion='Carrera 10 # 20-30',
            ciudad='Cali',
            telefono='3110000000',
            activo=True,
        )
        juridico_b = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=sede_b,
            nombre='Juridico',
            apellido='SedeB',
            email='juridico_b@triaje.test',
            password='x',
            activo=True,
        )
        juridico_sede_inactiva = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=self.sede_inactiva,
            nombre='Juridico',
            apellido='Off',
            email='juridico_off@triaje.test',
            password='x',
            activo=True,
        )
        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {'prioridad_sugerida': 'MEDIA', 'justificacion': 'Evaluación inicial.'},
        )
        url_detalle = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/'
        url_reintentar = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/reintentar/'

        with override_settings(TRIAJE_AISLAR_POR_SEDE=True, TRIAJE_N8N_ACTIVO=False):
            # Misma sede -> permitido en consulta y en reintento (responde 200 porque TRIAJE_N8N_ACTIVO=False retorna motivo='desactivado', no 403)
            self.client.force_authenticate(user=self.juridico)
            self.assertEqual(self.client.get(url_detalle).status_code, status.HTTP_200_OK)
            resp_reintento = self.client.post(url_reintentar)
            self.assertEqual(resp_reintento.status_code, status.HTTP_200_OK)
            self.assertFalse(resp_reintento.data['ejecutado'])
            self.assertEqual(resp_reintento.data['motivo'], 'DESACTIVADO')

            # Otra sede -> denegado (403) tanto en consulta como en reintento
            self.client.force_authenticate(user=juridico_b)
            self.assertEqual(self.client.get(url_detalle).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self.client.post(url_reintentar).status_code, status.HTTP_403_FORBIDDEN)

            # Sede inactiva -> denegado (403)
            self.client.force_authenticate(user=juridico_sede_inactiva)
            self.assertEqual(self.client.get(url_detalle).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self.client.post(url_reintentar).status_code, status.HTTP_403_FORBIDDEN)

    def test_guardas_recuperador_runserver_reloader_y_fork(self):
        import os
        import triaje_ia.recovery as rec

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('RUN_MAIN', None)
            self.assertTrue(_es_proceso_cli_excluido(['manage.py', 'runserver']))
            self.assertFalse(_es_proceso_cli_excluido(['manage.py', 'runserver', '--noreload']))

        with patch.dict(os.environ, {'RUN_MAIN': 'true'}, clear=False):
            self.assertFalse(_es_proceso_cli_excluido(['manage.py', 'runserver']))

        with override_settings(TRIAJE_N8N_ACTIVO=True, TRIAJE_RECOVERY_IN_PROCESS=False):
            self.assertFalse(iniciar_recuperador_automatico_si_aplica())

        detener_recuperador_automatico()
        try:
            with patch('triaje_ia.recovery._bucle_recuperador', side_effect=lambda: rec._STOP_EVENT.wait(2.0)):
                with patch('triaje_ia.recovery.os.getpid', return_value=11111):
                    self.assertTrue(iniciar_recuperador_automatico_si_aplica(force=True))
                    hilo_padre = rec._RECOVERY_THREAD
                    self.assertEqual(rec._STARTED_PID, 11111)
                    # Mismo PID -> no duplica hilo
                    self.assertFalse(iniciar_recuperador_automatico_si_aplica(force=True))

                with patch('triaje_ia.recovery.os.getpid', return_value=22222):
                    # Nuevo PID tras fork() -> limpia referencia heredada y crea hilo nuevo
                    self.assertTrue(iniciar_recuperador_automatico_si_aplica(force=True))
                    self.assertEqual(rec._STARTED_PID, 22222)
                    self.assertIsNot(rec._RECOVERY_THREAD, hilo_padre)
        finally:
            detener_recuperador_automatico()


class TriajeIntegracionJuridicaYDecisionesTests(_BaseTriajeSqliteTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.peticion = self._crear_peticion(prioridad='Media', descripcion='Perro atropellado con sangrado')

    def test_denuncia_antigua_sin_triaje_retorna_404_con_metadatos_y_aisla_sede(self):
        sede_b = Sede.objects.create(
            nombre='Sede Sur', direccion='Cra 5 # 1-2', ciudad='Popayán', telefono='3001112233', activo=True,
        )
        juridico_b = Usuarios.objects.create(
            id_rol=self.rol_juridico, id_sede=sede_b, nombre='Jur', apellido='B',
            email='jur_b_ant@triaje.test', password='x', activo=True,
        )
        url = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/'

        with override_settings(TRIAJE_N8N_ACTIVO=False, TRIAJE_AISLAR_POR_SEDE=True):
            self.client.force_authenticate(user=self.juridico)
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
            self.assertFalse(resp.data['existe_triaje'])
            self.assertTrue(resp.data['existe_peticion'])
            self.assertFalse(resp.data['automatizacion_activa'])

            # Otra sede no debe ni descubrir si existe o no triaje -> 403
            self.client.force_authenticate(user=juridico_b)
            self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

            # Rol no autorizado -> 403
            self.client.force_authenticate(user=self.vet_1)
            self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

    def test_reintentar_con_automatizacion_desactivada_no_despacha_aun_con_forzar(self):
        url = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/reintentar/'
        self.client.force_authenticate(user=self.juridico)
        with override_settings(TRIAJE_N8N_ACTIVO=False), patch('triaje_ia.services.requests.post') as mock_post:
            resp = self.client.post(url, {'forzar': True}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['ejecutado'])
        self.assertEqual(resp.data['motivo'], 'DESACTIVADO')
        self.assertFalse(resp.data['automatizacion_activa'])
        mock_post.assert_not_called()

    def test_serializacion_hallazgos_contradiccion_sede_carga_y_duplicados_enriquecidos(self):
        pet_dup_ref = self._crear_peticion(descripcion='Caso previo similar')
        alerta = PeticionPosibleDuplicado.objects.create(
            peticion_menor=self.peticion,
            peticion_mayor=pet_dup_ref,
            peticion_origen=self.peticion,
            similitud_texto=0.81,
            coincidencia_texto=True,
            distancia_metros=24.0,
            coincidencia_gps=True,
            porcentaje_similitud_visual=88.5,
            metodo_imagen='dinov2_small',
            coincidencia_imagen=True,
            score_combinado=0.86,
            criterios_coincidencia=['texto', 'gps', 'imagen'],
        )
        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {
                'estado_analisis': 'COMPLETADO',
                'prioridad_sugerida': 'URGENTE',
                'nivel_confianza': 0.87,
                'justificacion': 'Se reporta atropellamiento y la fotografía muestra lesión compatible.',
                'resumen_texto': 'Ciudadano reporta perro atropellado con sangrado.',
                'resumen_vision': 'Canino recostado con laceración visible.',
                'signos_reportados': ['Sangrado activo', 'Atropellamiento'],
                'signos_fotografia': ['Laceración en extremidad'],
                'contradiccion_foto_descripcion': True,
                'advertencias_contradiccion': ['La fotografía muestra un felino aunque la descripción indica un perro.'],
                'posibles_duplicados': [
                    {
                        'id_peticion': pet_dup_ref.id_peticion,
                        'numero_radicado': pet_dup_ref.numero_radicado,
                        'similitud_texto': 0.81,
                        'porcentaje_similitud_visual': 88.5,
                        'distancia_metros': 24.0,
                        'metodo_imagen': 'dinov2_small',
                    }
                ],
                'veterinario_recomendado_id': self.vet_1.id_usuario,
                'justificacion_recomendacion': 'Menor carga activa en la sede.',
                'carga_veterinarios_snapshot': [
                    {
                        'id_veterinario': self.vet_1.id_usuario,
                        'nombre_completo': 'Laura Vet',
                        'id_sede': self.sede.id_sede,
                        'sede_nombre': self.sede.nombre,
                        'total_asignados': 2,
                        'pendientes': 1,
                        'en_proceso': 0,
                        'finalizadas': 1,
                        'carga_activa': 1,
                        'urgentes_activos': 0,
                        'saturado': False,
                        'porcentaje_completado': 50,
                    }
                ],
            },
        )
        self.client.force_authenticate(user=self.juridico)
        resp = self.client.get(f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['tiene_fotografias'] is False)
        self.assertEqual(resp.data['signos_reportados'], ['Sangrado activo', 'Atropellamiento'])
        self.assertEqual(resp.data['signos_fotografia'], ['Laceración en extremidad'])
        self.assertTrue(resp.data['contradiccion_foto_descripcion'])
        self.assertEqual(len(resp.data['advertencias_contradiccion']), 1)
        self.assertEqual(resp.data['veterinario_recomendado_sede'], self.sede.nombre)
        self.assertEqual(resp.data['veterinario_recomendado_carga']['carga_activa'], 1)
        self.assertEqual(resp.data['posibles_duplicados_detalle'][0]['id_alerta'], alerta.id_alerta)
        self.assertEqual(resp.data['posibles_duplicados_detalle'][0]['estado_revision'], 'PENDIENTE')

    def test_no_persiste_signos_fotografia_cuando_peticion_no_tiene_fotos(self):
        pet_sin_foto = Peticiones.objects.create(
            id_tipo=self.tipo,
            id_ubicacion=self.ubic,
            id_estado=self.estado_rev,
            responsable=self.ciudadano,
            descripcion='Animal sin evidencia fotográfica.',
            prioridad='Media',
        )
        triaje = registrar_resultado_triaje(
            pet_sin_foto.id_peticion,
            {
                'prioridad_sugerida': 'MEDIA',
                'signos_reportados': ['Desnutrición reportada'],
                'signos_fotografia': ['Lesión inventada'],
            },
        )
        self.assertEqual(triaje.signos_reportados, ['Desnutrición reportada'])
        self.assertEqual(triaje.signos_fotografia, [])

    def test_decision_humana_aceptar_y_rechazar_prioridad_y_veterinario_con_auditoria(self):
        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {
                'estado_analisis': 'COMPLETADO',
                'prioridad_sugerida': 'URGENTE',
                'justificacion': 'Riesgo vital.',
                'veterinario_recomendado_id': self.vet_1.id_usuario,
                'justificacion_recomendacion': 'Menor carga activa.',
            },
        )
        url_decision = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/decision/'

        # Roles no autorizados (Veterinario, Ciudadano, SuperAdmin) -> 403
        for u in (self.vet_1, self.ciudadano, self.super_admin):
            self.client.force_authenticate(user=u)
            self.assertEqual(
                self.client.post(
                    url_decision,
                    {'tipo_recomendacion': 'PRIORIDAD', 'decision': 'ACEPTADA'},
                    format='json',
                ).status_code,
                status.HTTP_403_FORBIDDEN,
            )

        # Jurídico acepta prioridad sugerida -> actualiza Peticiones.prioridad y guarda auditoría
        self.client.force_authenticate(user=self.juridico)
        resp_prio = self.client.post(
            url_decision,
            {
                'tipo_recomendacion': 'PRIORIDAD',
                'decision': 'ACEPTADA',
                'observacion': 'Confirmado por equipo jurídico.',
            },
            format='json',
        )
        self.assertEqual(resp_prio.status_code, status.HTTP_200_OK)
        self.peticion.refresh_from_db()
        self.assertEqual(self.peticion.prioridad, 'URGENTE')
        self.assertEqual(resp_prio.data['triaje']['decision_prioridad'], 'ACEPTADA')
        self.assertEqual(resp_prio.data['triaje']['prioridad_aplicada'], 'URGENTE')
        self.assertEqual(resp_prio.data['triaje']['decision_prioridad_por_id'], self.juridico.id_usuario)
        self.assertEqual(len(resp_prio.data['triaje']['historial_decisiones']), 1)

        # Jurídico acepta veterinario recomendado -> delega al flujo autorizado AsignarPeticionView
        resp_vet = self.client.post(
            url_decision,
            {
                'tipo_recomendacion': 'VETERINARIO',
                'decision': 'ACEPTADA',
                'observacion': 'Asignación validada según carga.',
            },
            format='json',
        )
        self.assertEqual(resp_vet.status_code, status.HTTP_200_OK)
        self.peticion.refresh_from_db()
        self.assertEqual(self.peticion.asignado_a_id, self.vet_1.id_usuario)
        self.assertEqual(self.peticion.id_estado.nombre, 'Asignada')
        self.assertEqual(resp_vet.data['triaje']['decision_veterinario'], 'ACEPTADA')
        self.assertGreaterEqual(resp_vet.data['triaje']['veterinario_recomendado_carga']['carga_activa'], 1)
        self.assertEqual(len(resp_vet.data['triaje']['historial_decisiones']), 2)
        self.assertTrue(
            HistorialPeticiones.objects.filter(
                id_peticion=self.peticion,
                tipo_cambio='TRIAJE_PRIORIDAD',
            ).exists()
        )
        self.assertTrue(
            HistorialPeticiones.objects.filter(
                id_peticion=self.peticion,
                tipo_cambio='ASIGNACION',
            ).exists()
        )

    def test_rechazar_recomendaciones_conserva_peticion_intacta_y_registra_decision(self):
        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {
                'estado_analisis': 'COMPLETADO',
                'prioridad_sugerida': 'ALTA',
                'veterinario_recomendado_id': self.vet_2.id_usuario,
            },
        )
        url_decision = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/decision/'
        self.client.force_authenticate(user=self.admin)

        resp_p = self.client.post(
            url_decision,
            {
                'tipo_recomendacion': 'PRIORIDAD',
                'decision': 'RECHAZADA',
                'observacion': 'Se mantiene prioridad Media tras verificación telefónica.',
            },
            format='json',
        )
        self.assertEqual(resp_p.status_code, status.HTTP_200_OK)
        resp_v = self.client.post(
            url_decision,
            {
                'tipo_recomendacion': 'VETERINARIO',
                'decision': 'RECHAZADA',
                'observacion': 'Se asignará otro profesional manualmente.',
            },
            format='json',
        )
        self.assertEqual(resp_v.status_code, status.HTTP_200_OK)
        self.peticion.refresh_from_db()
        self.assertEqual(self.peticion.prioridad, 'Media')
        self.assertIsNone(self.peticion.asignado_a_id)
        self.assertEqual(resp_v.data['triaje']['decision_prioridad'], 'RECHAZADA')
        self.assertEqual(resp_v.data['triaje']['decision_veterinario'], 'RECHAZADA')
        self.assertEqual(len(resp_v.data['triaje']['historial_decisiones']), 2)

    def test_decision_aceptar_bloqueada_en_peticion_duplicada_o_terminal(self):
        pet_dup = self._crear_peticion(estado=self.estado_dup, prioridad='Media')
        registrar_resultado_triaje(
            pet_dup.id_peticion,
            {'estado_analisis': 'COMPLETADO', 'prioridad_sugerida': 'URGENTE'},
        )
        self.client.force_authenticate(user=self.juridico)
        resp = self.client.post(
            f'/api/triaje-ia/peticiones/{pet_dup.id_peticion}/decision/',
            {'tipo_recomendacion': 'PRIORIDAD', 'decision': 'ACEPTADA'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)

        pet_fin = self._crear_peticion(estado=self.estado_fin, prioridad='Media')
        registrar_resultado_triaje(
            pet_fin.id_peticion,
            {'estado_analisis': 'COMPLETADO', 'prioridad_sugerida': 'URGENTE'},
        )
        resp_fin = self.client.post(
            f'/api/triaje-ia/peticiones/{pet_fin.id_peticion}/decision/',
            {'tipo_recomendacion': 'PRIORIDAD', 'decision': 'ACEPTADA'},
            format='json',
        )
        self.assertEqual(resp_fin.status_code, status.HTTP_409_CONFLICT)

    def test_decision_veterinario_idempotente_y_sin_duplicar_notificaciones(self):
        from peticiones.models import HistorialAsignacionPeticion, NotificacionPeticion

        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {
                'estado_analisis': 'COMPLETADO',
                'prioridad_sugerida': 'ALTA',
                'veterinario_recomendado_id': self.vet_1.id_usuario,
            },
        )
        url_decision = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/decision/'
        self.client.force_authenticate(user=self.juridico)

        r1 = self.client.post(
            url_decision,
            {'tipo_recomendacion': 'VETERINARIO', 'decision': 'ACEPTADA', 'observacion': 'Primera vez'},
            format='json',
        )
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertTrue(r1.data['cambiada'])

        hist_pet_count = HistorialPeticiones.objects.filter(id_peticion=self.peticion, tipo_cambio='ASIGNACION').count()
        hist_asig_count = HistorialAsignacionPeticion.objects.filter(id_peticion=self.peticion).count()
        notif_count = NotificacionPeticion.objects.filter(id_peticion=self.peticion).count()

        # Repetir exactamente la misma solicitud -> idempotente, cambiada=False y cero duplicados
        r2 = self.client.post(
            url_decision,
            {'tipo_recomendacion': 'VETERINARIO', 'decision': 'ACEPTADA', 'observacion': 'Reintento HTTP'},
            format='json',
        )
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertFalse(r2.data['cambiada'])
        self.assertEqual(
            HistorialPeticiones.objects.filter(id_peticion=self.peticion, tipo_cambio='ASIGNACION').count(),
            hist_pet_count,
        )
        self.assertEqual(
            HistorialAsignacionPeticion.objects.filter(id_peticion=self.peticion).count(),
            hist_asig_count,
        )
        self.assertEqual(
            NotificacionPeticion.objects.filter(id_peticion=self.peticion).count(),
            notif_count,
        )
        self.assertEqual(len(r2.data['triaje']['historial_decisiones']), 1)

    def test_decision_veterinario_rollback_atomico_y_permisos_sede(self):
        from peticiones.models import NotificacionPeticion

        sede_b = Sede.objects.create(
            nombre='Sede Norte Test', direccion='Cra 9 # 10-20', ciudad='Popayán', telefono='3009998877', activo=True,
        )
        juridico_b = Usuarios.objects.create(
            id_rol=self.rol_juridico, id_sede=sede_b, nombre='JurB', apellido='SedeB',
            email='jur_sede_b@triaje.test', password='x', activo=True,
        )
        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {
                'estado_analisis': 'COMPLETADO',
                'prioridad_sugerida': 'ALTA',
                'veterinario_recomendado_id': self.vet_1.id_usuario,
            },
        )
        url_decision = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/decision/'
        url_reintento = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/reintentar/'

        # Jurídico de otra sede no puede decidir ni reintentar
        with override_settings(TRIAJE_AISLAR_POR_SEDE=True, TRIAJE_N8N_ACTIVO=True):
            self.client.force_authenticate(user=juridico_b)
            self.assertEqual(
                self.client.post(
                    url_decision,
                    {'tipo_recomendacion': 'VETERINARIO', 'decision': 'ACEPTADA'},
                    format='json',
                ).status_code,
                status.HTTP_403_FORBIDDEN,
            )
            self.assertEqual(
                self.client.post(url_reintento, {'forzar': True}, format='json').status_code,
                status.HTTP_403_FORBIDDEN,
            )

        # Rollback atómico: si falla el guardado después de asignar, AsignarPeticionView también se revierte
        self.client.force_authenticate(user=self.juridico)
        with patch('triaje_ia.services.TriajePeticion.save', side_effect=RuntimeError('Error simulado al guardar triaje')):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    url_decision,
                    {'tipo_recomendacion': 'VETERINARIO', 'decision': 'ACEPTADA'},
                    format='json',
                )
        self.peticion.refresh_from_db()
        self.assertIsNone(self.peticion.asignado_a_id)
        self.assertEqual(self.peticion.id_estado.nombre, self.estado_rev.nombre)
        self.assertEqual(NotificacionPeticion.objects.filter(id_peticion=self.peticion).count(), 0)

        # Veterinario recomendado inactivo -> 409 Conflict sin mutar
        self.vet_1.activo = False
        self.vet_1.save(update_fields=['activo'])
        resp_inactivo = self.client.post(
            url_decision,
            {'tipo_recomendacion': 'VETERINARIO', 'decision': 'ACEPTADA'},
            format='json',
        )
        self.assertEqual(resp_inactivo.status_code, status.HTTP_409_CONFLICT)
        self.assertIsNone(self.peticion.asignado_a_id)

    def test_decision_veterinario_compatible_con_asignacion_previa_sin_duplicar(self):
        from peticiones.models import HistorialPeticiones as HistP, NotificacionPeticion

        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {
                'estado_analisis': 'COMPLETADO',
                'veterinario_recomendado_id': self.vet_1.id_usuario,
            },
        )
        # Simular flujo legado previo: PATCH /asignar/ ya asignó al veterinario recomendado
        self.client.force_authenticate(user=self.juridico)
        r_patch = self.client.patch(
            f'/api/peticiones/{self.peticion.id_peticion}/asignar/',
            {'asignado_a': self.vet_1.id_usuario, 'motivo': 'Asignación previa'},
            format='json',
        )
        self.assertEqual(r_patch.status_code, status.HTTP_200_OK)
        notif_tras_patch = NotificacionPeticion.objects.filter(id_peticion=self.peticion).count()
        hist_asig_tras_patch = HistP.objects.filter(id_peticion=self.peticion, tipo_cambio='ASIGNACION').count()

        r_dec = self.client.post(
            f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/decision/',
            {'tipo_recomendacion': 'VETERINARIO', 'decision': 'ACEPTADA'},
            format='json',
        )
        self.assertEqual(r_dec.status_code, status.HTTP_200_OK)
        self.assertTrue(r_dec.data['cambiada'])
        self.assertEqual(NotificacionPeticion.objects.filter(id_peticion=self.peticion).count(), notif_tras_patch)
        self.assertEqual(HistP.objects.filter(id_peticion=self.peticion, tipo_cambio='ASIGNACION').count(), hist_asig_tras_patch)

    def test_listar_peticiones_expone_triaje_resumen_sin_n_plus_1_y_protege_roles(self):
        from django.test.utils import CaptureQueriesContext

        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {
                'estado_analisis': 'COMPLETADO',
                'prioridad_sugerida': 'URGENTE',
                'justificacion': 'Atropellamiento con hemorragia.',
                'veterinario_recomendado_id': self.vet_1.id_usuario,
                'carga_veterinarios_snapshot': [
                    {
                        'id_veterinario': self.vet_1.id_usuario,
                        'nombre_completo': 'Laura Vet',
                        'id_sede': self.sede.id_sede,
                        'sede_nombre': self.sede.nombre,
                        'total_asignados': 3,
                        'pendientes': 1,
                        'en_proceso': 1,
                        'finalizadas': 1,
                        'carga_activa': 2,
                        'urgentes_activos': 1,
                        'saturado': False,
                        'porcentaje_completado': 33,
                    }
                ],
            },
        )

        self.client.force_authenticate(user=self.juridico)
        # Primer llamado calienta caché de existencia de tabla
        resp_warmup = self.client.get('/api/peticiones/listar/')
        self.assertEqual(resp_warmup.status_code, status.HTTP_200_OK)

        with CaptureQueriesContext(self._sqlite_conn) as ctx_uno:
            resp_uno = self.client.get('/api/peticiones/listar/')
        self.assertEqual(resp_uno.status_code, status.HTTP_200_OK)

        # Agregar más peticiones con y sin triaje (incluida una asignada al veterinario)
        for i in range(4):
            pet_extra = self._crear_peticion(
                descripcion=f'Denuncia adicional #{i}',
                prioridad='Baja',
                asignado_a=self.vet_1 if i == 0 else None,
                estado=self.estado_asig if i == 0 else self.estado_rev,
            )
            if i < 3:
                registrar_resultado_triaje(
                    pet_extra.id_peticion,
                    {
                        'estado_analisis': 'COMPLETADO',
                        'prioridad_sugerida': 'ALTA',
                        'veterinario_recomendado_id': self.vet_2.id_usuario,
                        'carga_veterinarios_snapshot': [
                            {
                                'id_veterinario': self.vet_2.id_usuario,
                                'nombre_completo': 'Carlos Vet',
                                'id_sede': self.sede.id_sede,
                                'sede_nombre': self.sede.nombre,
                                'total_asignados': 1,
                                'pendientes': 1,
                                'en_proceso': 0,
                                'finalizadas': 0,
                                'carga_activa': 1,
                                'urgentes_activos': 0,
                                'saturado': False,
                                'porcentaje_completado': 0,
                            }
                        ],
                    },
                )

        with CaptureQueriesContext(self._sqlite_conn) as ctx_cinco:
            resp_cinco = self.client.get('/api/peticiones/listar/')
        self.assertEqual(resp_cinco.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_cinco.data), 5)
        self.assertEqual(len(ctx_cinco), len(ctx_uno))

        por_id = {item['id_peticion']: item for item in resp_cinco.data}
        item_principal = por_id[self.peticion.id_peticion]
        self.assertEqual(item_principal['prioridad'], 'Media')
        self.assertIsNone(item_principal['asignado_a'])
        self.assertEqual(item_principal['triaje_resumen']['prioridad_sugerida'], 'URGENTE')
        self.assertEqual(item_principal['triaje_resumen']['decision_prioridad'], 'PENDIENTE')
        self.assertEqual(item_principal['triaje_resumen']['veterinario_recomendado_nombre'], 'Laura Vet')
        self.assertEqual(item_principal['triaje_resumen']['veterinario_recomendado_carga']['carga_activa'], 2)
        self.assertEqual(item_principal['triaje_resumen']['decision_veterinario'], 'PENDIENTE')

        sin_triaje = [item for item in resp_cinco.data if item['triaje_resumen'] is None]
        self.assertEqual(len(sin_triaje), 1)

        # Roles no autorizados (Veterinario y Peticionario) nunca reciben triaje_resumen
        for usuario_no_auth in (self.vet_1, self.ciudadano):
            self.client.force_authenticate(user=usuario_no_auth)
            resp_rol = self.client.get('/api/peticiones/listar/')
            self.assertEqual(resp_rol.status_code, status.HTTP_200_OK)
            self.assertGreater(len(resp_rol.data), 0)
            for item in resp_rol.data:
                self.assertNotIn('triaje_resumen', item)


class TriajePrivacidadMetadatosYResilienciaAvanzadaTests(_BaseTriajeSqliteTestCase):
    def test_sanitizacion_pii_y_minimizacion_payload_n8n(self):
        from triaje_ia.services import (
            construir_payload_peticion_triaje,
            construir_url_foto_sin_metadatos_cloudinary,
            sanitizar_texto_pii_para_ia,
        )

        texto_con_pii = (
            'Soy Juan Carlos Perez, tel 3124567890, CC 1.061.789.123, '
            'correo prueba@correo.co, placa ABC123 en Calle 5 # 10-20 Barrio La Esperanza. '
            'El perro presenta sangrado activo tras atropellamiento. https://ejemplo.com/x @usuario'
        )
        sanitizado = sanitizar_texto_pii_para_ia(texto_con_pii)
        self.assertNotIn('Juan Carlos Perez', sanitizado)
        self.assertNotIn('3124567890', sanitizado)
        self.assertNotIn('1.061.789.123', sanitizado)
        self.assertNotIn('prueba@correo.co', sanitizado)
        self.assertNotIn('ABC123', sanitizado)
        self.assertNotIn('Calle 5 # 10-20', sanitizado)
        self.assertNotIn('La Esperanza', sanitizado)
        self.assertNotIn('https://ejemplo.com/x', sanitizado)
        self.assertNotIn('@usuario', sanitizado)
        self.assertIn('perro presenta sangrado activo tras atropellamiento', sanitizado)

        pet = self._crear_peticion(descripcion=texto_con_pii)
        with override_settings(TRIAJE_ENVIAR_URLS_EXTERNAS_N8N=False, TRIAJE_GEMINI_ENVIAR_FOTOS=False):
            payload = construir_payload_peticion_triaje(pet, despacho_token='tok-123')

        self.assertNotIn('ubicacion', payload)
        self.assertNotIn('numero_radicado', payload)
        self.assertNotIn('videos', payload)
        self.assertTrue(payload['tiene_fotos'])
        self.assertEqual(payload['total_fotos'], 1)
        self.assertFalse(payload['enviar_fotos_gemini'])
        self.assertEqual(payload['foto_segura_path'], '')
        self.assertEqual(payload['fotos'], [])
        self.assertIsNone(payload['foto'])
        self.assertNotIn('3124567890', payload['descripcion'])
        self.assertIn('sangrado activo', payload['descripcion'])

        with override_settings(
            TRIAJE_ENVIAR_URLS_EXTERNAS_N8N=True,
            TRIAJE_GEMINI_ENVIAR_FOTOS=True,
            TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=False,
        ):
            payload_real = construir_payload_peticion_triaje(pet, despacho_token='tok-123')
        self.assertFalse(payload_real['enviar_fotos_gemini'])
        self.assertEqual(payload_real['foto_segura_path'], '')
        self.assertEqual(payload_real['fotos'], [])

        with override_settings(
            TRIAJE_ENVIAR_URLS_EXTERNAS_N8N=False,
            TRIAJE_GEMINI_ENVIAR_FOTOS=True,
            TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=True,
        ):
            payload_con_foto = construir_payload_peticion_triaje(pet, despacho_token='tok-123')
        self.assertTrue(payload_con_foto['enviar_fotos_gemini'])
        self.assertEqual(
            payload_con_foto['foto_segura_path'],
            f'/api/triaje-ia/interno/peticiones/{pet.id_peticion}/foto-segura/',
        )

        url_strip = construir_url_foto_sin_metadatos_cloudinary(
            'https://res.cloudinary.com/demo/image/upload/v123/perro.jpg'
        )
        self.assertIn('/image/upload/fl_strip_profile,', url_strip)

    @override_settings(TRIAJE_SERVICE_TOKEN='token-interno-valido-123')
    def test_endpoint_foto_segura_elimina_metadatos_exif_y_exige_token(self):
        import io
        from PIL import Image

        img = Image.new('RGB', (64, 48), color=(180, 40, 40))
        exif = Image.Exif()
        exif[271] = 'CamaraSecreta'
        exif[272] = 'TelefonoCiudadano'
        exif[306] = '2026:11:27 10:00:00'
        buf_origen = io.BytesIO()
        img.save(buf_origen, format='JPEG', exif=exif.tobytes(), icc_profile=b'perfil-icc-fake')
        bytes_con_exif = buf_origen.getvalue()

        pet = self._crear_peticion()
        url = f'/api/triaje-ia/interno/peticiones/{pet.id_peticion}/foto-segura/'

        self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED)

        with override_settings(TRIAJE_GEMINI_ENVIAR_FOTOS=False):
            resp_bloqueado = self.client.get(url, HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123')
            self.assertEqual(resp_bloqueado.status_code, status.HTTP_403_FORBIDDEN)

        with override_settings(TRIAJE_GEMINI_ENVIAR_FOTOS=True, TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=False):
            resp = self.client.get(url, HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123')
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        with override_settings(TRIAJE_GEMINI_ENVIAR_FOTOS=True, TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=True):
            with patch('triaje_ia.services._descargar_bytes_url_validada', return_value=bytes_con_exif):
                resp = self.client.get(url, HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'image/jpeg')
        self.assertEqual(resp['Cache-Control'], 'no-store')

        with Image.open(io.BytesIO(resp.content)) as limpia:
            self.assertEqual(limpia.format, 'JPEG')
            self.assertEqual(len(limpia.getexif()), 0)
            self.assertNotIn('exif', limpia.info)
            self.assertNotIn('icc_profile', limpia.info)
            self.assertNotIn('xmp', limpia.info)

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_MAX_REINTENTOS=6,
        TRIAJE_N8N_BACKOFF_BASE_SEGUNDOS=10.0,
        TRIAJE_N8N_BACKOFF_MAX_SEGUNDOS=120.0,
        TRIAJE_N8N_ASYNC_THREAD=False,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=True,
    )
    def test_recuperacion_automatica_fallo_ia_transitorio_y_tope_backoff(self):
        from triaje_ia.services import _calcular_espera_backoff

        self.assertEqual(_calcular_espera_backoff(1, backoff_base=60.0, backoff_max=900.0), 60.0)
        self.assertEqual(_calcular_espera_backoff(5, backoff_base=60.0, backoff_max=900.0), 900.0)

        pet = self._crear_peticion(descripcion='Perro con dificultad respiratoria')
        with patch('triaje_ia.services.requests.post', return_value=MagicMock(status_code=200, text='ok')):
            res_envio = encolar_y_despachar_triaje_peticion(pet, async_dispatch=False)
        self.assertTrue(res_envio['enviado'])

        triaje = TriajePeticion.objects.get(peticion=pet)
        self.assertEqual(triaje.max_intentos, 6)

        # Callback reporta FALLO/PARCIAL de IA sin prioridad sugerida -> programa reintento automático
        registrar_resultado_triaje(
            pet.id_peticion,
            {
                'despacho_token': triaje.despacho_token,
                'estado_analisis': 'PARCIAL',
                'prioridad_sugerida': None,
                'justificacion': 'Gemini 429 temporal.',
            },
        )
        triaje.refresh_from_db()
        self.assertEqual(triaje.estado_analisis, TriajePeticion.ANALISIS_PARCIAL)
        self.assertIsNotNone(triaje.proximo_reintento_en)

        # Antes de vencer backoff no se reencola
        with patch('triaje_ia.services.requests.post') as mock_skip:
            res_antes = recuperar_trabajos_interrumpidos(limite=20)
        self.assertEqual(res_antes['recuperados_fallo_ia'], 0)
        mock_skip.assert_not_called()

        # Al vencer backoff se reencola y reenvía automáticamente
        triaje.proximo_reintento_en = _ahora_local() - timedelta(seconds=1)
        triaje.save(update_fields=['proximo_reintento_en'])
        with patch('triaje_ia.services.requests.post', return_value=MagicMock(status_code=200, text='ok')) as mock_retry:
            res_despues = recuperar_trabajos_interrumpidos(limite=20)

        self.assertEqual(res_despues['recuperados_fallo_ia'], 1)
        self.assertEqual(res_despues['enviados'], 1)
        mock_retry.assert_called_once()
        triaje.refresh_from_db()
        self.assertEqual(triaje.intentos_envio, 2)
        self.assertEqual(triaje.estado_analisis, TriajePeticion.ANALISIS_EN_PROCESO)

    def test_workflow_sanitized_json_privacidad_y_consistencia_valido(self):
        import json
        from pathlib import Path

        ruta = Path(__file__).resolve().parent / 'n8n_workflow_triaje_vetericano.sanitized.json'
        data = json.loads(ruta.read_text(encoding='utf-8'))

        self.assertFalse(data['active'])
        self.assertEqual(data['settings']['saveDataErrorExecution'], 'none')
        self.assertEqual(data['settings']['saveDataSuccessExecution'], 'none')
        self.assertFalse(data['settings']['saveManualExecutions'])
        self.assertFalse(data['settings']['saveExecutionProgress'])

        nodos = {n['name']: n for n in data['nodes']}
        self.assertIn('/foto-segura/', nodos['Download Primary Photo']['parameters']['url'])
        self.assertTrue(nodos['Download Primary Photo'].get('retryOnFail'))
        self.assertEqual(nodos['Analyze Photos Vision']['parameters']['options']['temperature'], 0)
        self.assertTrue(nodos['Analyze Photos Vision'].get('retryOnFail'))
        self.assertEqual(nodos['Analyze Text Triage']['parameters']['options']['temperature'], 0)
        self.assertTrue(nodos['Analyze Text Triage'].get('retryOnFail'))
        self.assertTrue(nodos['Detect Duplicates'].get('retryOnFail'))
        self.assertTrue(nodos['Fetch Vet Workload'].get('retryOnFail'))
        self.assertTrue(nodos['Save Triage Result'].get('retryOnFail'))
        assemble_code = nodos['Assemble Triage Result']['parameters']['jsCode']
        self.assertIn('ia_guardrail_aplicado', assemble_code)
        self.assertIn('Regla de priorizacion administrativa', assemble_code)
        self.assertIn('visionCtx.fotoAutorizada', assemble_code)
        self.assertIn('limpiarListaStrings(parsedIa.signos_detectados, 15, 80)', assemble_code)
        self.assertNotIn('numero_radicado', [a['name'] for a in nodos['Normalize Payload']['parameters']['assignments']['assignments']])
        asignaciones = nodos['Normalize Payload']['parameters']['assignments']['assignments']
        self.assertEqual(
            {a['name'] for a in asignaciones},
            {'id_peticion', 'clave_idempotencia', 'despacho_token', 'backendBaseUrl'},
        )
        self.assertIn('/?gemini=demo', nodos['Fetch Gemini Context']['parameters']['url'])
        self.assertIn('/foto-segura/?gemini=demo', nodos['Download Primary Photo']['parameters']['url'])
        self.assertIn('contexto.descripcion', nodos['Prepare Vision Context']['parameters']['jsCode'])
        conexiones = {
            nombre: [rama[0]['node'] for rama in salida['main']]
            for nombre, salida in data['connections'].items()
        }
        self.assertEqual(conexiones['Normalize Payload'], ['Fetch Gemini Context'])
        self.assertEqual(conexiones['Photo Downloaded'], ['Analyze Photos Vision', 'Prepare Vision Context'])
        self.assertEqual(conexiones['Allow Gemini Text'], ['Analyze Text Triage', 'Detect Duplicates'])

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='https://n8n-ejemplo.up.railway.app/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_ASYNC_THREAD=False,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=True,
    )
    def test_arranque_en_frio_serverless_recupera_en_segundo_intento(self):
        from triaje_ia.services import obtener_config_triaje

        cfg = obtener_config_triaje()
        self.assertEqual(cfg['timeout'], 20.0)
        self.assertEqual(cfg['backoff_base'], 30.0)
        self.assertEqual(cfg['max_reintentos'], 6)

        pet = self._crear_peticion(descripcion='Perro herido durante suspension serverless')
        # Intento 1: Railway devuelve 502 mientras n8n despierta (cold start)
        with patch('triaje_ia.services.requests.post', return_value=MagicMock(status_code=502, text='Application failed to respond')):
            r1 = encolar_y_despachar_triaje_peticion(pet, async_dispatch=False)
        self.assertFalse(r1['enviado'])
        self.assertEqual(r1['estado_envio'], TriajePeticion.ENVIO_FALLO_TEMPORAL)

        triaje = TriajePeticion.objects.get(peticion=pet)
        self.assertIsNotNone(triaje.proximo_reintento_en)

        # Intento 2: pasado el backoff de 30s, n8n ya está caliente y responde 200 OK
        triaje.proximo_reintento_en = _ahora_local() - timedelta(seconds=1)
        triaje.save(update_fields=['proximo_reintento_en'])
        with patch('triaje_ia.services.requests.post', return_value=MagicMock(status_code=200, text='{"message":"Workflow was started"}')) as mock_retry:
            r2 = recuperar_trabajos_interrumpidos(limite=20)
        self.assertEqual(r2['enviados'], 1)
        mock_retry.assert_called_once()
        triaje.refresh_from_db()
        self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_ENVIADO)
        self.assertEqual(triaje.estado_analisis, TriajePeticion.ANALISIS_EN_PROCESO)

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_ASYNC_THREAD=False,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=True,
    )
    def test_recuperacion_no_usa_pg_try_advisory_lock_y_soporta_concurrencia_workers(self):
        import inspect
        import os
        import tempfile
        from triaje_ia import services as triaje_services

        fuente = inspect.getsource(triaje_services.recuperar_trabajos_interrumpidos)
        self.assertNotIn('pg_try_advisory_lock', fuente)
        self.assertNotIn('pg_advisory_unlock', fuente)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'triaje_recovery_conc.sqlite3')
            conn_main = _crear_conexion_sqlite(file_uri=db_path)
            conn_prev = getattr(connections._connections, 'default', None)
            connections._connections.default = conn_main
            try:
                with conn_main.schema_editor(atomic=False) as editor:
                    for modelo in _MODELOS_AISLADOS:
                        editor.create_model(modelo)

                rol_pet = Rol.objects.create(id_rol=ROL_PETICIONARIO, nombre_rol='Peticionario')
                sede = Sede.objects.create(
                    nombre='Sede Rec', direccion='Cra 1', ciudad='Popayán', telefono='300', activo=True,
                )
                ciudadano = Usuarios.objects.create(
                    id_rol=rol_pet, id_sede=sede, nombre='C',
                    email='c_rec@triaje.test', password='x', activo=True,
                )
                tipo = TipoPeticion.objects.create(nombre='Maltrato', activo=True)
                est = EstadoPeticiones.objects.create(nombre='En revisión', activo=True, orden=1)
                ubic = Ubicaciones.objects.create(direccion='Calle 1', latitud=2.44, longitud=-76.6)
                pet = Peticiones.objects.create(
                    id_tipo=tipo, id_ubicacion=ubic, id_estado=est, responsable=ciudadano,
                    descripcion='Perro con fractura en extremidad trasera', numero_radicado='PET-2026-99002',
                )
                TriajePeticion.objects.create(
                    peticion=pet,
                    clave_idempotencia=f'triaje-pet-{pet.id_peticion}',
                    estado_envio=TriajePeticion.ENVIO_ENVIANDO,
                    lease_expira_en=_ahora_local() - timedelta(seconds=5),
                    despacho_token='lease-vencido-tok',
                    intentos_envio=1,
                    max_intentos=6,
                )

                envios = []
                lock_envios = threading.Lock()
                barrera = threading.Barrier(2)

                def _post_simulado(*args, **kwargs):
                    with lock_envios:
                        envios.append(kwargs.get('json', {}).get('id_peticion'))
                    return MagicMock(status_code=200, text='ok')

                def _worker():
                    # SQLite no admite SELECT FOR UPDATE. Adquirir el bloqueo de escritura
                    # antes de la lectura evita promover dos transacciones de lectura a la vez.
                    conn_hilo = _crear_conexion_sqlite(file_uri=db_path, transaction_mode='IMMEDIATE')
                    connections._connections.default = conn_hilo
                    try:
                        barrera.wait(timeout=5)
                        return recuperar_trabajos_interrumpidos(limite=20, usar_advisory_lock=True)
                    finally:
                        conn_hilo.close()

                with patch('triaje_ia.services.requests.post', side_effect=_post_simulado):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        workers = [pool.submit(_worker) for _ in range(2)]
                        resultados = [worker.result(timeout=15) for worker in workers]

                self.assertEqual(envios, [pet.pk])
                self.assertEqual(sum(r['recuperados_lease'] for r in resultados), 1)
                self.assertEqual(sum(r.get('enviados', 0) for r in resultados), 1)
            finally:
                conn_main.close()
                connections._connections.default = conn_prev

    def test_sanitizacion_redacta_nombres_sueltos_coordenadas_y_documentos(self):
        from triaje_ia.services import sanitizar_texto_pii_para_ia

        texto = (
            'Juan Carlos Perez golpeo al perro en 2.444814, -76.614739, '
            'CC 1061789123, placa XYZ-987, Calle 12 # 4-55. '
            'Perro con herida abierta y sangrado activo.'
        )
        limpio = sanitizar_texto_pii_para_ia(texto)
        self.assertNotIn('Juan Carlos Perez', limpio)
        self.assertNotIn('2.444814', limpio)
        self.assertNotIn('-76.614739', limpio)
        self.assertNotIn('1061789123', limpio)
        self.assertNotIn('XYZ-987', limpio)
        self.assertNotIn('Calle 12 # 4-55', limpio)
        self.assertIn('[PERSONA]', limpio)
        self.assertIn('[COORDENADAS]', limpio)
        self.assertIn('Perro con herida abierta y sangrado activo', limpio)

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_ASYNC_THREAD=False,
    )
    def test_contenido_confidencial_omite_gemini_y_ejecuta_triaje_local_con_revision_humana(self):
        pet = self._crear_peticion(
            descripcion='Caso con menor de edad testigo de agresion y perro con sangrado activo tras atropellamiento.'
        )
        prioridad_original = pet.prioridad

        with patch('triaje_ia.services.requests.post') as mock_post:
            with patch('triaje_ia.services.ejecutar_deteccion_duplicados_triaje', return_value={'posibles_duplicados': []}):
                res = encolar_y_despachar_triaje_peticion(pet, async_dispatch=False)

        mock_post.assert_not_called()
        self.assertFalse(res['enviado'])
        self.assertTrue(res['omitido_privacidad'])
        self.assertTrue(res['fallback_local'])

        triaje = TriajePeticion.objects.get(peticion=pet)
        self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_OMITIDO)
        self.assertEqual(triaje.estado_analisis, TriajePeticion.ANALISIS_PARCIAL)
        self.assertEqual(triaje.prioridad_sugerida, TriajePeticion.PRIORIDAD_URGENTE)
        self.assertEqual(triaje.modelo_texto_usado, 'reglas_locales_privacidad')
        self.assertIn('revisión humana', triaje.justificacion.lower())

        pet.refresh_from_db()
        self.assertEqual(pet.prioridad, prioridad_original)
        self.assertIsNone(pet.asignado_a)

    @override_settings(
        TRIAJE_N8N_ACTIVO=False,
        TRIAJE_SERVICE_TOKEN='token-interno-valido-123',
    )
    def test_comando_preparar_prueba_webhook_protege_peticiones_reales_y_genera_token_valido(self):
        import io
        import json
        from django.core.management import call_command
        from django.core.management.base import CommandError

        for descripcion in ('Perro herido real', 'Caso real con [PRUEBA-TRIAJE]', '[PRUEBA-TRIAJE] Un caso real'):
            pet_real = self._crear_peticion(descripcion=descripcion, con_evidencia=False)
            with self.assertRaises(CommandError):
                call_command('preparar_prueba_webhook_triaje', id_peticion=pet_real.pk, escenario='media')
        for evidencia, asignado_a in ((True, None), (False, self.vet_1)):
            pet_operativa = self._crear_peticion(
                descripcion=ESCENARIOS_DEMO['media'], con_evidencia=evidencia, asignado_a=asignado_a,
            )
            with self.assertRaises(CommandError):
                call_command('preparar_prueba_webhook_triaje', id_peticion=pet_operativa.pk, escenario='media')

        pet_ficticia = self._crear_peticion(descripcion=ESCENARIOS_DEMO['media'], con_evidencia=False)
        buf = io.StringIO()
        call_command('preparar_prueba_webhook_triaje', id_peticion=pet_ficticia.pk, escenario='media', stdout=buf)
        salida = json.loads(buf.getvalue())

        token = salida['despacho_token']
        self.assertTrue(token)
        self.assertEqual(salida['payload_webhook']['despacho_token'], token)
        self.assertEqual(set(salida['payload_webhook']), {'id_peticion', 'despacho_token', 'clave_idempotencia'})

        url_cb = f'/api/triaje-ia/interno/peticiones/{pet_ficticia.id_peticion}/resultado/'
        client = APIClient()
        resp_bad = client.post(
            url_cb,
            {
                'clave_idempotencia': salida['clave_idempotencia'],
                'despacho_token': 'token-invalido',
                'estado_analisis': 'COMPLETADO',
                'prioridad_sugerida': 'ALTA',
                'justificacion': 'Intento invalido',
            },
            format='json',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123',
        )
        self.assertEqual(resp_bad.status_code, status.HTTP_409_CONFLICT)

        resp_ok = client.post(
            url_cb,
            {
                'clave_idempotencia': salida['clave_idempotencia'],
                'despacho_token': token,
                'estado_analisis': 'COMPLETADO',
                'prioridad_sugerida': 'ALTA',
                'justificacion': 'Prueba integral controlada OK',
                'resultado_raw': {'gemini_demo': True},
            },
            format='json',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123',
        )
        self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_ok.data['prioridad_sugerida'], 'ALTA')

    @override_settings(
        TRIAJE_SERVICE_TOKEN='token-pruebas',
        TRIAJE_GEMINI_ENVIAR_FOTOS=True,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=False,
        TRIAJE_GEMINI_IDS_PRUEBA_PERMITIDOS=(),
    )
    def test_gemini_demo_solo_expone_material_sintetico_y_revalida_en_django(self):
        import io
        import json
        from django.core.management import call_command
        from PIL import Image

        for escenario in ESCENARIOS_DEMO:
            with self.subTest(escenario=escenario):
                pet = self._crear_peticion(descripcion=ESCENARIOS_DEMO[escenario], con_evidencia=False)
                salida = io.StringIO()
                call_command('preparar_prueba_webhook_triaje', id_peticion=pet.pk, escenario=escenario, stdout=salida)
                preparado = json.loads(salida.getvalue())
                headers = {
                    'HTTP_X_VETERICANO_TRIAJE_TOKEN': 'token-pruebas',
                    'HTTP_X_TRIAJE_DISPATCH_TOKEN': preparado['despacho_token'],
                }
                base = f'/api/triaje-ia/interno/peticiones/{pet.pk}/'
                contexto, foto = f'{base}?gemini=demo', f'{base}foto-segura/?gemini=demo'
                self.assertEqual(self.client.get(contexto).status_code, 401)
                self.assertEqual(self.client.get(foto, HTTP_X_VETERICANO_TRIAJE_TOKEN='token-pruebas').status_code, 403)
                bad_headers = {**headers, 'HTTP_X_TRIAJE_DISPATCH_TOKEN': 'obsoleto'}
                self.assertEqual(self.client.get(contexto, **bad_headers).status_code, 403)
                self.assertEqual(self.client.get(foto, **bad_headers).status_code, 403)
                ctx = self.client.get(contexto, **headers)
                self.assertEqual(ctx.status_code, 200)
                self.assertTrue(ctx.data['gemini_autorizado'])
                self.assertEqual(set(ctx.data), {'gemini_autorizado', 'descripcion', 'foto_autorizada'})
                self.assertTrue(ctx.data['descripcion'].startswith('[PRUEBA-TRIAJE]'))
                if escenario == 'urgente':
                    with patch('triaje_ia.services._descargar_bytes_url_validada') as download:
                        resp = self.client.get(foto, **headers)
                    download.assert_not_called()
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(resp['Content-Type'], 'image/jpeg')
                    self.assertEqual(resp['Cache-Control'], 'no-store')
                    with Image.open(io.BytesIO(resp.content)) as imagen:
                        self.assertEqual(imagen.size, (640, 400))
                        self.assertFalse(imagen.getexif())
                    with override_settings(TRIAJE_GEMINI_ENVIAR_FOTOS=False):
                        self.assertFalse(self.client.get(contexto, **headers).data['foto_autorizada'])
                        self.assertEqual(self.client.get(foto, **headers).status_code, 403)
                    callback = {
                        'clave_idempotencia': preparado['clave_idempotencia'],
                        'despacho_token': preparado['despacho_token'],
                        'estado_analisis': 'COMPLETADO',
                        'prioridad_sugerida': 'URGENTE',
                        'justificacion': 'Atropellamiento simulado.',
                        'signos_fotografia': ['Perro inmóvil'],
                        'modelo_vision_usado': 'models/gemini-3.5-flash-lite',
                        'resultado_raw': {'gemini_demo': True},
                    }
                    EvidenciaPeticiones.objects.create(
                        id_peticion=pet, ruta_archivo='https://res.cloudinary.com/demo/image/upload/reemplazo.jpg',
                    )
                else:
                    self.assertEqual(self.client.get(foto, **headers).status_code, 403)
                    Peticiones.objects.filter(pk=pet.pk).update(descripcion='[PRUEBA-TRIAJE] Texto privado editado')
                self.assertEqual(self.client.get(contexto, **headers).data, {'gemini_autorizado': False})
                self.assertEqual(self.client.get(foto, **headers).status_code, 403)
                if escenario == 'urgente':
                    EvidenciaPeticiones.objects.filter(id_peticion=pet).delete()
                    resultado = APIClient().post(f'{base}resultado/', callback, format='json', **headers)
                    self.assertEqual(resultado.status_code, 200)
                    self.assertTrue(resultado.data['imagen_sintetica_demo'])
                    self.assertFalse(resultado.data['tiene_fotografias'])
                    self.assertEqual(resultado.data['signos_fotografia'], ['Perro inmóvil'])

    @override_settings(TRIAJE_SERVICE_TOKEN='token-pruebas', TRIAJE_GEMINI_IDS_PRUEBA_PERMITIDOS=())
    def test_callback_de_gemini_fallido_o_revocado_persiste_reglas_locales(self):
        import io
        import json
        from django.core.management import call_command

        for revocado in (False, True):
            with self.subTest(revocado=revocado):
                pet = self._crear_peticion(descripcion=ESCENARIOS_DEMO['urgente'], con_evidencia=False)
                salida = io.StringIO()
                call_command('preparar_prueba_webhook_triaje', id_peticion=pet.pk, escenario='urgente', stdout=salida)
                payload = json.loads(salida.getvalue())['payload_webhook']
                if revocado:
                    Peticiones.objects.filter(pk=pet.pk).update(foto='https://ejemplo.com/imagen.jpg')
                resp = APIClient().post(
                    f'/api/triaje-ia/interno/peticiones/{pet.pk}/resultado/',
                    {
                        'clave_idempotencia': payload['clave_idempotencia'],
                        'despacho_token': payload['despacho_token'],
                        'estado_analisis': 'COMPLETADO' if revocado else 'PARCIAL',
                        'prioridad_sugerida': 'BAJA',
                        'justificacion': 'Salida del modelo descartada',
                        'resultado_raw': {'gemini_demo': True, 'ia': {'signos_detectados': ['Descartar']}},
                    },
                    format='json', HTTP_X_VETERICANO_TRIAJE_TOKEN='token-pruebas',
                )
                self.assertEqual(resp.status_code, 200)
                triaje = TriajePeticion.objects.get(peticion=pet)
                self.assertEqual(triaje.prioridad_sugerida, 'URGENTE', triaje.resultado_raw)
                self.assertEqual(triaje.modelo_texto_usado, 'reglas_locales_privacidad')
                self.assertEqual(triaje.estado_analisis, 'PARCIAL')
                self.assertEqual(triaje.signos_detectados, [])
                self.assertEqual(triaje.resultado_raw['motivo'], 'gemini_no_autorizado' if revocado else 'gemini_no_disponible')
                pet.refresh_from_db()
                self.assertIsNone(pet.prioridad)
                self.assertIsNone(pet.asignado_a)

    @override_settings(
        TRIAJE_SERVICE_TOKEN='token-pruebas', TRIAJE_GEMINI_ENVIAR_FOTOS=True,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=False, TRIAJE_GEMINI_IDS_PRUEBA_PERMITIDOS=(),
    )
    def test_demo_urgente_habilita_adjunto_explicito_para_dinov2_y_gemini_recibe_su_propia_imagen(self):
        import io
        import json
        from django.core.management import call_command
        from django.core.management.base import CommandError

        pet = self._crear_peticion(descripcion=ESCENARIOS_DEMO['urgente'])
        with self.assertRaises(CommandError):
            call_command(
                'preparar_prueba_webhook_triaje', id_peticion=pet.pk, escenario='urgente',
                foto_duplicados_url='https://res.cloudinary.com/demo/image/upload/otra.jpg',
            )

        stdout = io.StringIO()
        call_command(
            'preparar_prueba_webhook_triaje', id_peticion=pet.pk, escenario='urgente',
            foto_duplicados_url=pet.foto, stdout=stdout,
        )
        payload = json.loads(stdout.getvalue())['payload_webhook']
        self.assertNotIn(pet.foto, str(payload))
        headers = {
            'HTTP_X_VETERICANO_TRIAJE_TOKEN': 'token-pruebas',
            'HTTP_X_TRIAJE_DISPATCH_TOKEN': payload['despacho_token'],
        }
        base = f'/api/triaje-ia/interno/peticiones/{pet.pk}/'
        self.assertTrue(self.client.get(f'{base}?gemini=demo', **headers).data['foto_autorizada'])
        with patch('triaje_ia.services._descargar_bytes_url_validada') as descargar:
            foto = self.client.get(f'{base}foto-segura/?gemini=demo', **headers)
        self.assertEqual(foto.status_code, 200)
        descargar.assert_not_called()

        with patch(
            'triaje_ia.services.detectar_duplicados_desde_solicitud', return_value={'posibles_duplicados': []},
        ) as detector:
            respuesta = APIClient().post(
                f'{base}duplicados/', {'usar_dinov2': True, 'registrar_alertas': False},
                format='json', **headers,
            )
        self.assertEqual(respuesta.status_code, 200)
        detector.assert_called_once_with(
            id_peticion=pet.pk, usar_dinov2=True, incluir_imagenes=True, incluir_evidencias=True,
        )

        Peticiones.objects.filter(pk=pet.pk).update(foto='https://res.cloudinary.com/demo/image/upload/otra.jpg')
        self.assertEqual(self.client.get(f'{base}?gemini=demo', **headers).data, {'gemini_autorizado': False})
        self.assertEqual(self.client.get(f'{base}foto-segura/?gemini=demo', **headers).status_code, 403)

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_ASYNC_THREAD=False,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=False,
    )
    def test_denuncia_real_por_defecto_omite_gemini_y_solo_envia_pruebas_ficticias(self):
        pet_real = self._crear_peticion(descripcion='Perro con fractura en pata trasera')
        prioridad_original = pet_real.prioridad

        with patch('triaje_ia.services.requests.post') as mock_post:
            with patch('triaje_ia.services.ejecutar_deteccion_duplicados_triaje', return_value={'posibles_duplicados': []}):
                res_real = encolar_y_despachar_triaje_peticion(pet_real, async_dispatch=False)

        mock_post.assert_not_called()
        self.assertFalse(res_real['enviado'])
        self.assertTrue(res_real['omitido_privacidad'])
        self.assertTrue(res_real['fallback_local'])

        triaje_real = TriajePeticion.objects.get(peticion=pet_real)
        self.assertEqual(triaje_real.estado_envio, TriajePeticion.ENVIO_OMITIDO)
        self.assertEqual(triaje_real.estado_analisis, TriajePeticion.ANALISIS_PARCIAL)
        self.assertEqual(triaje_real.prioridad_sugerida, TriajePeticion.PRIORIDAD_ALTA)
        self.assertEqual(triaje_real.modelo_texto_usado, 'reglas_locales_privacidad')
        pet_real.refresh_from_db()
        self.assertEqual(pet_real.prioridad, prioridad_original)

        pet_prueba = self._crear_peticion(descripcion=ESCENARIOS_DEMO['media'], con_evidencia=False)
        with override_settings(TRIAJE_GEMINI_IDS_PRUEBA_PERMITIDOS=(pet_prueba.id_peticion,)):
            with patch('triaje_ia.services.requests.post', return_value=MagicMock(status_code=200, text='ok')) as mock_post_prueba:
                res_prueba = encolar_y_despachar_triaje_peticion(pet_prueba, async_dispatch=False)

        mock_post_prueba.assert_not_called()
        self.assertFalse(res_prueba['enviado'])
        self.assertTrue(res_prueba['fallback_local'])

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_ASYNC_THREAD=False,
        TRIAJE_GEMINI_PERMITIR_DENUNCIAS_REALES=False,
        TRIAJE_GEMINI_IDS_PRUEBA_PERMITIDOS=(),
    )
    def test_denuncia_ciudadana_con_prefijo_prueba_triaje_no_se_envia_a_gemini(self):
        pet_spoof = self._crear_peticion(
            descripcion='[PRUEBA-TRIAJE] Perro con fractura en pata trasera reportado por ciudadano'
        )
        with patch('triaje_ia.services.requests.post') as mock_post:
            with patch('triaje_ia.services.ejecutar_deteccion_duplicados_triaje', return_value={'posibles_duplicados': []}):
                res = encolar_y_despachar_triaje_peticion(pet_spoof, async_dispatch=False)

        mock_post.assert_not_called()
        self.assertFalse(res['enviado'])
        self.assertTrue(res['omitido_privacidad'])
        self.assertTrue(res['fallback_local'])
        triaje = TriajePeticion.objects.get(peticion=pet_spoof)
        self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_OMITIDO)
        self.assertEqual(triaje.estado_analisis, TriajePeticion.ANALISIS_PARCIAL)
        self.assertEqual(triaje.modelo_texto_usado, 'reglas_locales_privacidad')
