"""
Pruebas unitarias e integración aislada para el módulo `triaje_ia`.

Valida:
- Bandera `TRIAJE_N8N_ACTIVO=False` por defecto (no dispara envío ni altera `IniciarPeticionView`).
- Envío no bloqueante, persistente, idempotente y con reintentos + backoff ante fallos de n8n.
- Redacción de secretos en logs/errores persistidos y payload sin credenciales.
- Autenticación de servicio con privilegios mínimos (`TriajeServiceTokenAuthentication`)
  y rechazo ante token ausente, inválido o no configurado (fail-closed).
- Compatibilidad con niveles de prioridad existentes (`URGENTE`, `ALTA`, `MEDIA`, `BAJA`)
  sin sobrescribir `Peticiones.prioridad` ni asignar `Peticiones.asignado_a`.
- Reutilización de detección de duplicados (DINOv2) y cálculo de carga laboral veterinaria.
"""

from __future__ import annotations

from datetime import timedelta
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
from triaje_ia.models import TriajePeticion
from triaje_ia.services import (
    _ahora_local,
    calcular_carga_veterinarios,
    ejecutar_deteccion_duplicados_triaje,
    encolar_y_despachar_triaje_peticion,
    normalizar_prioridad_sugerida,
    procesar_reintentos_pendientes,
    registrar_resultado_triaje,
)
from users.constants import (
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_PETICIONARIO,
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
    HistorialPeticiones,
    PeticionFotoAutorizada,
    PeticionImagenEmbedding,
    PeticionPosibleDuplicado,
    TriajePeticion,
)


