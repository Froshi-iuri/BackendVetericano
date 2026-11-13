"""
Pruebas automatizadas para el servicio de alertas de posibles duplicados (Fase 4B).

Se ejecutan sobre una base SQLite aislada en memoria con el esquema real generado
desde los modelos Django (sin conexión a Supabase ni necesidad de `test_vetericano`),
validando restricciones reales de BD, savepoints, bloqueos ordenados y contratos de
`NotificacionJuridico` e `HistorialPeticiones`.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from django.db import IntegrityError, connection, connections
from django.db.backends.sqlite3.base import DatabaseWrapper as SqliteDatabaseWrapper
from django.test import SimpleTestCase

from peticiones.api.serializers import (
    HistorialPeticionesSerializer,
    NotificacionJuridicoSerializer,
)
from peticiones.models import (
    EstadoPeticiones,
    EvidenciaPeticiones,
    HistorialPeticiones,
    NotificacionJuridico,
    Peticiones,
    PeticionPosibleDuplicado,
    TipoPeticion,
    Ubicaciones,
)
from peticiones.services.alertas_duplicados_service import (
    AlertaNoEncontradaError,
    HISTORIAL_TIPO_DUPLICADO_CONFIRMADO,
    HISTORIAL_TIPO_DUPLICADO_DESCARTADO,
    HISTORIAL_TIPO_DUPLICADO_DETECTADO,
    HISTORIAL_TIPO_DUPLICADO_REABIERTO,
    MAX_LONGITUD_TIPO_CAMBIO_HISTORIAL,
    ParDuplicadoInvalidoError,
    PeticionNoEncontradaError,
    SeleccionPrincipalInvalidaError,
    TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
    TransicionAlertaInvalidaError,
    UsuarioRevisionInvalidoError,
    confirmar_duplicado,
    descartar_duplicado,
    gestionar_decision_alerta,
    listar_alertas_duplicados,
    obtener_alerta_por_id,
    obtener_alerta_por_par,
    obtener_resumen_alertas_duplicados,
    reabrir_alerta_pendiente,
    registrar_alerta_posible_duplicado,
    registrar_alertas_desde_deteccion,
)
from users.models import Rol, Sede, Usuarios


_MODELOS_ESQUEMA_AISLADO = (
    Rol,
    Sede,
    Usuarios,
    TipoPeticion,
    EstadoPeticiones,
    Ubicaciones,
    Peticiones,
    EvidenciaPeticiones,
    NotificacionJuridico,
    HistorialPeticiones,
    PeticionPosibleDuplicado,
)


def _crear_conexion_sqlite_aislada() -> SqliteDatabaseWrapper:
    settings_dict = connections.configure_settings({
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    })['default']
    sqlite_conn = SqliteDatabaseWrapper(settings_dict, alias='default')
    sqlite_conn.ensure_connection()
    return sqlite_conn


class _BaseSqliteAisladoTestCase(SimpleTestCase):
    """
    Reemplaza temporalmente `connections['default']` por una conexión SQLite
    en memoria con las tablas reales de `peticiones` y `users`, restaurando la
    conexión original al finalizar cada prueba sin tocar Supabase/Postgres.
    """

    @classmethod
    def _add_databases_failures(cls):
        # Evita envolver métodos del wrapper mientras la prueba usa SQLite aislado.
        return None

    @classmethod
    def _remove_databases_failures(cls):
        return None

    def setUp(self):
        super().setUp()
        self._conexion_original = getattr(connections._connections, 'default', None)
        self._sqlite_conn = _crear_conexion_sqlite_aislada()
        connections._connections.default = self._sqlite_conn
        self.addCleanup(self._restaurar_conexion_original)

        with self._sqlite_conn.schema_editor(atomic=False) as editor:
            for modelo in _MODELOS_ESQUEMA_AISLADO:
                editor.create_model(modelo)

        self.rol_juridico = Rol.objects.create(nombre_rol='JURIDICO')
        self.rol_ciudadano = Rol.objects.create(nombre_rol='USUARIO')
        self.sede = Sede.objects.create(
            nombre='Sede Norte',
            direccion='Cra 1 # 2-3',
            ciudad='Bogotá',
            telefono='3000000000',
        )
        self.usuario_ciudadano = Usuarios.objects.create(
            id_rol=self.rol_ciudadano,
            id_sede=self.sede,
            nombre='Ciudadano Demo',
            email='ciudadano@test.local',
            password='hash',
            activo=True,
        )
        self.usuario_juridico = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=self.sede,
            nombre='Abogada Jurídico',
            email='juridico@test.local',
            password='hash',
            activo=True,
        )
        self.usuario_juridico_2 = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=self.sede,
            nombre='Abogado Revisor 2',
            email='juridico2@test.local',
            password='hash',
            activo=True,
        )
        self.tipo_maltrato = TipoPeticion.objects.create(nombre='MALTRATO')
        self.estado_pendiente = EstadoPeticiones.objects.create(nombre='PENDIENTE')
        self.estado_en_revision = EstadoPeticiones.objects.create(nombre='EN REVISION')
        self.ubicacion_1 = Ubicaciones.objects.create(
            direccion='Calle 10 # 20-30',
            latitud=4.60971,
            longitud=-74.08175,
        )
        self.ubicacion_2 = Ubicaciones.objects.create(
            direccion='Calle 10 # 20-32',
            latitud=4.60972,
            longitud=-74.08176,
        )

    def _restaurar_conexion_original(self):
        try:
            if getattr(self, '_sqlite_conn', None) is not None:
                self._sqlite_conn.close()
        finally:
            if getattr(self, '_conexion_original', None) is not None:
                connections._connections.default = self._conexion_original

    def _crear_peticion(
        self,
        *,
        radicado: str,
        descripcion: str = 'Canino en estado de abandono en vía pública',
        estado: EstadoPeticiones | None = None,
        ubicacion: Ubicaciones | None = None,
        asignado_a: Usuarios | None = None,
        prioridad: str = 'MEDIA',
        foto: str | None = 'https://res.cloudinary.com/demo/image/upload/v1/foto.jpg',
    ) -> Peticiones:
        return Peticiones.objects.create(
            numero_radicado=radicado,
            id_tipo=self.tipo_maltrato,
            id_ubicacion=ubicacion or self.ubicacion_1,
            id_estado=estado or self.estado_pendiente,
            responsable=self.usuario_ciudadano,
            asignado_a=asignado_a,
            asignado_por=self.usuario_juridico if asignado_a else None,
            descripcion=descripcion,
            fecha=datetime(2026, 11, 10, 10, 0, 0),
            prioridad=prioridad,
            foto=foto,
        )


class RegistroIdempotenteAlertasTests(_BaseSqliteAisladoTestCase):
    """Pruebas de creación idempotente, par ordenado, notificaciones y métricas cero."""

    def test_crea_alerta_normalizando_par_inverso_y_preserva_ceros_validos(self):
        p_menor = self._crear_peticion(radicado='PET-2026-0001')
        p_mayor = self._crear_peticion(radicado='PET-2026-0002', ubicacion=self.ubicacion_2)

        evidencia = {
            'similitud_texto': 0.91,
            'coincidencia_texto': True,
            'distancia_metros': 0.0,
            'coincidencia_gps': True,
            'distancia_hamming': 0,
            'similitud_imagen': 1.0,
            'coincidencia_imagen': True,
            'score_combinado': 0.97,
            'criterios_coincidencia': ['TEXTO', 'GPS', 'IMAGEN'],
        }

        res = registrar_alerta_posible_duplicado(
            p_mayor.id_peticion,
            p_menor.id_peticion,
            peticion_origen=p_mayor,
            evidencia_deteccion=evidencia,
            origen_deteccion=PeticionPosibleDuplicado.ORIGEN_REGISTRO,
            usuario_accion=self.usuario_ciudadano,
        )

        self.assertTrue(res.creada)
        self.assertFalse(res.actualizada)
        self.assertTrue(res.notificada)
        self.assertFalse(res.respetada_decision_previa)
        self.assertIsNotNone(res.notificacion_id)
        self.assertEqual(len(res.historial_ids), 2)

        alerta = PeticionPosibleDuplicado.objects.get()
        self.assertEqual(alerta.peticion_menor_id, p_menor.id_peticion)
        self.assertEqual(alerta.peticion_mayor_id, p_mayor.id_peticion)
        self.assertEqual(alerta.peticion_origen_id, p_mayor.id_peticion)
        self.assertEqual(alerta.estado_revision, PeticionPosibleDuplicado.ESTADO_PENDIENTE)
        self.assertIsNone(alerta.peticion_principal_id)
        self.assertEqual(alerta.distancia_metros, 0.0)
        self.assertEqual(alerta.distancia_hamming, 0)
        self.assertEqual(alerta.score_combinado, 0.97)
        self.assertEqual(alerta.criterios_coincidencia, ['TEXTO', 'GPS', 'IMAGEN'])

        # Una sola notificación apuntando a la petición origen
        self.assertEqual(NotificacionJuridico.objects.count(), 1)
        notif = NotificacionJuridico.objects.get(pk=res.notificacion_id)
        self.assertEqual(notif.id_peticion_id, p_mayor.id_peticion)
        self.assertEqual(notif.tipo, TIPO_NOTIFICACION_POSIBLE_DUPLICADO)
        self.assertFalse(notif.leida)
        self.assertIn(f'par #{p_menor.id_peticion}-#{p_mayor.id_peticion})', notif.mensaje)
        self.assertIn('PET-2026-0001', notif.mensaje)
        self.assertIn('PET-2026-0002', notif.mensaje)

        notif_data = NotificacionJuridicoSerializer(notif).data
        self.assertEqual(notif_data['numero_radicado'], 'PET-2026-0002')
        self.assertEqual(notif_data['tipo'], TIPO_NOTIFICACION_POSIBLE_DUPLICADO)

        # Historial registrado para ambas peticiones sin exceder varchar(30)
        historiales = list(HistorialPeticiones.objects.order_by('id_peticion'))
        self.assertEqual(len(historiales), 2)
        for h in historiales:
            self.assertEqual(h.tipo_cambio, HISTORIAL_TIPO_DUPLICADO_DETECTADO)
            self.assertLessEqual(len(h.tipo_cambio), MAX_LONGITUD_TIPO_CAMBIO_HISTORIAL)
            self.assertEqual(h.id_usuario_accion_id, self.usuario_ciudadano.id_usuario)
            self.assertIsNone(h.id_estado_anterior)
            self.assertIsNone(h.id_estado_nuevo)
            ser = HistorialPeticionesSerializer(h).data
            self.assertEqual(ser['tipo_cambio'], HISTORIAL_TIPO_DUPLICADO_DETECTADO)

    def test_reingreso_mismo_par_en_orden_inverso_es_idempotente_y_no_duplica_notificacion(self):
        p1 = self._crear_peticion(radicado='PET-2026-0010')
        p2 = self._crear_peticion(radicado='PET-2026-0011')
        evidencia = {
            'similitud_texto': 0.80,
            'coincidencia_texto': True,
            'score_combinado': 0.80,
            'criterios_coincidencia': ['TEXTO'],
        }

        res1 = registrar_alerta_posible_duplicado(
            p1, p2, peticion_origen=p2, evidencia_deteccion=evidencia
        )
        res2 = registrar_alerta_posible_duplicado(
            p2, p1, peticion_origen=p1, evidencia_deteccion=evidencia
        )

        self.assertTrue(res1.creada)
        self.assertFalse(res2.creada)
        self.assertFalse(res2.actualizada)
        self.assertFalse(res2.notificada)
        self.assertEqual(res1.alerta.id_alerta, res2.alerta.id_alerta)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), 1)
        self.assertEqual(NotificacionJuridico.objects.count(), 1)
        self.assertEqual(HistorialPeticiones.objects.count(), 2)

    def test_actualiza_metricas_si_pendiente_sin_renotificar(self):
        p1 = self._crear_peticion(radicado='PET-2026-0020')
        p2 = self._crear_peticion(radicado='PET-2026-0021')

        registrar_alerta_posible_duplicado(
            p1,
            p2,
            peticion_origen=p2,
            evidencia_deteccion={
                'similitud_texto': 0.65,
                'coincidencia_texto': True,
                'score_combinado': 0.65,
                'criterios_coincidencia': ['TEXTO'],
            },
        )
        res_reanalisis = registrar_alerta_posible_duplicado(
            p2,
            p1,
            peticion_origen=p2,
            origen_deteccion=PeticionPosibleDuplicado.ORIGEN_REANALISIS,
            evidencia_deteccion={
                'similitud_texto': 0.65,
                'coincidencia_texto': True,
                'distancia_hamming': 3,
                'similitud_imagen': 0.9531,
                'coincidencia_imagen': True,
                'score_combinado': 0.91,
                'criterios_coincidencia': ['TEXTO', 'IMAGEN'],
            },
        )

        self.assertFalse(res_reanalisis.creada)
        self.assertTrue(res_reanalisis.actualizada)
        self.assertFalse(res_reanalisis.notificada)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), 1)
        self.assertEqual(NotificacionJuridico.objects.count(), 1)
        alerta = PeticionPosibleDuplicado.objects.get()
        self.assertEqual(alerta.estado_revision, PeticionPosibleDuplicado.ESTADO_PENDIENTE)
        self.assertEqual(alerta.score_combinado, 0.91)
        self.assertEqual(alerta.distancia_hamming, 3)
        self.assertEqual(alerta.origen_deteccion, PeticionPosibleDuplicado.ORIGEN_REANALISIS)

    def test_no_hay_colision_de_prefijo_en_marcador_de_notificacion(self):
        peticiones = [
            self._crear_peticion(radicado=f'PET-2026-{i:04d}')
            for i in range(1, 22)
        ]
        # Primero se notifica el par #1-#20
        r_1_20 = registrar_alerta_posible_duplicado(
            peticiones[0],
            peticiones[19],
            peticion_origen=peticiones[19],
            evidencia_deteccion={'score_combinado': 0.70, 'coincidencia_texto': True},
        )
        # Luego el par #1-#2 (que antes podría haber hecho subcadena con par #1-#20)
        r_1_2 = registrar_alerta_posible_duplicado(
            peticiones[0],
            peticiones[1],
            peticion_origen=peticiones[1],
            evidencia_deteccion={'score_combinado': 0.75, 'coincidencia_texto': True},
        )

        self.assertTrue(r_1_20.notificada)
        self.assertTrue(r_1_2.notificada)
        self.assertEqual(NotificacionJuridico.objects.count(), 2)


class ConcurrenciaYRestriccionesBDTests(_BaseSqliteAisladoTestCase):
    """Pruebas de manejo de concurrencia (race uq_pet_dup_par) y orden de bloqueos."""

    def test_recupera_limpiamente_si_otro_proceso_gana_insercion_uq_pet_dup_par(self):
        p1 = self._crear_peticion(radicado='PET-2026-0100')
        p2 = self._crear_peticion(radicado='PET-2026-0101')

        manager = PeticionPosibleDuplicado.objects
        real_select_for_update = manager.select_for_update
        alerta_ganadora_id: list[int] = []
        estado_carrera = {'inyectada': False}

        class _QuerysetCarrera:
            def __init__(self, qs):
                self._qs = qs

            def filter(self, *args, **kwargs):
                return _QuerysetCarrera(self._qs.filter(*args, **kwargs))

            def first(self):
                resultado = self._qs.first()
                if not estado_carrera['inyectada']:
                    estado_carrera['inyectada'] = True
                    # Simula inserción ganadora entre la lectura inicial y el savepoint de CREATE
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO peticion_posible_duplicado (
                                id_peticion_menor, id_peticion_mayor, id_peticion_origen,
                                estado_revision, id_peticion_principal,
                                similitud_texto, coincidencia_texto,
                                distancia_metros, coincidencia_gps,
                                distancia_hamming, similitud_imagen, coincidencia_imagen,
                                score_combinado, criterios_coincidencia, origen_deteccion,
                                revisado_por_id, observacion_revision, fecha_revision,
                                fecha_creacion, fecha_actualizacion
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            [
                                p1.id_peticion,
                                p2.id_peticion,
                                p2.id_peticion,
                                'PENDIENTE',
                                None,
                                0.88,
                                1,
                                None,
                                0,
                                None,
                                None,
                                0,
                                0.88,
                                '["TEXTO"]',
                                'REGISTRO_PETICION',
                                None,
                                None,
                                None,
                                '2026-11-10 10:05:00',
                                '2026-11-10 10:05:00',
                            ],
                        )
                        alerta_ganadora_id.append(cursor.lastrowid)

                        cursor.execute(
                            """
                            INSERT INTO notificacion_juridico (
                                id_peticion, tipo, mensaje, leida, fecha_creacion
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            [
                                p2.id_peticion,
                                TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
                                f'Posible duplicado detectado (alerta #{alerta_ganadora_id[0]}, '
                                f'par #{p1.id_peticion}-#{p2.id_peticion}): ya notificada.',
                                0,
                                '2026-11-10 10:05:00',
                            ],
                        )
                return resultado

        def simular_carrera_select_for_update(*args, **kwargs):
            return _QuerysetCarrera(real_select_for_update(*args, **kwargs))

        with patch.object(manager, 'select_for_update', side_effect=simular_carrera_select_for_update):
            res = registrar_alerta_posible_duplicado(
                p2.id_peticion,
                p1.id_peticion,
                peticion_origen=p2.id_peticion,
                evidencia_deteccion={
                    'similitud_texto': 0.88,
                    'coincidencia_texto': True,
                    'score_combinado': 0.88,
                    'criterios_coincidencia': ['TEXTO'],
                },
            )

        self.assertFalse(res.creada)
        self.assertFalse(res.notificada)
        self.assertIsNone(res.notificacion_id)
        self.assertEqual(res.alerta.id_alerta, alerta_ganadora_id[0])
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), 1)
        self.assertEqual(NotificacionJuridico.objects.count(), 1)
        self.assertEqual(HistorialPeticiones.objects.count(), 0)

        # Verifica que la restricción SQL sigue impidiendo duplicados directos
        with self.assertRaises(IntegrityError):
            PeticionPosibleDuplicado.objects.create(
                peticion_menor=p1,
                peticion_mayor=p2,
            )

    def test_bloqueos_en_orden_ascendente_para_registro_y_decision(self):
        p1 = self._crear_peticion(radicado='PET-2026-0110')
        p2 = self._crear_peticion(radicado='PET-2026-0111')

        self._sqlite_conn.force_debug_cursor = True
        self._sqlite_conn.queries_log.clear()

        consultas_lock: list[str] = []
        original_select_for_update = Peticiones.objects.select_for_update

        def espiar_lock(*args, **kwargs):
            qs = original_select_for_update(*args, **kwargs)
            consultas_lock.append('select_for_update')
            return qs

        with patch.object(Peticiones.objects, 'select_for_update', side_effect=espiar_lock):
            res = registrar_alerta_posible_duplicado(p2.id_peticion, p1.id_peticion)
            confirmar_duplicado(
                res.alerta.id_alerta,
                peticion_principal=p1.id_peticion,
                usuario_revisor=self.usuario_juridico,
            )

        self.assertEqual(consultas_lock, ['select_for_update', 'select_for_update'])
        sqls = [q['sql'] for q in self._sqlite_conn.queries_log]
        ordenes_peticiones = [
            idx
            for idx, sql in enumerate(sqls)
            if 'FROM "peticiones"' in sql and 'ORDER BY "peticiones"."id_peticion" ASC' in sql
        ]
        self.assertEqual(len(ordenes_peticiones), 2)

        # Ni peticiones ni evidencias reciben UPDATE/DELETE durante el flujo
        self.assertFalse(
            any(
                sql.startswith(
                    (
                        'UPDATE "peticiones"',
                        'DELETE FROM "peticiones"',
                        'UPDATE "evidencia_peticiones"',
                        'DELETE FROM "evidencia_peticiones"',
                    )
                )
                for sql in sqls
            )
        )


