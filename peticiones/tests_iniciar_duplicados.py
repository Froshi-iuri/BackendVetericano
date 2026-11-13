"""
Pruebas unitarias y de integración aislada de Fase 4D:
Integración de detección automática de posibles duplicados en `POST /api/peticiones/iniciar/`.

Valida:
- Conservación estricta de HTTP 201 y el contrato JSON original de `/api/peticiones/iniciar/`.
- Ejecución posterior al guardado/commit mediante `transaction.on_commit`.
- Cero descargas de imágenes de Cloudinary durante el registro (`incluir_imagenes=False`,
  `incluir_evidencias=False`).
- Resiliencia ante fallos del detector (timeouts/excepciones nunca bloquean el registro).
- Compatibilidad con el payload/fallback GPS `(0, 0)` y reintentos offline de Android.
- Idempotencia y respeto a decisiones previas `CONFIRMADO` / `DESCARTADO` sin crear alertas
  ni notificaciones repetidas ni modificar peticiones existentes.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from django.conf import settings as django_settings
from django.db import OperationalError, connections
from django.db.backends.sqlite3.base import DatabaseWrapper as SqliteDatabaseWrapper
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from peticiones.api.views import DetectarDuplicadosPeticionView, IniciarPeticionView
from peticiones.models import (
    EstadoPeticiones,
    HistorialPeticiones,
    NotificacionJuridico,
    Peticiones,
    PeticionPosibleDuplicado,
    TipoPeticion,
    Ubicaciones,
)
from peticiones.services.alertas_duplicados_service import (
    HISTORIAL_TIPO_DUPLICADO_DETECTADO,
    TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
    _obtener_config_deteccion_automatica,
    confirmar_duplicado,
    descartar_duplicado,
    detectar_y_registrar_duplicados_al_iniciar,
)
from peticiones.services.duplicados_service import (
    buscar_peticiones_similares_por_descripcion,
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


def _dummy_user(uid: int, rol_id: int) -> MagicMock:
    user = MagicMock()
    user.id_usuario = uid
    user.pk = uid
    user.id_rol_id = rol_id
    user.id_rol = MagicMock(id_rol=rol_id)
    user.activo = True
    user.is_authenticated = True
    user.is_anonymous = False
    return user


def _crear_peticion_en_memoria(
    *,
    id_peticion: int = 101,
    radicado: str = 'PET-2026-00101',
    descripcion: str = 'Perro herido frente al parque principal',
    latitud=None,
    longitud=None,
    persistida: bool = True,
) -> Peticiones:
    ubicacion = None
    if latitud is not None or longitud is not None:
        ubicacion = Ubicaciones(
            id_ubicacion=501,
            direccion='Calle 5 #4-10',
            latitud=latitud,
            longitud=longitud,
        )
    pet = Peticiones(
        id_peticion=id_peticion,
        numero_radicado=radicado,
        id_tipo=TipoPeticion(id_tipo=1, nombre='Maltrato'),
        id_ubicacion=ubicacion,
        id_estado=EstadoPeticiones(id_estado=1, nombre='En revisión'),
        descripcion=descripcion,
        prioridad='ALTA',
        foto='https://res.cloudinary.com/demo/image/upload/v1/pet_test.jpg',
    )
    pet._state.adding = not persistida
    return pet


class IniciarPeticionAutoDeteccionAPITests(SimpleTestCase):
    """Pruebas unitarias de `IniciarPeticionView` con integración Fase 4D."""

    def setUp(self):
        super().setUp()
        self.factory = APIRequestFactory()
        self.ciudadano = _dummy_user(10, 4)
        self.juridico = _dummy_user(20, 2)
        self.admin = _dummy_user(30, 1)
        self.veterinario = _dummy_user(40, 3)

    @patch('peticiones.api.views.detectar_y_registrar_duplicados_al_iniciar')
    @patch('peticiones.api.views.transaction.on_commit')
    @patch('peticiones.api.views.enviar_correo_peticion_urgente_juridico', return_value={'enviado': False})
    @patch('peticiones.api.views.crear_notificaciones_nueva_peticion')
    @patch('peticiones.api.views.registrar_historial_peticion')
    @patch('peticiones.api.views._obtener_estado_por_nombre')
    def test_iniciar_mantiene_http_201_json_original_y_programa_on_commit(
        self,
        mock_estado,
        _mock_hist,
        _mock_notif,
        _mock_correo,
        mock_on_commit,
        mock_detectar,
    ):
        estado = EstadoPeticiones(id_estado=1, nombre='En revisión')
        mock_estado.return_value = estado

        # Ejecutar inmediatamente el callback pasado a transaction.on_commit
        # tras validar que serializer.save() ya ocurrió antes de registrarlo.
        eventos = []

        def _capture_commit(fn):
            eventos.append('on_commit_registered')
            fn()

        mock_on_commit.side_effect = _capture_commit

        for actor in (self.ciudadano, self.juridico, self.admin):
            eventos.clear()
            mock_detectar.reset_mock()
            mock_on_commit.reset_mock()

            peticion = _crear_peticion_en_memoria(
                id_peticion=200 + actor.id_usuario,
                radicado=f'PET-2026-00{200 + actor.id_usuario}',
            )
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
                    'https://res.cloudinary.com/demo/image/upload/v1/pet_test.jpg'
                ]
                mock_ev_prop.return_value = mock_ev_set
                ser = MagicMock()
                ser.is_valid.return_value = True

                def _save(**_kwargs):
                    eventos.append('saved')
                    return peticion

                ser.save.side_effect = _save
                mock_get_ser.return_value = ser

                req = self.factory.post(
                    '/api/peticiones/iniciar/',
                    {'id_tipo': 1, 'descripcion': 'Perro herido frente al parque principal'},
                    format='json',
                )
                force_authenticate(req, user=actor)
                resp = IniciarPeticionView.as_view()(req)

            self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
            self.assertEqual(set(resp.data.keys()), CLAVES_RESPUESTA_INICIAR)
            self.assertNotIn('posibles_duplicados', resp.data)
            self.assertEqual(resp.data['id_peticion'], peticion.id_peticion)
            self.assertEqual(resp.data['numero_radicado'], peticion.numero_radicado)
            self.assertEqual(eventos, ['saved', 'on_commit_registered'])
            mock_on_commit.assert_called_once()
            mock_detectar.assert_called_once_with(peticion, usuario_accion=actor)

    @patch('peticiones.api.views.detectar_y_registrar_duplicados_al_iniciar')
    @patch('peticiones.api.views.transaction.on_commit')
    def test_veterinario_403_no_programa_ni_ejecuta_deteccion(self, mock_on_commit, mock_detectar):
        req = self.factory.post(
            '/api/peticiones/iniciar/',
            {'id_tipo': 1, 'descripcion': 'Perro herido frente al parque principal'},
            format='json',
        )
        force_authenticate(req, user=self.veterinario)
        resp = IniciarPeticionView.as_view()(req)

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        mock_on_commit.assert_not_called()
        mock_detectar.assert_not_called()

    @patch('peticiones.api.views.detectar_y_registrar_duplicados_al_iniciar')
    @patch('peticiones.api.views.transaction.on_commit')
    @patch('peticiones.api.views.enviar_correo_peticion_urgente_juridico', return_value={'enviado': False})
    @patch('peticiones.api.views.crear_notificaciones_nueva_peticion')
    @patch('peticiones.api.views.registrar_historial_peticion')
    @patch('peticiones.api.views._obtener_estado_por_nombre')
    def test_fallo_no_controlado_en_hook_no_bloquea_registro_201(
        self,
        mock_estado,
        _mock_hist,
        _mock_notif,
        _mock_correo,
        mock_on_commit,
        mock_detectar,
    ):
        mock_estado.return_value = EstadoPeticiones(id_estado=1, nombre='En revisión')
        mock_on_commit.side_effect = lambda fn: fn()
        mock_detectar.side_effect = RuntimeError('Fallo simulado del detector automático')

        peticion = _crear_peticion_en_memoria(id_peticion=305, radicado='PET-2026-00305')
        with patch.object(
            Peticiones,
            'evidenciapeticiones_set',
            new_callable=PropertyMock,
        ) as mock_ev_prop, patch.object(
            IniciarPeticionView,
            'get_serializer',
        ) as mock_get_ser:
            mock_ev_set = MagicMock()
            mock_ev_set.values_list.return_value = []
            mock_ev_prop.return_value = mock_ev_set
            ser = MagicMock()
            ser.is_valid.return_value = True
            ser.save.return_value = peticion
            mock_get_ser.return_value = ser

            req = self.factory.post(
                '/api/peticiones/iniciar/',
                {'id_tipo': 1, 'descripcion': 'Canino lesionado parque central'},
                format='json',
            )
            force_authenticate(req, user=self.ciudadano)
            with self.assertLogs('peticiones.api.views', level='ERROR'):
                resp = IniciarPeticionView.as_view()(req)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['id_peticion'], 305)
        self.assertEqual(set(resp.data.keys()), CLAVES_RESPUESTA_INICIAR)

    @patch('peticiones.api.views.detectar_y_registrar_duplicados_al_iniciar')
    @patch('peticiones.api.views.transaction.on_commit')
    @patch('peticiones.api.views.enviar_correo_peticion_urgente_juridico', return_value={'enviado': False})
    @patch('peticiones.api.views.crear_notificaciones_nueva_peticion')
    @patch('peticiones.api.views.registrar_historial_peticion')
    @patch('peticiones.api.views._obtener_estado_por_nombre')
    def test_omite_hook_si_instancia_no_esta_persistida(
        self,
        mock_estado,
        _mock_hist,
        _mock_notif,
        _mock_correo,
        mock_on_commit,
        mock_detectar,
    ):
        mock_estado.return_value = EstadoPeticiones(id_estado=1, nombre='En revisión')
        pet_sin_guardar = _crear_peticion_en_memoria(id_peticion=306, persistida=False)

        with patch.object(
            Peticiones,
            'evidenciapeticiones_set',
            new_callable=PropertyMock,
        ) as mock_ev_prop, patch.object(
            IniciarPeticionView,
            'get_serializer',
        ) as mock_get_ser:
            mock_ev_set = MagicMock()
            mock_ev_set.values_list.return_value = []
            mock_ev_prop.return_value = mock_ev_set
            ser = MagicMock()
            ser.is_valid.return_value = True
            ser.save.return_value = pet_sin_guardar
            mock_get_ser.return_value = ser

            req = self.factory.post(
                '/api/peticiones/iniciar/',
                {'id_tipo': 1, 'descripcion': 'Perro herido parque central'},
                format='json',
            )
            force_authenticate(req, user=self.ciudadano)
            resp = IniciarPeticionView.as_view()(req)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        mock_on_commit.assert_not_called()
        mock_detectar.assert_not_called()

    @patch('peticiones.services.duplicados_service.calcular_hashes_urls')
    @patch('peticiones.services.duplicados_service.Peticiones.objects')
    def test_buscar_sin_imagenes_ni_evidencias_no_descarga_cloudinary_ni_hace_prefetch(
        self,
        mock_pet_objects,
        mock_calc_hashes,
    ):
        cand = _crear_peticion_en_memoria(
            id_peticion=88,
            radicado='PET-2026-00088',
            descripcion='Perro herido frente al parque principal',
            latitud=2.4419,
            longitud=-76.6063,
        )
        cand.similitud = 0.85

        qs_texto = MagicMock()
        qs_texto.filter.return_value = qs_texto
        qs_texto.exclude.return_value = qs_texto
        qs_texto.annotate.return_value = qs_texto
        qs_texto.order_by.return_value = qs_texto
        qs_texto.__getitem__.return_value = [cand]

        qs_gps = MagicMock()
        qs_gps.filter.return_value = qs_gps
        qs_gps.exclude.return_value = qs_gps
        qs_gps.annotate.return_value = qs_gps
        qs_gps.order_by.return_value = qs_gps
        qs_gps.__getitem__.return_value = [cand]

        select_rel_mock = MagicMock()
        select_rel_mock.side_effect = [qs_texto, qs_gps]
        mock_pet_objects.select_related = select_rel_mock

        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=AssertionError('No debe invocar descargas Cloudinary'),
        ):
            resultado = buscar_peticiones_similares_por_descripcion(
                'Perro herido frente al parque principal',
                latitud=2.44191,
                longitud=-76.60631,
                foto='https://res.cloudinary.com/demo/image/upload/v1/foto_origen.jpg',
                fotos=['https://res.cloudinary.com/demo/image/upload/v1/foto_extra.jpg'],
                excluir_id_peticion=99,
                incluir_imagenes=False,
                incluir_evidencias=False,
            )

        self.assertTrue(resultado['posible_duplicado'])
        self.assertFalse(resultado['parametros']['imagen_activa'])
        self.assertEqual(len(resultado['posibles_duplicados']), 1)
        self.assertFalse(resultado['posibles_duplicados'][0]['coincidencia_imagen'])
        self.assertIsNone(resultado['posibles_duplicados'][0]['foto'])
        self.assertEqual(resultado['posibles_duplicados'][0]['fotos'], [])
        mock_calc_hashes.assert_not_called()
        qs_texto.prefetch_related.assert_not_called()
        qs_gps.prefetch_related.assert_not_called()


class DetectarYRegistrarAlIniciarServiceTests(SimpleTestCase):
    """Pruebas unitarias del orquestador `detectar_y_registrar_duplicados_al_iniciar`."""

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=False)
    @patch('peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion')
    def test_omitida_cuando_feature_flag_esta_desactivada(self, mock_buscar):
        pet = _crear_peticion_en_memoria(id_peticion=10)
        res = detectar_y_registrar_duplicados_al_iniciar(pet)

        self.assertFalse(res.ejecutada)
        self.assertEqual(res.omitida_motivo, 'DESACTIVADA')
        mock_buscar.assert_not_called()

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True)
    @patch('peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion')
    def test_android_gps_cero_y_descripcion_corta_omiten_consultas_sql(self, mock_buscar):
        pet = _crear_peticion_en_memoria(
            id_peticion=11,
            descripcion='Ayuda',
            latitud=0.0,
            longitud=0.0,
        )
        res = detectar_y_registrar_duplicados_al_iniciar(pet)

        self.assertFalse(res.ejecutada)
        self.assertEqual(res.omitida_motivo, 'SIN_TEXTO_NI_GPS_VALIDOS')
        mock_buscar.assert_not_called()

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True)
    @patch('peticiones.services.alertas_duplicados_service.connection')
    @patch('peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion')
    def test_statement_timeout_o_error_busqueda_retorna_error_sin_excepcion(self, mock_buscar, mock_conn):
        mock_conn.vendor = 'postgresql'
        mock_buscar.side_effect = OperationalError('canceling statement due to statement timeout')
        pet = _crear_peticion_en_memoria(id_peticion=12)

        with patch('peticiones.services.alertas_duplicados_service.transaction.atomic'):
            with self.assertLogs('peticiones.services.alertas_duplicados_service', level='WARNING'):
                res = detectar_y_registrar_duplicados_al_iniciar(pet)

        self.assertFalse(res.ejecutada)
        self.assertEqual(res.peticion_id, 12)
        self.assertIn('statement timeout', res.error)
        mock_conn.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            "SELECT set_config('statement_timeout', %s, true)",
            ['1500'],
        )

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True)
    @patch('peticiones.services.alertas_duplicados_service.connection')
    @patch('peticiones.services.alertas_duplicados_service.registrar_alertas_desde_deteccion')
    @patch('peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion')
    def test_error_en_persistencia_de_alerta_tampoco_propaga(self, mock_buscar, mock_registrar, mock_conn):
        mock_conn.vendor = 'sqlite'
        mock_buscar.return_value = {
            'posible_duplicado': True,
            'posibles_duplicados': [{'id_peticion': 9, 'posible_duplicado': True, 'score_combinado': 0.9}],
        }
        mock_registrar.side_effect = RuntimeError('Fallo al persistir alerta')
        pet = _crear_peticion_en_memoria(id_peticion=13)

        with patch('peticiones.services.alertas_duplicados_service.transaction.atomic'):
            with self.assertLogs('peticiones.services.alertas_duplicados_service', level='WARNING'):
                res = detectar_y_registrar_duplicados_al_iniciar(pet)

        self.assertFalse(res.ejecutada)
        self.assertEqual(res.peticion_id, 13)
        self.assertIn('Fallo al persistir alerta', res.error)

    @override_settings(
        DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True,
        DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS=50,
        DUPLICADOS_AUTO_MAX_CANDIDATOS=7,
    )
    def test_configuracion_auto_acotada_clamping(self):
        cfg = _obtener_config_deteccion_automatica()
        self.assertTrue(cfg['activo'])
        self.assertEqual(cfg['timeout_ms'], 100)
        self.assertEqual(cfg['max_candidatos'], 7)

    @override_settings(
        DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True,
        DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS=999999,
        DUPLICADOS_AUTO_MAX_CANDIDATOS=999,
    )
    def test_configuracion_auto_tope_maximo(self):
        cfg = _obtener_config_deteccion_automatica()
        self.assertEqual(cfg['timeout_ms'], 10000)
        self.assertEqual(cfg['max_candidatos'], 50)


class DetectarYRegistrarSqliteIntegracionTests(SimpleTestCase):
    """
    Valida persistencia idempotente, respeto de `CONFIRMADO`/`DESCARTADO`,
    notificaciones y auditoría usando SQLite aislado en memoria.
    """

    @classmethod
    def _add_databases_failures(cls):
        return None

    @classmethod
    def _remove_databases_failures(cls):
        return None

    def setUp(self):
        super().setUp()
        self._conexion_original = getattr(connections._connections, 'default', None)
        settings_dict = connections.configure_settings({
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        })['default']
        self._sqlite_conn = SqliteDatabaseWrapper(settings_dict, alias='default')
        self._sqlite_conn.ensure_connection()
        connections._connections.default = self._sqlite_conn

        modelos = (
            Rol,
            Sede,
            Usuarios,
            TipoPeticion,
            EstadoPeticiones,
            Ubicaciones,
            Peticiones,
            NotificacionJuridico,
            HistorialPeticiones,
            PeticionPosibleDuplicado,
        )
        with self._sqlite_conn.schema_editor(atomic=False) as schema_editor:
            for modelo in modelos:
                schema_editor.create_model(modelo)

        self.rol_juridico = Rol.objects.create(nombre_rol='JURIDICO')
        self.rol_ciudadano = Rol.objects.create(nombre_rol='USUARIO')
        self.sede = Sede.objects.create(
            nombre='Sede SQLite 4D',
            ciudad='Popayán',
            direccion='Calle 1',
            telefono='3000000000',
            codigo='SQLITE-4D',
        )
        self.juridico = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=self.sede,
            nombre='Ana',
            apellido='Juridico',
            email='juridico.sqlite@test.local',
            password='hash',
            activo=True,
        )
        self.ciudadano = Usuarios.objects.create(
            id_rol=self.rol_ciudadano,
            id_sede=self.sede,
            nombre='Juan',
            apellido='Ciudadano',
            email='ciudadano.sqlite@test.local',
            password='hash',
            activo=True,
        )
        self.tipo = TipoPeticion.objects.create(nombre='Urgencia veterinaria')
        self.estado = EstadoPeticiones.objects.create(nombre='En revisión')
        self.ubic = Ubicaciones.objects.create(
            direccion='Parque Principal',
            latitud=2.4419,
            longitud=-76.6063,
        )
        self.p1 = Peticiones.objects.create(
            numero_radicado='PET-2026-00001',
            id_tipo=self.tipo,
            id_ubicacion=self.ubic,
            id_estado=self.estado,
            responsable=self.ciudadano,
            descripcion='Perro herido atropellado frente al parque principal',
            fecha=datetime(2026, 11, 12, 9, 0, 0),
            prioridad='ALTA',
        )
        self.p2 = Peticiones.objects.create(
            numero_radicado='PET-2026-00002',
            id_tipo=self.tipo,
            id_ubicacion=self.ubic,
            id_estado=self.estado,
            responsable=self.ciudadano,
            descripcion='Canino herido atropellado frente al parque principal',
            fecha=datetime(2026, 11, 12, 10, 0, 0),
            prioridad='ALTA',
        )

    def tearDown(self):
        try:
            self._sqlite_conn.close()
        finally:
            if self._conexion_original is not None:
                connections._connections.default = self._conexion_original
            elif hasattr(connections._connections, 'default'):
                delattr(connections._connections, 'default')
        super().tearDown()

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True)
    @patch('peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion')
    def test_registro_idempotente_y_respeto_decisiones_previas(self, mock_buscar):
        mock_buscar.return_value = {
            'posible_duplicado': True,
            'posibles_duplicados': [
                {
                    'id_peticion': self.p1.id_peticion,
                    'posible_duplicado': True,
                    'similitud_texto': 0.89,
                    'coincidencia_texto': True,
                    'distancia_metros': 8.5,
                    'coincidencia_gps': True,
                    'coincidencia_imagen': False,
                    'score_combinado': 0.88,
                    'criterios_coincidencia': ['texto', 'gps'],
                }
            ],
        }

        res1 = detectar_y_registrar_duplicados_al_iniciar(self.p2, usuario_accion=self.ciudadano)
        self.assertTrue(res1.ejecutada)
        self.assertEqual(res1.candidatos_detectados, 1)
        self.assertIsNotNone(res1.batch)
        self.assertEqual(res1.batch.creadas, 1)
        self.assertEqual(res1.batch.notificaciones_creadas, 1)

        mock_buscar.assert_called_once()
        _, kwargs = mock_buscar.call_args
        self.assertFalse(kwargs['incluir_imagenes'])
        self.assertFalse(kwargs['incluir_evidencias'])

        # Segunda ejecución (reintento idempotente) no crea alertas ni notificaciones repetidas
        res2 = detectar_y_registrar_duplicados_al_iniciar(self.p2, usuario_accion=self.ciudadano)
        self.assertTrue(res2.ejecutada)
        self.assertEqual(res2.batch.creadas, 0)
        self.assertEqual(res2.batch.notificaciones_creadas, 0)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), 1)
        self.assertEqual(
            NotificacionJuridico.objects.filter(tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO).count(),
            1,
        )

        # Confirmar alerta y verificar respeto ante nueva ejecución
        alerta = PeticionPosibleDuplicado.objects.first()
        confirmar_duplicado(
            alerta.id_alerta,
            peticion_principal=self.p1.id_peticion,
            usuario_revisor=self.juridico,
            observacion='Confirmado',
        )
        res3 = detectar_y_registrar_duplicados_al_iniciar(self.p2, usuario_accion=self.ciudadano)
        self.assertEqual(res3.batch.respetadas_decision_previa, 1)
        self.assertEqual(res3.batch.creadas, 0)
        self.assertEqual(res3.batch.notificaciones_creadas, 0)
        alerta.refresh_from_db()
        self.assertEqual(alerta.estado_revision, PeticionPosibleDuplicado.ESTADO_CONFIRMADO)

        # Verificar lo mismo para DESCARTADO con otro par (p1, p3)
        p3 = Peticiones.objects.create(
            numero_radicado='PET-2026-00003',
            id_tipo=self.tipo,
            id_ubicacion=self.ubic,
            id_estado=self.estado,
            responsable=self.ciudadano,
            descripcion='Perro herido atropellado parque principal',
            fecha=datetime(2026, 11, 12, 11, 0, 0),
            prioridad='MEDIA',
        )
        res_p3 = detectar_y_registrar_duplicados_al_iniciar(p3, usuario_accion=self.ciudadano)
        self.assertEqual(res_p3.batch.creadas, 1)
        alerta_13 = PeticionPosibleDuplicado.objects.get(peticion_menor=self.p1, peticion_mayor=p3)
        descartar_duplicado(
            alerta_13.id_alerta,
            usuario_revisor=self.juridico,
            observacion='Descartado',
        )
        res_p3_retry = detectar_y_registrar_duplicados_al_iniciar(p3, usuario_accion=self.ciudadano)
        self.assertEqual(res_p3_retry.batch.respetadas_decision_previa, 1)
        alerta_13.refresh_from_db()
        self.assertEqual(alerta_13.estado_revision, PeticionPosibleDuplicado.ESTADO_DESCARTADO)
        self.assertEqual(
            HistorialPeticiones.objects.filter(tipo_cambio=HISTORIAL_TIPO_DUPLICADO_DETECTADO).count(),
            4,
        )


def _cargar_modulo_settings_con_env(env_vars: dict[str, str | None]):
    """
    Evalúa `vetericano/settings.py` en un módulo aislado con variables de entorno controladas,
    sin alterar `django.conf.settings` ni depender de `.env`.
    """
    settings_path = Path(django_settings.BASE_DIR) / 'vetericano' / 'settings.py'
    claves_duplicados = (
        'DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA',
        'DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS',
        'DUPLICADOS_AUTO_MAX_CANDIDATOS',
    )
    env_limpio = {k: v for k, v in os.environ.items() if k not in claves_duplicados}
    env_limpio.setdefault('DATABASE_URL', 'postgres://postgres:dummy@127.0.0.1:5432/dummy_db')
    for clave, valor in env_vars.items():
        if valor is None:
            env_limpio.pop(clave, None)
        else:
            env_limpio[clave] = valor

    spec = importlib.util.spec_from_file_location('vetericano_settings_probe', str(settings_path))
    modulo = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, env_limpio, clear=True), patch('dotenv.load_dotenv'):
        spec.loader.exec_module(modulo)
    return modulo


class ConfiguracionDeteccionAutomaticaSettingsTests(SimpleTestCase):
    """
    Pruebas de lectura y validación de variables de entorno en `vetericano/settings.py`
    para detección automática de duplicados (`True`, `False`, ausentes e inválidos).
    """

    def test_defaults_seguros_en_django_settings_activo(self):
        self.assertIs(django_settings.DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA, False)
        self.assertEqual(django_settings.DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS, 1500)
        self.assertIsNone(django_settings.DUPLICADOS_AUTO_MAX_CANDIDATOS)

        cfg = _obtener_config_deteccion_automatica()
        self.assertIs(cfg['activo'], False)
        self.assertEqual(cfg['timeout_ms'], 1500)
        self.assertIsNone(cfg['max_candidatos'])

    def test_variables_ausentes_usan_valores_seguros_predeterminados(self):
        mod = _cargar_modulo_settings_con_env({})
        self.assertIs(mod.DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA, False)
        self.assertEqual(mod.DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS, 1500)
        self.assertIsNone(mod.DUPLICADOS_AUTO_MAX_CANDIDATOS)

    def test_deteccion_automatica_activa_conversion_true(self):
        for valor in ('True', 'true', 'TRUE', '1', 'yes', 'si', 'sí', 'on', ' true '):
            with self.subTest(valor=valor):
                mod = _cargar_modulo_settings_con_env({
                    'DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA': valor,
                })
                self.assertIs(mod.DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA, True)

    def test_deteccion_automatica_activa_conversion_false_sin_interpretar_cadena_como_true(self):
        for valor in ('False', 'false', 'FALSE', '0', 'no', 'off', ' false ', ''):
            with self.subTest(valor=valor):
                mod = _cargar_modulo_settings_con_env({
                    'DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA': valor,
                })
                self.assertIs(mod.DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA, False)

    def test_deteccion_automatica_activa_valores_invalidos_caen_a_false(self):
        for valor in ('invalido', 'abc', '2', '-1', 'maybe', 'null', '   '):
            with self.subTest(valor=valor):
                mod = _cargar_modulo_settings_con_env({
                    'DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA': valor,
                })
                self.assertIs(mod.DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA, False)

    def test_statement_timeout_valido_acotado_e_invalido(self):
        casos = [
            ('1500', 1500, 1500),
            ('2500', 2500, 2500),
            (' 800 ', 800, 800),
            ('50', 100, 100),
            ('99999', 10000, 10000),
            ('0', 0, None),
            ('', 1500, 1500),
            ('   ', 1500, 1500),
            ('abc', 1500, 1500),
            ('12.5', 1500, 1500),
            ('true', 1500, 1500),
            ('-10', 1500, 1500),
        ]
        for raw, esperado_setting, esperado_servicio in casos:
            with self.subTest(raw=raw):
                mod = _cargar_modulo_settings_con_env({
                    'DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS': raw,
                })
                self.assertEqual(mod.DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS, esperado_setting)
                self.assertIs(type(mod.DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS), int)
                with override_settings(
                    DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS=mod.DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS
                ):
                    cfg = _obtener_config_deteccion_automatica()
                    self.assertEqual(cfg['timeout_ms'], esperado_servicio)

    def test_max_candidatos_opcional_limites_e_invalidos(self):
        casos = [
            ('1', 1),
            ('7', 7),
            (' 25 ', 25),
            ('50', 50),
            ('51', 50),
            ('999', 50),
            ('', None),
            ('   ', None),
            ('0', None),
            ('-5', None),
            ('abc', None),
            ('12.5', None),
            ('true', None),
        ]
        for raw, esperado in casos:
            with self.subTest(raw=raw):
                mod = _cargar_modulo_settings_con_env({
                    'DUPLICADOS_AUTO_MAX_CANDIDATOS': raw,
                })
                self.assertEqual(mod.DUPLICADOS_AUTO_MAX_CANDIDATOS, esperado)
                with override_settings(
                    DUPLICADOS_AUTO_MAX_CANDIDATOS=mod.DUPLICADOS_AUTO_MAX_CANDIDATOS
                ):
                    cfg = _obtener_config_deteccion_automatica()
                    self.assertEqual(cfg['max_candidatos'], esperado)

    def test_cadena_false_desactiva_solo_deteccion_automatica_y_mantiene_iniciar_y_manual(self):
        mod = _cargar_modulo_settings_con_env({
            'DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA': 'False',
        })
        self.assertIs(mod.DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA, False)

        factory = APIRequestFactory()
        ciudadano = _dummy_user(101, 4)
        juridico = _dummy_user(102, 2)
        estado = EstadoPeticiones(id_estado=1, nombre='En revisión')
        peticion = _crear_peticion_en_memoria(id_peticion=901, radicado='PET-2026-00901')

        with override_settings(
            DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=mod.DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA
        ):
            # 1) /api/peticiones/iniciar/ mantiene HTTP 201 + JSON original sin consultar duplicados
            with patch('peticiones.api.views._obtener_estado_por_nombre', return_value=estado), \
                 patch('peticiones.api.views.registrar_historial_peticion'), \
                 patch('peticiones.api.views.crear_notificaciones_nueva_peticion'), \
                 patch('peticiones.api.views.enviar_correo_peticion_urgente_juridico', return_value={'enviado': False}), \
                 patch('peticiones.api.views.transaction.on_commit', side_effect=lambda fn: fn()), \
                 patch('peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion') as mock_auto_buscar, \
                 patch.object(Peticiones, 'evidenciapeticiones_set', new_callable=PropertyMock) as mock_ev_prop, \
                 patch.object(IniciarPeticionView, 'get_serializer') as mock_get_ser:
                mock_ev_set = MagicMock()
                mock_ev_set.values_list.return_value = []
                mock_ev_prop.return_value = mock_ev_set
                ser = MagicMock()
                ser.is_valid.return_value = True
                ser.save.return_value = peticion
                mock_get_ser.return_value = ser

                req_iniciar = factory.post(
                    '/api/peticiones/iniciar/',
                    {'id_tipo': 1, 'descripcion': 'Perro herido frente al parque principal'},
                    format='json',
                )
                force_authenticate(req_iniciar, user=ciudadano)
                resp_iniciar = IniciarPeticionView.as_view()(req_iniciar)

            self.assertEqual(resp_iniciar.status_code, status.HTTP_201_CREATED)
            self.assertEqual(set(resp_iniciar.data.keys()), CLAVES_RESPUESTA_INICIAR)
            self.assertNotIn('posibles_duplicados', resp_iniciar.data)
            mock_auto_buscar.assert_not_called()

            # 2) El endpoint manual POST /api/peticiones/detectar-duplicados/ sigue operando normalmente
            with patch('peticiones.api.views.detectar_duplicados_desde_solicitud') as mock_manual:
                mock_manual.return_value = {
                    'advertencia_duplicado': True,
                    'posible_duplicado': True,
                    'total_candidatos': 1,
                    'posibles_duplicados': [{'id_peticion': 88}],
                    'mensaje': 'Se encontraron candidatos',
                    'parametros': {},
                }
                req_manual = factory.post(
                    '/api/peticiones/detectar-duplicados/',
                    {'descripcion': 'Perro herido frente al parque principal'},
                    format='json',
                )
                force_authenticate(req_manual, user=juridico)
                resp_manual = DetectarDuplicadosPeticionView.as_view()(req_manual)

            self.assertEqual(resp_manual.status_code, status.HTTP_200_OK)
            self.assertTrue(resp_manual.data['posible_duplicado'])
            mock_manual.assert_called_once()