def _crear_conexion_sqlite() -> SqliteDatabaseWrapper:
    settings_dict = connections.configure_settings({
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    })['default']
    conn = SqliteDatabaseWrapper(settings_dict, alias='default')
    conn.ensure_connection()
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

        with self._sqlite_conn.schema_editor(atomic=False) as editor:
            for modelo in _MODELOS_AISLADOS:
                editor.create_model(modelo)

        self.rol_admin = Rol.objects.create(id_rol=ROL_ADMINISTRADOR, nombre_rol='Administrador')
        self.rol_juridico = Rol.objects.create(id_rol=ROL_JURIDICO, nombre_rol='Jurídico')
        self.rol_vet = Rol.objects.create(id_rol=ROL_VETERINARIO, nombre_rol='Veterinario')
        self.rol_pet = Rol.objects.create(id_rol=ROL_PETICIONARIO, nombre_rol='Peticionario')

        self.sede = Sede.objects.create(
            nombre='Sede Principal',
            direccion='Cra 1 # 2-3',
            ciudad='Popayán',
            telefono='3001234567',
        )
        self.admin = Usuarios.objects.create(
            id_rol=self.rol_admin, id_sede=self.sede,
            nombre='Admin', apellido='Uno', email='admin@triaje.test', password='x', activo=True,
        )
        self.juridico = Usuarios.objects.create(
            id_rol=self.rol_juridico, id_sede=self.sede,
            nombre='Juridico', apellido='Uno', email='juridico@triaje.test', password='x', activo=True,
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
        self.ciudadano = Usuarios.objects.create(
            id_rol=self.rol_pet, id_sede=self.sede,
            nombre='Ciudadano', apellido='Demo', email='ciudadano@triaje.test', password='x', activo=True,
        )

        self.tipo = TipoPeticion.objects.create(nombre='Maltrato Animal', activo=True)
        self.estado_rev = EstadoPeticiones.objects.create(nombre='En revisión', activo=True, orden=1)
        self.estado_asig = EstadoPeticiones.objects.create(nombre='Asignada', activo=True, orden=2)
        self.estado_fin = EstadoPeticiones.objects.create(nombre='Finalizada', activo=True, orden=6)
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

    def _crear_peticion(self, *, descripcion='Perro herido con sangrado', prioridad=None, asignado_a=None, estado=None):
        p = Peticiones.objects.create(
            id_tipo=self.tipo,
            id_ubicacion=self.ubic,
            id_estado=estado or self.estado_rev,
            responsable=self.ciudadano,
            asignado_a=asignado_a,
            descripcion=descripcion,
            prioridad=prioridad,
            foto='https://res.cloudinary.com/demo/image/upload/perro.jpg',
        )
        p.numero_radicado = f'PET-2026-{str(p.id_peticion).zfill(5)}'
        p.save(update_fields=['numero_radicado'])
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

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_WEBHOOK_SECRET='secreto-webhook-test',
        TRIAJE_SERVICE_TOKEN='token-servicio-super-secreto',
        TRIAJE_N8N_ASYNC_THREAD=False,
    )
    def test_iniciar_peticion_despacha_idempotente_sin_exponer_token_servicio(self):
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
        mock_post.assert_called_once()

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs['headers']['X-Vetericano-Triaje-Secret'], 'secreto-webhook-test')
        self.assertEqual(kwargs['headers']['X-Idempotency-Key'], triaje.clave_idempotencia)
        self.assertNotIn('token-servicio-super-secreto', str(kwargs['json']))
        self.assertEqual(kwargs['json']['fotos'], ['https://res.cloudinary.com/demo/image/upload/herido.jpg'])

        # Petición repetida / segundo despacho debe ser idempotente
        with patch('triaje_ia.services.requests.post') as mock_post_2:
            res_rep = encolar_y_despachar_triaje_peticion(id_pet, async_dispatch=False)
        self.assertTrue(res_rep['idempotente'])
        self.assertEqual(TriajePeticion.objects.filter(peticion_id=id_pet).count(), 1)
        mock_post_2.assert_not_called()

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_WEBHOOK_SECRET='secreto-super-confidencial',
        TRIAJE_N8N_MAX_REINTENTOS=2,
        TRIAJE_N8N_BACKOFF_BASE_SEGUNDOS=10.0,
        TRIAJE_N8N_ASYNC_THREAD=False,
    )
    def test_fallo_n8n_no_bloquea_registro_y_reintenta_con_backoff(self):
        pet = self._crear_peticion()
        with patch(
            'triaje_ia.services.requests.post',
            side_effect=requests.Timeout('Timeout conectando con Authorization: Bearer secreto-super-confidencial'),
        ):
            res_1 = encolar_y_despachar_triaje_peticion(pet, async_dispatch=False)

        self.assertFalse(res_1['enviado'])
        triaje = TriajePeticion.objects.get(peticion=pet)
        self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_FALLO_TEMPORAL)
        self.assertEqual(triaje.intentos_envio, 1)
        self.assertIsNotNone(triaje.proximo_reintento_en)
        self.assertNotIn('secreto-super-confidencial', triaje.ultimo_error_envio)

        # Antes de vencer backoff no se reintenta
        with patch('triaje_ia.services.requests.post') as mock_skip:
            res_skip = procesar_reintentos_pendientes()
        self.assertEqual(res_skip['procesados'], 0)
        mock_skip.assert_not_called()

        # Al vencer backoff y recuperarse n8n, el reintento logra ENVIO_ENVIADO
        triaje.proximo_reintento_en = _ahora_local() - timedelta(seconds=5)
        triaje.save(update_fields=['proximo_reintento_en'])
        with patch('triaje_ia.services.requests.post', return_value=MagicMock(status_code=202, text='ok')):
            res_ok = procesar_reintentos_pendientes()
        self.assertEqual(res_ok['enviados'], 1)
        triaje.refresh_from_db()
        self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_ENVIADO)
        self.assertEqual(triaje.intentos_envio, 2)

    @override_settings(
        TRIAJE_N8N_ACTIVO=True,
        TRIAJE_N8N_WEBHOOK_URL='http://n8n.internal:5678/webhook/vetericano-triaje-peticion',
        TRIAJE_N8N_MAX_REINTENTOS=2,
        TRIAJE_N8N_ASYNC_THREAD=False,
    )
    def test_agota_reintentos_marca_fallo_definitivo(self):
        pet = self._crear_peticion()
        with patch('triaje_ia.services.requests.post', return_value=MagicMock(status_code=503, text='Service Unavailable')):
            encolar_y_despachar_triaje_peticion(pet, async_dispatch=False)
            triaje = TriajePeticion.objects.get(peticion=pet)
            triaje.proximo_reintento_en = _ahora_local() - timedelta(seconds=1)
            triaje.save(update_fields=['proximo_reintento_en'])
            procesar_reintentos_pendientes()

        triaje.refresh_from_db()
        self.assertEqual(triaje.estado_envio, TriajePeticion.ENVIO_FALLO_DEFINITIVO)
        self.assertEqual(triaje.intentos_envio, 2)
        self.assertEqual(triaje.ultimo_codigo_http, 503)