class DecisionesYRespetoEstadoPrevioTests(_BaseSqliteAisladoTestCase):
    """Pruebas de decisiones PENDIENTE / CONFIRMADO / DESCARTADO, historial e inmutabilidad."""

    def test_respeta_alerta_descartada_ante_redeteccion_posterior(self):
        p1 = self._crear_peticion(radicado='PET-2026-0200')
        p2 = self._crear_peticion(radicado='PET-2026-0201')

        creacion = registrar_alerta_posible_duplicado(
            p1,
            p2,
            peticion_origen=p2,
            evidencia_deteccion={'similitud_texto': 0.72, 'coincidencia_texto': True, 'score_combinado': 0.72},
        )
        decision = descartar_duplicado(
            creacion.alerta.id_alerta,
            usuario_revisor=self.usuario_juridico,
            observacion='  Falsa alarma: reporte de zona distinta  ',
        )
        self.assertTrue(decision.cambiada)
        self.assertEqual(decision.estado_anterior, 'PENDIENTE')
        self.assertEqual(decision.estado_nuevo, 'DESCARTADO')
        self.assertIsNone(decision.alerta.peticion_principal_id)
        self.assertEqual(decision.alerta.observacion_revision, 'Falsa alarma: reporte de zona distinta')
        fecha_rev_inicial = decision.alerta.fecha_revision
        self.assertIsNotNone(fecha_rev_inicial)

        # Re-detección posterior (incluso con score más alto) no debe sobrescribir DESCARTADO
        redeteccion = registrar_alerta_posible_duplicado(
            p2,
            p1,
            peticion_origen=p2,
            origen_deteccion=PeticionPosibleDuplicado.ORIGEN_ENDPOINT_MANUAL,
            evidencia_deteccion={
                'similitud_texto': 0.99,
                'coincidencia_texto': True,
                'score_combinado': 0.99,
                'criterios_coincidencia': ['TEXTO'],
            },
        )
        self.assertFalse(redeteccion.creada)
        self.assertFalse(redeteccion.actualizada)
        self.assertFalse(redeteccion.notificada)
        self.assertTrue(redeteccion.respetada_decision_previa)

        alerta = PeticionPosibleDuplicado.objects.get(pk=creacion.alerta.id_alerta)
        self.assertEqual(alerta.estado_revision, PeticionPosibleDuplicado.ESTADO_DESCARTADO)
        self.assertIsNone(alerta.peticion_principal_id)
        self.assertEqual(alerta.revisado_por_id, self.usuario_juridico.id_usuario)
        self.assertEqual(alerta.fecha_revision, fecha_rev_inicial)
        self.assertEqual(alerta.score_combinado, 0.72)
        self.assertEqual(NotificacionJuridico.objects.count(), 1)

    def test_respeta_alerta_confirmada_ante_redeteccion_posterior(self):
        p1 = self._crear_peticion(radicado='PET-2026-0210')
        p2 = self._crear_peticion(radicado='PET-2026-0211')

        creacion = registrar_alerta_posible_duplicado(
            p1,
            p2,
            peticion_origen=p2,
            evidencia_deteccion={'score_combinado': 0.85, 'coincidencia_texto': True},
        )
        res_conf = confirmar_duplicado(
            creacion.alerta.id_alerta,
            peticion_principal=p1.id_peticion,
            usuario_revisor=self.usuario_juridico.id_usuario,
            observacion='Caso duplicado verificado por Jurídico.',
        )
        self.assertTrue(res_conf.cambiada)
        self.assertEqual(res_conf.alerta.estado_revision, PeticionPosibleDuplicado.ESTADO_CONFIRMADO)
        self.assertEqual(res_conf.alerta.peticion_principal_id, p1.id_peticion)
        self.assertEqual(res_conf.alerta.revisado_por_id, self.usuario_juridico.id_usuario)

        redeteccion = registrar_alerta_posible_duplicado(
            p1,
            p2,
            evidencia_deteccion={'score_combinado': 0.99, 'coincidencia_imagen': True},
        )
        self.assertTrue(redeteccion.respetada_decision_previa)
        self.assertFalse(redeteccion.actualizada)
        self.assertFalse(redeteccion.notificada)

        alerta = PeticionPosibleDuplicado.objects.get(pk=creacion.alerta.id_alerta)
        self.assertEqual(alerta.estado_revision, PeticionPosibleDuplicado.ESTADO_CONFIRMADO)
        self.assertEqual(alerta.peticion_principal_id, p1.id_peticion)
        self.assertEqual(NotificacionJuridico.objects.count(), 1)

    def test_flujo_completo_decisiones_idempotencia_transiciones_e_inmutabilidad(self):
        p1 = self._crear_peticion(
            radicado='PET-2026-0300',
            descripcion='Perro herido sector Kennedy',
            estado=self.estado_en_revision,
            asignado_a=self.usuario_juridico,
            prioridad='ALTA',
        )
        p2 = self._crear_peticion(
            radicado='PET-2026-0301',
            descripcion='Canino lastimado parque Kennedy',
            estado=self.estado_pendiente,
            prioridad='MEDIA',
        )
        EvidenciaPeticiones.objects.create(
            id_peticion=p1,
            ruta_archivo='https://cdn.test/ev1.jpg',
            descripcion='Foto original 1',
        )
        EvidenciaPeticiones.objects.create(
            id_peticion=p2,
            ruta_archivo='https://cdn.test/ev2.jpg',
            descripcion='Foto original 2',
        )

        snapshot_p1 = (
            p1.numero_radicado, p1.id_tipo_id, p1.id_ubicacion_id, p1.id_estado_id,
            p1.responsable_id, p1.asignado_a_id, p1.descripcion, p1.prioridad, p1.foto,
        )
        snapshot_p2 = (
            p2.numero_radicado, p2.id_tipo_id, p2.id_ubicacion_id, p2.id_estado_id,
            p2.responsable_id, p2.asignado_a_id, p2.descripcion, p2.prioridad, p2.foto,
        )

        alerta = registrar_alerta_posible_duplicado(
            p1, p2, peticion_origen=p2, evidencia_deteccion={'score_combinado': 0.89}
        ).alerta

        # Confirmar exige peticion_principal válida del par
        with self.assertRaises(SeleccionPrincipalInvalidaError):
            gestionar_decision_alerta(alerta.id_alerta, 'CONFIRMADO')

        p_externa = self._crear_peticion(radicado='PET-2026-0999')
        with self.assertRaises(SeleccionPrincipalInvalidaError):
            confirmar_duplicado(alerta.id_alerta, peticion_principal=p_externa)

        # Confirmar válido
        res_conf = confirmar_duplicado(
            alerta.id_alerta,
            peticion_principal=p1,
            usuario_revisor=self.usuario_juridico,
            observacion='Se conserva PET-2026-0300 como principal.',
        )
        self.assertTrue(res_conf.cambiada)
        self.assertEqual(len(res_conf.historial_ids), 2)

        # Repetir exactamente la misma confirmación es idempotente
        res_conf_rep = confirmar_duplicado(
            alerta.id_alerta,
            peticion_principal=p1,
            usuario_revisor=self.usuario_juridico_2,
        )
        self.assertFalse(res_conf_rep.cambiada)
        self.assertEqual(res_conf_rep.historial_ids, ())

        # Cambiar de decisión sin bandera explícita falla
        with self.assertRaises(TransicionAlertaInvalidaError):
            descartar_duplicado(alerta.id_alerta, usuario_revisor=self.usuario_juridico)

        with self.assertRaises(TransicionAlertaInvalidaError):
            confirmar_duplicado(alerta.id_alerta, peticion_principal=p2, usuario_revisor=self.usuario_juridico)

        # Reapertura explícita a PENDIENTE limpia principal y audita
        res_reabrir = reabrir_alerta_pendiente(
            alerta.id_alerta,
            usuario_revisor=self.usuario_juridico_2,
            observacion='Se requiere validar nueva evidencia fotográfica.',
        )
        self.assertTrue(res_reabrir.cambiada)
        self.assertEqual(res_reabrir.estado_anterior, 'CONFIRMADO')
        self.assertEqual(res_reabrir.estado_nuevo, 'PENDIENTE')
        self.assertIsNone(res_reabrir.alerta.peticion_principal_id)

        # Repetir PENDIENTE cuando ya está PENDIENTE es idempotente
        res_pend_idem = gestionar_decision_alerta(alerta.id_alerta, 'PENDIENTE')
        self.assertFalse(res_pend_idem.cambiada)

        # Ahora se puede descartar
        with self.assertRaises(SeleccionPrincipalInvalidaError):
            gestionar_decision_alerta(alerta.id_alerta, 'DESCARTADO', peticion_principal=p1)

        res_desc = descartar_duplicado(
            alerta.id_alerta,
            usuario_revisor=self.usuario_juridico,
            observacion='No corresponde al mismo caso.',
        )
        self.assertTrue(res_desc.cambiada)
        self.assertEqual(res_desc.estado_nuevo, 'DESCARTADO')

        # Repetir descarte es idempotente
        res_desc_idem = descartar_duplicado(alerta.id_alerta)
        self.assertFalse(res_desc_idem.cambiada)

        # Verifica historial completo: detección(2) + confirmación(2) + reapertura(2) + descarte(2) = 8
        tipos = list(HistorialPeticiones.objects.order_by('id_historial').values_list('tipo_cambio', flat=True))
        self.assertEqual(
            tipos,
            [
                HISTORIAL_TIPO_DUPLICADO_DETECTADO,
                HISTORIAL_TIPO_DUPLICADO_DETECTADO,
                HISTORIAL_TIPO_DUPLICADO_CONFIRMADO,
                HISTORIAL_TIPO_DUPLICADO_CONFIRMADO,
                HISTORIAL_TIPO_DUPLICADO_REABIERTO,
                HISTORIAL_TIPO_DUPLICADO_REABIERTO,
                HISTORIAL_TIPO_DUPLICADO_DESCARTADO,
                HISTORIAL_TIPO_DUPLICADO_DESCARTADO,
            ],
        )

        # Verificar que ni peticiones ni evidencias originales sufrieron alteración alguna
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(
            (
                p1.numero_radicado, p1.id_tipo_id, p1.id_ubicacion_id, p1.id_estado_id,
                p1.responsable_id, p1.asignado_a_id, p1.descripcion, p1.prioridad, p1.foto,
            ),
            snapshot_p1,
        )
        self.assertEqual(
            (
                p2.numero_radicado, p2.id_tipo_id, p2.id_ubicacion_id, p2.id_estado_id,
                p2.responsable_id, p2.asignado_a_id, p2.descripcion, p2.prioridad, p2.foto,
            ),
            snapshot_p2,
        )
        self.assertEqual(
            list(
                EvidenciaPeticiones.objects.order_by('id_evidencia').values_list(
                    'id_peticion_id', 'ruta_archivo', 'descripcion'
                )
            ),
            [
                (p1.id_peticion, 'https://cdn.test/ev1.jpg', 'Foto original 1'),
                (p2.id_peticion, 'https://cdn.test/ev2.jpg', 'Foto original 2'),
            ],
        )
        self.assertEqual(NotificacionJuridico.objects.count(), 1)

    def test_rectificacion_explicita_con_bandera_cambio_previo(self):
        p1 = self._crear_peticion(radicado='PET-2026-0350')
        p2 = self._crear_peticion(radicado='PET-2026-0351')

        alerta = registrar_alerta_posible_duplicado(
            p1, p2, notificar=False, registrar_historial=False
        ).alerta
        self.assertEqual(NotificacionJuridico.objects.count(), 0)
        self.assertEqual(HistorialPeticiones.objects.count(), 0)

        descartar_duplicado(alerta.id_alerta, usuario_revisor=self.usuario_juridico)
        res_rect = confirmar_duplicado(
            alerta.id_alerta,
            peticion_principal=p2,
            usuario_revisor=self.usuario_juridico_2,
            observacion='Rectificación autorizada.',
            permitir_cambio_decision_previa=True,
        )
        self.assertTrue(res_rect.cambiada)
        self.assertEqual(res_rect.estado_anterior, PeticionPosibleDuplicado.ESTADO_DESCARTADO)
        self.assertEqual(res_rect.estado_nuevo, PeticionPosibleDuplicado.ESTADO_CONFIRMADO)
        self.assertEqual(res_rect.alerta.peticion_principal_id, p2.id_peticion)
        self.assertEqual(obtener_alerta_por_par(p2, p1).id_alerta, alerta.id_alerta)
        self.assertEqual(obtener_alerta_por_par(p1, p2).id_alerta, alerta.id_alerta)