class TriajeEndpointsAutenticacionYReglasTests(_BaseTriajeSqliteTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.peticion = self._crear_peticion(prioridad='Media')

    @override_settings(TRIAJE_SERVICE_TOKEN='token-interno-valido-123')
    def test_endpoints_internos_rechazan_sin_token_o_token_invalido(self):
        url = f'/api/triaje-ia/interno/peticiones/{self.peticion.id_peticion}/resultado/'
        resp_sin = self.client.post(url, {'prioridad_sugerida': 'ALTA'}, format='json')
        self.assertEqual(resp_sin.status_code, status.HTTP_401_UNAUTHORIZED)

        resp_mal = self.client.post(
            url,
            {'prioridad_sugerida': 'ALTA'},
            format='json',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-incorrecto',
        )
        self.assertEqual(resp_mal.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(TRIAJE_SERVICE_TOKEN='')
    def test_endpoints_internos_fail_closed_si_token_no_configurado(self):
        url = '/api/triaje-ia/interno/veterinarios/carga/'
        resp = self.client.get(url, HTTP_X_VETERICANO_TRIAJE_TOKEN='cualquiera')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(TRIAJE_SERVICE_TOKEN='token-interno-valido-123')
    def test_token_servicio_tiene_permisos_minimos_y_no_accede_a_otras_apps(self):
        # Puede consultar carga de veterinarios internos
        resp_ok = self.client.get(
            '/api/triaje-ia/interno/veterinarios/carga/',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123',
        )
        self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)

        # NO puede acceder a endpoints fuera de triaje_ia
        resp_pet = self.client.get(
            '/api/peticiones/listar/',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123',
        )
        self.assertEqual(resp_pet.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(TRIAJE_SERVICE_TOKEN='token-interno-valido-123')
    def test_guardar_resultado_idempotente_y_sin_sobrescribir_peticion(self):
        # Cargar trabajo previo para vet_1 (1 pendiente) y vet_2 (0 activas)
        self._crear_peticion(asignado_a=self.vet_1, estado=self.estado_asig, prioridad='ALTA')

        resp_carga = self.client.get(
            '/api/triaje-ia/interno/veterinarios/carga/',
            HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123',
        )
        self.assertEqual(resp_carga.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_carga.data['veterinario_recomendado']['id_veterinario'], self.vet_2.id_usuario)

        # Simular guardado desde n8n con prioridad "Crítica" -> normalizada a "URGENTE"
        url_res = f'/api/triaje-ia/interno/peticiones/{self.peticion.id_peticion}/resultado/'
        body = {
            'clave_idempotencia': f'triaje-pet-{self.peticion.id_peticion}',
            'estado_analisis': 'COMPLETADO',
            'prioridad_sugerida': 'Crítica',
            'nivel_confianza': 0.92,
            'justificacion': 'Sangrado severo visible en extremidad posterior.',
            'resumen_texto': 'Canino herido en vía pública.',
            'resumen_vision': 'Lesión hemorrágica aparente.',
            'signos_detectados': ['sangrado', 'herida'],
            'posibles_duplicados': [{'id_peticion': 999, 'score_combinado': 0.81}],
            'veterinario_recomendado_id': self.vet_2.id_usuario,
            'justificacion_recomendacion': resp_carga.data['justificacion_recomendacion'],
            'carga_veterinarios_snapshot': resp_carga.data['veterinarios'],
            'modelo_texto_usado': 'models/gemini-2.5-flash',
            'modelo_vision_usado': 'models/gemini-2.5-flash',
            'n8n_execution_id': 'exec-sim-001',
        }
        resp_1 = self.client.post(url_res, body, format='json', HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123')
        self.assertEqual(resp_1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_1.data['prioridad_sugerida'], 'URGENTE')
        self.assertTrue(es_prioridad_urgente(resp_1.data['prioridad_sugerida']))
        self.assertEqual(resp_1.data['veterinario_recomendado_id'], self.vet_2.id_usuario)

        # Reenvío repetido (idempotente) no duplica registro
        resp_2 = self.client.post(url_res, body, format='json', HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123')
        self.assertEqual(resp_2.status_code, status.HTTP_200_OK)
        self.assertEqual(TriajePeticion.objects.filter(peticion=self.peticion).count(), 1)

        # Verificar que Peticiones.prioridad y Peticiones.asignado_a NO fueron sobrescritos
        self.peticion.refresh_from_db()
        self.assertEqual(self.peticion.prioridad, 'Media')
        self.assertIsNone(self.peticion.asignado_a_id)

    @override_settings(TRIAJE_SERVICE_TOKEN='token-interno-valido-123')
    def test_fallo_ia_permite_estado_parcial_e_ignora_veterinario_inactivo(self):
        url_res = f'/api/triaje-ia/interno/peticiones/{self.peticion.id_peticion}/resultado/'
        body = {
            'estado_analisis': 'PARCIAL',
            'prioridad_sugerida': '',
            'justificacion': 'Gemini no disponible (HTTP 429); se conservan duplicados y carga.',
            'veterinario_recomendado_id': self.vet_inactivo.id_usuario,
        }
        resp = self.client.post(url_res, body, format='json', HTTP_X_VETERICANO_TRIAJE_TOKEN='token-interno-valido-123')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['estado_analisis'], 'PARCIAL')
        self.assertIsNone(resp.data['prioridad_sugerida'])
        self.assertIsNone(resp.data['veterinario_recomendado_id'])

    def test_permisos_consulta_juridico_vs_veterinario_y_ciudadano(self):
        registrar_resultado_triaje(
            self.peticion.id_peticion,
            {'prioridad_sugerida': 'ALTA', 'justificacion': 'Caso prioritario.'},
        )
        url = f'/api/triaje-ia/peticiones/{self.peticion.id_peticion}/'

        self.client.force_authenticate(user=self.ciudadano)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.vet_1)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.juridico)
        resp_jur = self.client.get(url)
        self.assertEqual(resp_jur.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_jur.data['prioridad_sugerida'], 'ALTA')

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

        carga = calcular_carga_veterinarios()
        self.assertEqual(carga['total_veterinarios_activos'], 2)