class BatchDeteccionYConsultasTests(_BaseSqliteAisladoTestCase):
    """Pruebas de integración con salida del detector, validaciones y consultas de lectura."""

    def test_registrar_alertas_desde_deteccion_deduplica_omite_y_respeta_resueltas(self):
        p_a = self._crear_peticion(radicado='PET-2026-0401')
        p_b = self._crear_peticion(radicado='PET-2026-0402')
        p_origen = self._crear_peticion(radicado='PET-2026-0403')

        # Deja previamente descartado el par (p_a, p_origen)
        alerta_previa = registrar_alerta_posible_duplicado(
            p_a, p_origen, peticion_origen=p_origen, evidencia_deteccion={'score_combinado': 0.61}
        ).alerta
        descartar_duplicado(alerta_previa.id_alerta, usuario_revisor=self.usuario_juridico)

        salida_detector = {
            'total_candidatos': 4,
            'posibles_duplicados': [
                # Autorreferencia: se omite
                {
                    'id_peticion': p_origen.id_peticion,
                    'similitud_texto': 1.0,
                    'coincidencia_texto': True,
                    'posible_duplicado': True,
                    'score_combinado': 1.0,
                },
                # Par ya descartado: se respeta
                {
                    'id_peticion': p_a.id_peticion,
                    'similitud_texto': 0.90,
                    'coincidencia_texto': True,
                    'posible_duplicado': True,
                    'score_combinado': 0.90,
                },
                # Candidato repetido en el payload: conserva el de mayor score
                {
                    'id_peticion': p_b.id_peticion,
                    'similitud_texto': 0.70,
                    'coincidencia_texto': True,
                    'posible_duplicado': True,
                    'score_combinado': 0.70,
                },
                {
                    'id_peticion': p_b.id_peticion,
                    'similitud_texto': 0.88,
                    'coincidencia_texto': True,
                    'distancia_metros': 12.5,
                    'coincidencia_gps': True,
                    'posible_duplicado': True,
                    'score_combinado': 0.88,
                    'criterios_coincidencia': ['TEXTO', 'GPS'],
                },
                # Candidato marcado posible_duplicado=False: se omite
                {
                    'id_peticion': 99998,
                    'posible_duplicado': False,
                    'score_combinado': 0.12,
                },
                # Candidato eliminado concurrentemente: se omite de forma segura
                {
                    'id_peticion': 99999,
                    'posible_duplicado': True,
                    'score_combinado': 0.82,
                },
            ],
        }

        res_batch = registrar_alertas_desde_deteccion(
            p_origen,
            salida_detector,
            origen_deteccion=PeticionPosibleDuplicado.ORIGEN_REGISTRO,
            usuario_accion=self.usuario_ciudadano,
        )

        self.assertEqual(res_batch.total_procesados, 2)
        self.assertEqual(res_batch.creadas, 1)
        self.assertEqual(res_batch.respetadas_decision_previa, 1)
        self.assertEqual(res_batch.notificaciones_creadas, 1)
        self.assertEqual(len(res_batch.omitidos), 3)

        alerta_b = obtener_alerta_por_par(p_b, p_origen)
        self.assertIsNotNone(alerta_b)
        self.assertEqual(alerta_b.score_combinado, 0.88)
        self.assertEqual(alerta_b.criterios_coincidencia, ['TEXTO', 'GPS'])

        alerta_a = obtener_alerta_por_id(alerta_previa.id_alerta)
        self.assertEqual(alerta_a.estado_revision, PeticionPosibleDuplicado.ESTADO_DESCARTADO)

        # Consultas agregadas y listado filtrado
        resumen = obtener_resumen_alertas_duplicados()
        self.assertEqual(
            resumen,
            {'PENDIENTE': 1, 'CONFIRMADO': 0, 'DESCARTADO': 1, 'total': 2},
        )
        self.assertEqual(listar_alertas_duplicados(solo_pendientes=True).count(), 1)
        self.assertEqual(listar_alertas_duplicados(id_peticion=p_origen.id_peticion).count(), 2)
        self.assertEqual(
            obtener_resumen_alertas_duplicados(id_peticion=p_b.id_peticion),
            {'PENDIENTE': 1, 'CONFIRMADO': 0, 'DESCARTADO': 0, 'total': 1},
        )

    def test_validaciones_de_entradas_invalidas(self):
        p1 = self._crear_peticion(radicado='PET-2026-0500')
        p2 = self._crear_peticion(radicado='PET-2026-0501')

        with self.assertRaises(ParDuplicadoInvalidoError):
            registrar_alerta_posible_duplicado(p1, p1)

        with self.assertRaises(ParDuplicadoInvalidoError):
            registrar_alerta_posible_duplicado(p1, True)

        with self.assertRaises(ParDuplicadoInvalidoError):
            registrar_alerta_posible_duplicado(p1, p2, origen_deteccion='OTRO')

        with self.assertRaises(ParDuplicadoInvalidoError):
            registrar_alerta_posible_duplicado(p1, p2, peticion_origen=9999)

        with self.assertRaises(ParDuplicadoInvalidoError):
            registrar_alerta_posible_duplicado(p1, p2, evidencia_deteccion={'score_combinado': float('nan')})

        with self.assertRaises(PeticionNoEncontradaError):
            registrar_alerta_posible_duplicado(p1, 999999)

        with self.assertRaises(PeticionNoEncontradaError):
            registrar_alertas_desde_deteccion(999999, [])

        with self.assertRaises(AlertaNoEncontradaError):
            obtener_alerta_por_id(999999)

        with self.assertRaises(AlertaNoEncontradaError):
            descartar_duplicado(999999)

        res = registrar_alerta_posible_duplicado(p1, p2)
        with self.assertRaises(UsuarioRevisionInvalidoError):
            confirmar_duplicado(res.alerta.id_alerta, peticion_principal=p1, usuario_revisor=888888)
