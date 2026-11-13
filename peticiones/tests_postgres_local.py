"""
Pruebas reales sobre PostgreSQL local aislado (test_vetericano clonada de
template_vetericano_test) para validar:
- Esquema real de 0002 (historial_asignacion_peticion, notificacion_juridico) y 0005 (peticion_posible_duplicado)
- Integridad referencial (PROTECT, SET_NULL, CASCADE), unicidad y restricciones CHECK
- Conservacion intacta de peticiones, evidencias, notificaciones_peticiones e historial_peticiones
- Concurrencia real multi-hilo con threading.Barrier en alertas_duplicados_service.py
- RLS restrictivo y privilegios frente a roles anon y authenticated sin afectar a Django
"""

from __future__ import annotations

import threading
from datetime import datetime
import time
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError, OperationalError, connection, connections, transaction
from django.db.models import ProtectedError
from django.db.utils import ProgrammingError
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from clinica.models import Consulta, HistoriaClinica

from inventario.tests.base import VetericanoTransactionTestCase
from peticiones.models import (
    EstadoPeticiones,
    EvidenciaPeticiones,
    HistorialAsignacionPeticion,
    HistorialPeticiones,
    NotificacionJuridico,
    NotificacionPeticion,
    Peticiones,
    PeticionPosibleDuplicado,
    TipoPeticion,
    Ubicaciones,
)
from peticiones.services.alertas_duplicados_service import (
    HISTORIAL_TIPO_DUPLICADO_DETECTADO,
    TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
    confirmar_duplicado,
    descartar_duplicado,
    detectar_y_registrar_duplicados_al_iniciar,
    registrar_alerta_posible_duplicado,
)
from users.models import Rol, Sede, Usuarios


def _es_postgresql() -> bool:
    return connection.vendor == 'postgresql'


@skipUnless(_es_postgresql(), 'Requiere PostgreSQL local aislado')
class PostgresIntegridadYConservacionTests(VetericanoTransactionTestCase):
    """Valida integridad referencial, restricciones SQL reales y cero impacto en datos previos."""

    def setUp(self):
        super().setUp()
        self.rol_juridico = Rol.objects.get(pk=2)
        self.rol_ciudadano = Rol.objects.get(pk=4)
        self.sede = Sede.objects.create(
            nombre='Sede Local PG18',
            direccion='Cra 5 # 10-20',
            ciudad='Popayan',
            telefono='3001112233',
            codigo='PG18-01',
        )
        self.ciudadano = Usuarios.objects.create(
            id_rol=self.rol_ciudadano,
            id_sede=self.sede,
            nombre='Ciudadano PG',
            email='ciudadano.pg@local.test',
            password='hash',
            activo=True,
        )
        self.juridico = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=self.sede,
            nombre='Juridico PG',
            email='juridico.pg@local.test',
            password='hash',
            activo=True,
        )
        self.vet1 = Usuarios.objects.create(
            id_rol=Rol.objects.get(pk=3),
            id_sede=self.sede,
            nombre='Vet Uno',
            email='vet1.pg@local.test',
            password='hash',
            activo=True,
        )
        self.vet2 = Usuarios.objects.create(
            id_rol=Rol.objects.get(pk=3),
            id_sede=self.sede,
            nombre='Vet Dos',
            email='vet2.pg@local.test',
            password='hash',
            activo=True,
        )
        self.tipo = TipoPeticion.objects.first()
        self.estado = EstadoPeticiones.objects.first()
        self.ubicacion = Ubicaciones.objects.create(
            direccion='Calle 1 # 2-3',
            latitud=2.4419,
            longitud=-76.6063,
        )

    def _crear_peticion(self, radicado: str, descripcion: str = 'Canino abandonado') -> Peticiones:
        return Peticiones.objects.create(
            numero_radicado=radicado,
            id_tipo=self.tipo,
            id_ubicacion=self.ubicacion,
            id_estado=self.estado,
            responsable=self.ciudadano,
            asignado_a=self.vet1,
            asignado_por=self.juridico,
            descripcion=descripcion,
            fecha=datetime(2026, 11, 12, 10, 0, 0),
            prioridad='ALTA',
            foto='https://res.cloudinary.com/demo/image/upload/v1/test.jpg',
        )

    def test_esquema_0002_0005_pg_trgm_y_rls_en_postgresql(self):
        with connection.cursor() as cur:
            cur.execute("""
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND c.relname IN (
                      'historial_asignacion_peticion',
                      'notificacion_juridico',
                      'peticion_posible_duplicado'
                  )
                ORDER BY c.relname;
            """)
            rls_rows = cur.fetchall()
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm';")
            ext = cur.fetchone()
            cur.execute("""
                SELECT conname
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                WHERE c.relname = 'peticion_posible_duplicado';
            """)
            constraints = {r[0] for r in cur.fetchall()}

        self.assertEqual(
            rls_rows,
            [
                ('historial_asignacion_peticion', True, False),
                ('notificacion_juridico', True, False),
                ('peticion_posible_duplicado', True, False),
            ],
        )
        self.assertIsNotNone(ext)
        self.assertTrue(
            {
                'chk_pet_dup_par_ordenado',
                'uq_pet_dup_par',
                'chk_pet_dup_estado_valido',
                'chk_pet_dup_principal_par',
                'chk_pet_dup_origen_par',
            }.issubset(constraints)
        )

    def test_restricciones_sql_y_reglas_on_delete_reales(self):
        p1 = self._crear_peticion('RAD-PG-001')
        p2 = self._crear_peticion('RAD-PG-002')
        p3 = self._crear_peticion('RAD-PG-003')
        p4 = self._crear_peticion('RAD-PG-004')

        # 1. chk_pet_dup_par_ordenado rechaza menor >= mayor en SQL directo
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO public.peticion_posible_duplicado (
                            id_peticion_menor, id_peticion_mayor, estado_revision,
                            coincidencia_texto, coincidencia_gps, coincidencia_imagen,
                            score_combinado, criterios_coincidencia, origen_deteccion,
                            fecha_creacion, fecha_actualizacion
                        ) VALUES (%s, %s, 'PENDIENTE', false, false, false, 0.5, '[]'::jsonb,
                                  'REGISTRO_PETICION', NOW(), NOW());
                        """,
                        [p2.id_peticion, p1.id_peticion],
                    )

        # 2. chk_pet_dup_estado_valido rechaza estado no permitido
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PeticionPosibleDuplicado.objects.create(
                    peticion_menor=p1,
                    peticion_mayor=p2,
                    estado_revision='INVALIDO',
                )

        # 3. chk_pet_dup_principal_par y chk_pet_dup_origen_par rechazan id ajeno (p3)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PeticionPosibleDuplicado.objects.create(
                    peticion_menor=p1,
                    peticion_mayor=p2,
                    peticion_principal=p3,
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PeticionPosibleDuplicado.objects.create(
                    peticion_menor=p1,
                    peticion_mayor=p2,
                    peticion_origen=p3,
                )

        # 4. Registro valido + unicidad uq_pet_dup_par
        revisor_extra = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=self.sede,
            nombre='Revisor Extra',
            email='revisor.extra.pg@local.test',
            password='hash',
            activo=True,
        )
        alerta = PeticionPosibleDuplicado.objects.create(
            peticion_menor=p1,
            peticion_mayor=p2,
            peticion_origen=p2,
            revisado_por=revisor_extra,
            score_combinado=0.91,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PeticionPosibleDuplicado.objects.create(
                    peticion_menor=p1,
                    peticion_mayor=p2,
                )

        # 5. PROTECT impide eliminar p1 referenciada por alerta
        with self.assertRaises(ProtectedError):
            p1.delete()

        # 6. SET_NULL al eliminar usuario revisor conserva alerta
        revisor_extra.delete()
        alerta.refresh_from_db()
        self.assertIsNone(alerta.revisado_por_id)

        # 7. CASCADE en tablas de 0002 para p4 (sin alertas de duplicado)
        HistorialAsignacionPeticion.objects.create(
            id_peticion=p4,
            veterinario_anterior=self.vet1,
            veterinario_nuevo=self.vet2,
            motivo='Reasignacion de prueba PG18',
            asignado_por=None,
        )
        NotificacionJuridico.objects.create(
            id_peticion=p4,
            tipo='NUEVA_URGENTE',
            mensaje='Prueba cascada 0002',
            leida=False,
        )
        p4_id = p4.id_peticion
        p4.delete()
        self.assertEqual(HistorialAsignacionPeticion.objects.filter(id_peticion_id=p4_id).count(), 0)
        self.assertEqual(NotificacionJuridico.objects.filter(id_peticion_id=p4_id).count(), 0)

    def test_conserva_peticiones_notificaciones_e_historiales_existentes(self):
        p1 = self._crear_peticion('RAD-CONS-01', descripcion='Canino herido en parque central')
        p2 = self._crear_peticion('RAD-CONS-02', descripcion='Canino herido cerca del parque central')
        ev1 = EvidenciaPeticiones.objects.create(
            id_peticion=p1,
            ruta_archivo='https://res.cloudinary.com/demo/image/upload/v1/ev1.jpg',
            descripcion='Evidencia previa intacta',
        )
        notif_prev = NotificacionPeticion.objects.create(
            id_peticion=p1,
            id_usuario_destino=self.juridico,
            tipo='NUEVA_PETICION',
            titulo='Titulo previo intacto',
            mensaje='Mensaje previo intacto',
            leida=False,
        )
        hist_prev = HistorialPeticiones.objects.create(
            id_peticion=p1,
            tipo_cambio='CREACION',
            id_estado_nuevo=self.estado,
            id_usuario_accion=self.ciudadano,
            observacion='Creacion previa intacta',
        )

        # Flujo combinado: reasignacion (0002) + alerta/notificacion/auditoria (0005 + 0002)
        HistorialAsignacionPeticion.objects.create(
            id_peticion=p1,
            veterinario_anterior=self.vet1,
            veterinario_nuevo=self.vet2,
            motivo='Reasignacion operativa',
            asignado_por=self.juridico,
        )
        res_reg = registrar_alerta_posible_duplicado(
            p2.id_peticion,
            p1.id_peticion,
            peticion_origen=p2.id_peticion,
            evidencia_deteccion={
                'similitud_texto': 0.87,
                'coincidencia_texto': True,
                'score_combinado': 0.87,
                'criterios_coincidencia': ['TEXTO'],
            },
            usuario_accion=self.juridico,
        )
        confirmar_duplicado(
            res_reg.alerta.id_alerta,
            peticion_principal=p1.id_peticion,
            usuario_revisor=self.juridico,
            observacion='Confirmado en PG18',
        )

        # Verificacion de conservacion estricta
        p1.refresh_from_db()
        p2.refresh_from_db()
        ev1.refresh_from_db()
        notif_prev.refresh_from_db()
        hist_prev.refresh_from_db()

        self.assertEqual(p1.descripcion, 'Canino herido en parque central')
        self.assertEqual(p2.descripcion, 'Canino herido cerca del parque central')
        self.assertEqual(ev1.ruta_archivo, 'https://res.cloudinary.com/demo/image/upload/v1/ev1.jpg')
        self.assertEqual(notif_prev.titulo, 'Titulo previo intacto')
        self.assertEqual(notif_prev.mensaje, 'Mensaje previo intacto')
        self.assertEqual(hist_prev.observacion, 'Creacion previa intacta')
        self.assertEqual(HistorialAsignacionPeticion.objects.filter(id_peticion=p1).count(), 1)
        self.assertEqual(NotificacionJuridico.objects.filter(tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO).count(), 1)


@skipUnless(_es_postgresql(), 'Requiere PostgreSQL local aislado')
class PostgresConcurrenciaAlertasTests(VetericanoTransactionTestCase):
    """Pruebas reales multi-hilo con Barrier sobre PostgreSQL para alertas_duplicados_service."""

    def setUp(self):
        super().setUp()
        self.sede = Sede.objects.create(
            nombre='Sede Concurrencia PG18',
            direccion='Av 1 # 2-3',
            ciudad='Popayan',
            telefono='3002223344',
            codigo='CONC-01',
        )
        self.ciudadano = Usuarios.objects.create(
            id_rol=Rol.objects.get(pk=4),
            id_sede=self.sede,
            nombre='Ciudadano Conc',
            email='ciudadano.conc@local.test',
            password='hash',
            activo=True,
        )
        self.juridico = Usuarios.objects.create(
            id_rol=Rol.objects.get(pk=2),
            id_sede=self.sede,
            nombre='Juridico Conc',
            email='juridico.conc@local.test',
            password='hash',
            activo=True,
        )
        self.ubicacion = Ubicaciones.objects.create(
            direccion='Parque Conc',
            latitud=2.442,
            longitud=-76.606,
        )
        self.p1 = Peticiones.objects.create(
            numero_radicado='RAD-CONC-01',
            id_tipo=TipoPeticion.objects.first(),
            id_ubicacion=self.ubicacion,
            id_estado=EstadoPeticiones.objects.first(),
            responsable=self.ciudadano,
            descripcion='Mascota en riesgo sector norte',
            fecha=datetime(2026, 11, 12, 11, 0, 0),
            prioridad='ALTA',
        )
        self.p2 = Peticiones.objects.create(
            numero_radicado='RAD-CONC-02',
            id_tipo=TipoPeticion.objects.first(),
            id_ubicacion=self.ubicacion,
            id_estado=EstadoPeticiones.objects.first(),
            responsable=self.ciudadano,
            descripcion='Mascota en riesgo mismo sector norte',
            fecha=datetime(2026, 11, 12, 11, 5, 0),
            prioridad='ALTA',
        )

    def test_concurrencia_real_hilos_par_invertido_un_solo_registro_y_notificacion(self):
        num_hilos = 6
        barrier = threading.Barrier(num_hilos)
        resultados = []
        errores = []
        lock_res = threading.Lock()

        def worker(idx: int):
            try:
                # Alterna (p1, p2) y (p2, p1) para tensionar el orden de locks y uq_pet_dup_par
                a, b = (self.p1.id_peticion, self.p2.id_peticion) if idx % 2 == 0 else (self.p2.id_peticion, self.p1.id_peticion)
                barrier.wait(timeout=5.0)
                res = registrar_alerta_posible_duplicado(
                    a,
                    b,
                    peticion_origen=b,
                    evidencia_deteccion={
                        'similitud_texto': 0.80 + (idx * 0.01),
                        'coincidencia_texto': True,
                        'score_combinado': 0.80 + (idx * 0.01),
                        'criterios_coincidencia': ['TEXTO'],
                    },
                    usuario_accion=self.juridico.id_usuario,
                )
                with lock_res:
                    resultados.append(res)
            except Exception as exc:
                with lock_res:
                    errores.append(exc)
            finally:
                connection.close()

        hilos = [threading.Thread(target=worker, args=(i,)) for i in range(num_hilos)]
        for t in hilos:
            t.start()
        for t in hilos:
            t.join(timeout=10.0)

        self.assertEqual(errores, [], f'Errores inesperados en concurrencia: {errores}')
        self.assertEqual(len(resultados), num_hilos)
        self.assertEqual(sum(1 for r in resultados if r.creada), 1)
        self.assertEqual(sum(1 for r in resultados if r.notificada), 1)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), 1)
        self.assertEqual(NotificacionJuridico.objects.filter(tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO).count(), 1)
        self.assertEqual(
            HistorialPeticiones.objects.filter(tipo_cambio=HISTORIAL_TIPO_DUPLICADO_DETECTADO).count(),
            2,
        )

    def test_concurrencia_real_decision_y_redeteccion_sin_deadlock(self):
        inicial = registrar_alerta_posible_duplicado(
            self.p1.id_peticion,
            self.p2.id_peticion,
            evidencia_deteccion={
                'similitud_texto': 0.75,
                'coincidencia_texto': True,
                'score_combinado': 0.75,
                'criterios_coincidencia': ['TEXTO'],
            },
            usuario_accion=self.juridico.id_usuario,
        )

        barrier = threading.Barrier(2)
        errores = []
        salidas = {}

        def hilo_confirmar():
            try:
                barrier.wait(timeout=5.0)
                salidas['confirmacion'] = confirmar_duplicado(
                    inicial.alerta.id_alerta,
                    peticion_principal=self.p1.id_peticion,
                    usuario_revisor=self.juridico.id_usuario,
                    observacion='Confirmacion concurrente',
                )
            except Exception as exc:
                errores.append(('confirmar', exc))
            finally:
                connection.close()

        def hilo_redetectar():
            try:
                barrier.wait(timeout=5.0)
                salidas['redeteccion'] = registrar_alerta_posible_duplicado(
                    self.p2.id_peticion,
                    self.p1.id_peticion,
                    evidencia_deteccion={
                        'similitud_texto': 0.99,
                        'coincidencia_texto': True,
                        'score_combinado': 0.99,
                        'criterios_coincidencia': ['TEXTO'],
                    },
                    usuario_accion=self.juridico.id_usuario,
                )
            except Exception as exc:
                errores.append(('redetectar', exc))
            finally:
                connection.close()

        t1 = threading.Thread(target=hilo_confirmar)
        t2 = threading.Thread(target=hilo_redetectar)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        self.assertEqual(errores, [], f'Se detectaron errores o deadlocks: {errores}')
        alerta_final = PeticionPosibleDuplicado.objects.get(pk=inicial.alerta.id_alerta)
        self.assertEqual(alerta_final.estado_revision, PeticionPosibleDuplicado.ESTADO_CONFIRMADO)
        self.assertEqual(alerta_final.peticion_principal_id, self.p1.id_peticion)

        # Una redeteccion posterior debe respetar siempre la decision CONFIRMADO
        post = registrar_alerta_posible_duplicado(
            self.p2.id_peticion,
            self.p1.id_peticion,
            evidencia_deteccion={
                'similitud_texto': 1.0,
                'coincidencia_texto': True,
                'score_combinado': 1.0,
                'criterios_coincidencia': ['TEXTO'],
            },
        )
        self.assertTrue(post.respetada_decision_previa)
        self.assertFalse(post.creada)
        self.assertFalse(post.notificada)


@skipUnless(_es_postgresql(), 'Requiere PostgreSQL local aislado')
class PostgresRLSRestrictivoTests(VetericanoTransactionTestCase):
    """Verifica RLS restrictivo para anon/authenticated y acceso normal para Django."""

    def setUp(self):
        super().setUp()
        self.sede = Sede.objects.create(
            nombre='Sede RLS PG18',
            direccion='Calle RLS 1',
            ciudad='Popayan',
            telefono='3003334455',
            codigo='RLS-01',
        )
        self.ciudadano = Usuarios.objects.create(
            id_rol=Rol.objects.get(pk=4),
            id_sede=self.sede,
            nombre='Ciudadano RLS',
            email='ciudadano.rls@local.test',
            password='hash',
            activo=True,
        )
        self.vet = Usuarios.objects.create(
            id_rol=Rol.objects.get(pk=3),
            id_sede=self.sede,
            nombre='Vet RLS',
            email='vet.rls@local.test',
            password='hash',
            activo=True,
        )
        self.ubicacion = Ubicaciones.objects.create(
            direccion='Barrio RLS',
            latitud=2.44,
            longitud=-76.60,
        )
        self.p1 = Peticiones.objects.create(
            numero_radicado='RAD-RLS-01',
            id_tipo=TipoPeticion.objects.first(),
            id_ubicacion=self.ubicacion,
            id_estado=EstadoPeticiones.objects.first(),
            responsable=self.ciudadano,
            descripcion='Caso confidencial RLS 1',
            fecha=datetime(2026, 11, 12, 12, 0, 0),
        )
        self.p2 = Peticiones.objects.create(
            numero_radicado='RAD-RLS-02',
            id_tipo=TipoPeticion.objects.first(),
            id_ubicacion=self.ubicacion,
            id_estado=EstadoPeticiones.objects.first(),
            responsable=self.ciudadano,
            descripcion='Caso confidencial RLS 2',
            fecha=datetime(2026, 11, 12, 12, 5, 0),
        )

    def test_rls_bloquea_anon_y_authenticated_y_django_conserva_acceso(self):
        hist = HistorialAsignacionPeticion.objects.create(
            id_peticion=self.p1,
            veterinario_nuevo=self.vet,
            motivo='Privado asignacion',
        )
        notif = NotificacionJuridico.objects.create(
            id_peticion=self.p1,
            tipo='POSIBLE_DUPLICADO',
            mensaje='Privado notificacion',
        )
        alerta = PeticionPosibleDuplicado.objects.create(
            peticion_menor=self.p1,
            peticion_mayor=self.p2,
            observacion_revision='Privado alerta',
        )

        # 1. Django (conexion postgres) lee y actualiza las 3 tablas sin problema
        self.assertEqual(HistorialAsignacionPeticion.objects.count(), 1)
        self.assertEqual(NotificacionJuridico.objects.count(), 1)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), 1)
        notif.leida = True
        notif.save(update_fields=['leida'])

        tablas = (
            'historial_asignacion_peticion',
            'notificacion_juridico',
            'peticion_posible_duplicado',
        )

        # 2. Con REVOKE + RLS aplicados: anon y authenticated reciben permission denied directo
        for rol_api in ('anon', 'authenticated'):
            for tbl in tablas:
                with self.assertRaises(ProgrammingError):
                    with transaction.atomic():
                        with connection.cursor() as cur:
                            cur.execute(f'SET LOCAL ROLE {rol_api};')
                            cur.execute(f'SELECT COUNT(*) FROM public."{tbl}";')

        # 3. Simulacion temporal de DEFAULT_ACL (GRANT SELECT/INSERT revertido al final):
        #    comprueba que RLS por si solo devuelve 0 filas en SELECT y bloquea INSERT con 42501
        with transaction.atomic():
            sid = transaction.savepoint()
            with connection.cursor() as cur:
                for tbl in tablas:
                    cur.execute(f'GRANT ALL ON TABLE public."{tbl}" TO anon, authenticated;')

            for rol_api in ('anon', 'authenticated'):
                for tbl in tablas:
                    with transaction.atomic():
                        with connection.cursor() as cur:
                            cur.execute(f'SET LOCAL ROLE {rol_api};')
                            cur.execute(f'SELECT COUNT(*) FROM public."{tbl}";')
                            self.assertEqual(
                                cur.fetchone()[0],
                                0,
                                f'RLS debe ocultar todas las filas en {tbl} para {rol_api}',
                            )

                with self.assertRaises(ProgrammingError) as ctx_ins:
                    with transaction.atomic():
                        with connection.cursor() as cur:
                            cur.execute(f'SET LOCAL ROLE {rol_api};')
                            cur.execute(
                                """
                                INSERT INTO public.notificacion_juridico (
                                    id_peticion, tipo, mensaje, leida, fecha_creacion
                                ) VALUES (%s, 'TEST', 'intruso', false, NOW());
                                """,
                                [self.p1.id_peticion],
                            )
                self.assertIsNotNone(ctx_ins.exception.__cause__)
                self.assertEqual(ctx_ins.exception.__cause__.pgcode, '42501')

            transaction.savepoint_rollback(sid)

        # 4. Tras rollback del GRANT temporal, Django sigue operando normalmente
        self.assertTrue(NotificacionJuridico.objects.filter(pk=notif.pk, leida=True).exists())
        self.assertTrue(HistorialAsignacionPeticion.objects.filter(pk=hist.pk).exists())
        self.assertTrue(PeticionPosibleDuplicado.objects.filter(pk=alerta.pk).exists())


@skipUnless(_es_postgresql(), 'Requiere PostgreSQL local aislado')
class PostgresAlertasDuplicadosAPITests(VetericanoTransactionTestCase):
    """Pruebas reales E2E de los 4 endpoints Fase 4C sobre PostgreSQL local aislado."""

    def setUp(self):
        super().setUp()
        self.sede = Sede.objects.create(
            nombre='Sede API PG18',
            direccion='Cra 10 # 20-30',
            ciudad='Popayan',
            telefono='3101112233',
            codigo='APIPG-01',
        )
        self.rol_admin = Rol.objects.get(pk=1)
        self.rol_juridico = Rol.objects.get(pk=2)
        self.rol_vet = Rol.objects.get(pk=3)
        self.rol_ciudadano = Rol.objects.get(pk=4)
        self.rol_super = Rol.objects.filter(pk=5).first() or Rol.objects.create(
            id_rol=5,
            nombre_rol='super_administrador',
        )

        self.admin = Usuarios.objects.create(
            id_rol=self.rol_admin,
            id_sede=self.sede,
            nombre='Admin',
            apellido='PG',
            email='admin.api@local.test',
            password='hash',
            activo=True,
        )
        self.juridico = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=self.sede,
            nombre='Laura',
            apellido='Juridica',
            email='juridico.api@local.test',
            password='hash',
            activo=True,
        )
        self.vet = Usuarios.objects.create(
            id_rol=self.rol_vet,
            id_sede=self.sede,
            nombre='Carlos',
            apellido='Vet',
            email='vet.api@local.test',
            password='hash',
            matricula_profesional='MP-987',
            activo=True,
        )
        self.ciudadano = Usuarios.objects.create(
            id_rol=self.rol_ciudadano,
            id_sede=self.sede,
            nombre='Mario',
            apellido='Ciudadano',
            email='ciudadano.api@local.test',
            password='hash',
            activo=True,
        )
        self.superadmin = Usuarios.objects.create(
            id_rol=self.rol_super,
            id_sede=self.sede,
            nombre='Super',
            apellido='Admin',
            email='super.api@local.test',
            password='hash',
            activo=True,
        )
        self.juridico_inactivo = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=self.sede,
            nombre='Inactivo',
            apellido='Juridico',
            email='inactivo.api@local.test',
            password='hash',
            activo=False,
        )

        self.tipo = TipoPeticion.objects.first()
        self.estado_inicial = EstadoPeticiones.objects.order_by('id_estado').first()
        self.estado_segundo = EstadoPeticiones.objects.order_by('id_estado').last()
        self.ubic1 = Ubicaciones.objects.create(
            direccion='Parque Caldas #1-10',
            latitud=2.4419000,
            longitud=-76.6063000,
        )
        self.ubic2 = Ubicaciones.objects.create(
            direccion='Cra 6 #4-20',
            latitud=2.4420000,
            longitud=-76.6064000,
        )

        self.p1 = Peticiones.objects.create(
            numero_radicado='PET-API-0001',
            id_tipo=self.tipo,
            id_ubicacion=self.ubic1,
            id_estado=self.estado_inicial,
            responsable=self.ciudadano,
            descripcion='Perro herido cerca al parque principal',
            fecha=datetime(2026, 11, 12, 9, 0, 0),
            prioridad='ALTA',
            foto='https://res.cloudinary.com/demo/image/upload/v1/p1_main.jpg',
        )
        EvidenciaPeticiones.objects.create(
            id_peticion=self.p1,
            ruta_archivo='https://res.cloudinary.com/demo/image/upload/v1/p1_extra.jpg',
            descripcion='Evidencia adicional p1',
        )

        self.p2 = Peticiones.objects.create(
            numero_radicado='PET-API-0002',
            id_tipo=self.tipo,
            id_ubicacion=self.ubic2,
            id_estado=self.estado_segundo,
            responsable=self.ciudadano,
            asignado_a=self.vet,
            asignado_por=self.juridico,
            fecha_asignacion=datetime(2026, 11, 12, 11, 0, 0),
            descripcion='Canino lesionado frente al parque principal',
            fecha=datetime(2026, 11, 12, 10, 30, 0),
            prioridad='MEDIA',
            foto='https://res.cloudinary.com/demo/image/upload/v1/p2_main.jpg',
        )
        self.p3 = Peticiones.objects.create(
            numero_radicado='PET-API-0003',
            id_tipo=self.tipo,
            id_ubicacion=self.ubic1,
            id_estado=self.estado_inicial,
            responsable=self.ciudadano,
            descripcion='Gato atrapado en alcantarilla sur',
            fecha=datetime(2026, 11, 12, 11, 30, 0),
            prioridad='BAJA',
        )

        self.alerta_12 = registrar_alerta_posible_duplicado(
            self.p1,
            self.p2,
            peticion_origen=self.p2,
            evidencia_deteccion={
                'similitud_texto': 0.88,
                'coincidencia_texto': True,
                'distancia_metros': 15.2,
                'coincidencia_gps': True,
                'distancia_hamming': 4,
                'similitud_imagen': 0.9375,
                'coincidencia_imagen': True,
                'score_combinado': 0.91,
                'criterios_coincidencia': ['TEXTO', 'GPS', 'IMAGEN'],
            },
        ).alerta
        self.alerta_13 = registrar_alerta_posible_duplicado(
            self.p1,
            self.p3,
            peticion_origen=self.p3,
            evidencia_deteccion={
                'similitud_texto': 0.72,
                'coincidencia_texto': True,
                'score_combinado': 0.72,
                'criterios_coincidencia': ['TEXTO'],
            },
        ).alerta

    def test_permisos_filtros_paginacion_detalle_y_conservacion_datos_pg(self):
        client = APIClient()

        # 1. Sin autenticar -> 401
        self.assertEqual(client.get('/api/peticiones/duplicados/alertas/').status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Roles no autorizados o usuario inactivo -> 403
        for denied_user in (self.vet, self.ciudadano, self.superadmin, self.juridico_inactivo):
            client.force_authenticate(user=denied_user)
            self.assertEqual(client.get('/api/peticiones/duplicados/alertas/').status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(
                client.get(f'/api/peticiones/duplicados/alertas/{self.alerta_12.id_alerta}/').status_code,
                status.HTTP_403_FORBIDDEN,
            )
            self.assertEqual(
                client.post(
                    f'/api/peticiones/duplicados/alertas/{self.alerta_12.id_alerta}/resolver/',
                    {'estado_revision': 'DESCARTADO'},
                    format='json',
                ).status_code,
                status.HTTP_403_FORBIDDEN,
            )
            self.assertEqual(
                client.get(f'/api/peticiones/{self.p1.id_peticion}/duplicados/').status_code,
                status.HTTP_403_FORBIDDEN,
            )

        # 3. Jurídico y Admin listan con paginación y filtros
        client.force_authenticate(user=self.admin)
        resp_list = client.get('/api/peticiones/duplicados/alertas/?estado=PENDIENTE&page=1&page_size=1')
        self.assertEqual(resp_list.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_list.data['count'], 2)
        self.assertEqual(resp_list.data['total_pages'], 2)
        self.assertEqual(len(resp_list.data['results']), 1)
        self.assertEqual(resp_list.data['results'][0]['id_alerta'], self.alerta_12.id_alerta)
        self.assertEqual(resp_list.data['resumen']['PENDIENTE'], 2)

        resp_filt_pet = client.get(f'/api/peticiones/duplicados/alertas/?id_peticion={self.p3.id_peticion}')
        self.assertEqual(resp_filt_pet.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_filt_pet.data['count'], 1)
        self.assertEqual(resp_filt_pet.data['results'][0]['id_alerta'], self.alerta_13.id_alerta)

        # 4. Comparación detallada en BD real (descripción, fotos, ubicación, estado y asignaciones)
        client.force_authenticate(user=self.juridico)
        resp_det = client.get(f'/api/peticiones/duplicados/alertas/{self.alerta_12.id_alerta}/')
        self.assertEqual(resp_det.status_code, status.HTTP_200_OK)
        det = resp_det.data
        self.assertEqual(det['peticion_menor']['id_peticion'], self.p1.id_peticion)
        self.assertEqual(det['peticion_menor']['total_fotos'], 2)
        self.assertEqual(len(det['peticion_menor']['evidencias']), 1)
        self.assertEqual(det['peticion_menor']['ubicacion']['direccion'], 'Parque Caldas #1-10')
        self.assertEqual(det['peticion_mayor']['asignacion']['asignada'], True)
        self.assertEqual(det['peticion_mayor']['asignacion']['asignado_a']['id_usuario'], self.vet.id_usuario)
        self.assertEqual(det['peticion_mayor']['asignacion']['asignado_a']['matricula_profesional'], 'MP-987')

        # 5. Alertas por petición y 404 para inexistente
        resp_pet = client.get(f'/api/peticiones/{self.p1.id_peticion}/duplicados/')
        self.assertEqual(resp_pet.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_pet.data['count'], 2)
        self.assertEqual(client.get('/api/peticiones/999999/duplicados/').status_code, status.HTTP_404_NOT_FOUND)

        # 6. Petición principal inválida -> 400
        resp_invalid_princ = client.post(
            f'/api/peticiones/duplicados/alertas/{self.alerta_12.id_alerta}/resolver/',
            {'estado_revision': 'CONFIRMADO', 'peticion_principal': self.p3.id_peticion},
            format='json',
        )
        self.assertEqual(resp_invalid_princ.status_code, status.HTTP_400_BAD_REQUEST)

        # Snapshot previo de peticiones e historias clínicas
        snap_p1 = (self.p1.id_estado_id, self.p1.asignado_a_id, self.p1.asignado_por_id, self.p1.descripcion, self.p1.foto)
        snap_p2 = (self.p2.id_estado_id, self.p2.asignado_a_id, self.p2.asignado_por_id, self.p2.descripcion, self.p2.foto)
        historias_antes = HistoriaClinica.objects.count()
        consultas_antes = Consulta.objects.count()
        hist_asig_antes = HistorialAsignacionPeticion.objects.count()

        # 7. Confirmar alerta 12 y descartar alerta 13
        resp_conf = client.post(
            f'/api/peticiones/duplicados/alertas/{self.alerta_12.id_alerta}/resolver/',
            {
                'estado_revision': 'CONFIRMADO',
                'peticion_principal': self.p1.id_peticion,
                'observacion': 'Mismo perro confirmado por Jurídico',
            },
            format='json',
        )
        self.assertEqual(resp_conf.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_conf.data['cambiada'])
        self.assertEqual(resp_conf.data['alerta']['estado_revision'], 'CONFIRMADO')
        self.assertEqual(resp_conf.data['alerta']['peticion_principal_id'], self.p1.id_peticion)
        self.assertEqual(resp_conf.data['alerta']['revisado_por']['id_usuario'], self.juridico.id_usuario)

        # Conflicto 409 al intentar cambiar decisión previa
        resp_conflict = client.post(
            f'/api/peticiones/duplicados/alertas/{self.alerta_12.id_alerta}/resolver/',
            {'estado_revision': 'DESCARTADO', 'observacion': 'Intento contradictorio'},
            format='json',
        )
        self.assertEqual(resp_conflict.status_code, status.HTTP_409_CONFLICT)

        resp_desc = client.post(
            f'/api/peticiones/duplicados/alertas/{self.alerta_13.id_alerta}/resolver/',
            {'estado_revision': 'DESCARTADO', 'observacion': 'Son especies diferentes'},
            format='json',
        )
        self.assertEqual(resp_desc.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_desc.data['alerta']['estado_revision'], 'DESCARTADO')

        # 8. Filtro tras resolver muestra resumen exacto
        resp_res = client.get('/api/peticiones/duplicados/alertas/?estado=CONFIRMADO')
        self.assertEqual(resp_res.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_res.data['count'], 1)
        self.assertEqual(
            resp_res.data['resumen'],
            {'PENDIENTE': 0, 'CONFIRMADO': 1, 'DESCARTADO': 1, 'total': 2},
        )

        # 9. Verificar conservación estricta de peticiones, asignaciones e historias clínicas
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual((self.p1.id_estado_id, self.p1.asignado_a_id, self.p1.asignado_por_id, self.p1.descripcion, self.p1.foto), snap_p1)
        self.assertEqual((self.p2.id_estado_id, self.p2.asignado_a_id, self.p2.asignado_por_id, self.p2.descripcion, self.p2.foto), snap_p2)
        self.assertEqual(HistoriaClinica.objects.count(), historias_antes)
        self.assertEqual(Consulta.objects.count(), consultas_antes)
        self.assertEqual(HistorialAsignacionPeticion.objects.count(), hist_asig_antes)

        # 10. Verificar que /api/peticiones/iniciar/ mantiene HTTP 201 + JSON original
        # y dispara detección automática idempotente en BD (Fase 4D) sin alterar p1/p2
        total_alertas_prev = PeticionPosibleDuplicado.objects.count()
        client.force_authenticate(user=self.ciudadano)
        with override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True):
            resp_iniciar = client.post(
                '/api/peticiones/iniciar/',
                {
                    'id_tipo': self.tipo.id_tipo,
                    'descripcion': 'Perro herido cerca al parque principal',
                    'prioridad': 'ALTA',
                },
                format='json',
            )
        self.assertEqual(resp_iniciar.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('posibles_duplicados', resp_iniciar.data)
        self.assertEqual(
            set(resp_iniciar.data.keys()),
            {
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
            },
        )
        id_nueva = resp_iniciar.data['id_peticion']
        self.assertGreater(PeticionPosibleDuplicado.objects.count(), total_alertas_prev)
        alerta_auto = PeticionPosibleDuplicado.objects.filter(
            peticion_menor_id=min(self.p1.id_peticion, id_nueva),
            peticion_mayor_id=max(self.p1.id_peticion, id_nueva),
        ).first()
        self.assertIsNotNone(alerta_auto)
        self.assertEqual(alerta_auto.origen_deteccion, PeticionPosibleDuplicado.ORIGEN_REGISTRO)
        self.assertEqual(alerta_auto.estado_revision, PeticionPosibleDuplicado.ESTADO_PENDIENTE)
        self.assertTrue(alerta_auto.coincidencia_texto)
        self.assertFalse(alerta_auto.coincidencia_imagen)

        # Confirmar conservación intacta de p1 y p2 tras la detección automática
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual((self.p1.id_estado_id, self.p1.asignado_a_id, self.p1.asignado_por_id, self.p1.descripcion, self.p1.foto), snap_p1)
        self.assertEqual((self.p2.id_estado_id, self.p2.asignado_a_id, self.p2.asignado_por_id, self.p2.descripcion, self.p2.foto), snap_p2)

    def test_resolucion_simultanea_endpoint_en_postgresql(self):
        barrier = threading.Barrier(2)
        respuestas = []
        errores = []
        lock = threading.Lock()
        url = f'/api/peticiones/duplicados/alertas/{self.alerta_12.id_alerta}/resolver/'

        def worker(actor, payload):
            local_client = APIClient()
            local_client.force_authenticate(user=actor)
            try:
                barrier.wait(timeout=5)
                resp = local_client.post(url, payload, format='json')
                with lock:
                    respuestas.append(resp.status_code)
            except Exception as exc:
                with lock:
                    errores.append(exc)
            finally:
                connections.close_all()

        t1 = threading.Thread(
            target=worker,
            args=(
                self.juridico,
                {
                    'estado_revision': 'CONFIRMADO',
                    'peticion_principal': self.p1.id_peticion,
                    'observacion': 'Confirma Juridico',
                },
            ),
        )
        t2 = threading.Thread(
            target=worker,
            args=(
                self.admin,
                {
                    'estado_revision': 'DESCARTADO',
                    'observacion': 'Descarta Admin simultaneo',
                },
            ),
        )
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(errores, [])
        self.assertEqual(sorted(respuestas), [status.HTTP_200_OK, status.HTTP_409_CONFLICT])

        alerta_final = PeticionPosibleDuplicado.objects.get(pk=self.alerta_12.id_alerta)
        self.assertIn(
            alerta_final.estado_revision,
            (PeticionPosibleDuplicado.ESTADO_CONFIRMADO, PeticionPosibleDuplicado.ESTADO_DESCARTADO),
        )


@skipUnless(_es_postgresql(), 'Requiere PostgreSQL local aislado')
class PostgresIniciarDeteccionAutomaticaTests(VetericanoTransactionTestCase):
    """Pruebas de integración PG18 local para Fase 4D (detección automática en /iniciar/)."""

    def setUp(self):
        super().setUp()
        self.rol_admin = Rol.objects.get(pk=1)
        self.rol_juridico = Rol.objects.get(pk=2)
        self.rol_vet = Rol.objects.get(pk=3)
        self.rol_ciudadano = Rol.objects.get(pk=4)
        self.sede = Sede.objects.create(
            nombre='Sede Fase 4D PG18',
            ciudad='Popayan',
            direccion='Calle 5 #4-20',
            telefono='3001112233',
            codigo='PG18-4D',
        )
        self.admin = Usuarios.objects.create(
            id_rol=self.rol_admin,
            id_sede=self.sede,
            nombre='Admin',
            apellido='Fase4D',
            email='admin.f4d@local.test',
            password='hash',
            activo=True,
        )
        self.juridico = Usuarios.objects.create(
            id_rol=self.rol_juridico,
            id_sede=self.sede,
            nombre='Juridico',
            apellido='Fase4D',
            email='juridico.f4d@local.test',
            password='hash',
            activo=True,
        )
        self.vet = Usuarios.objects.create(
            id_rol=self.rol_vet,
            id_sede=self.sede,
            nombre='Vet',
            apellido='Fase4D',
            email='vet.f4d@local.test',
            password='hash',
            activo=True,
        )
        self.ciudadano = Usuarios.objects.create(
            id_rol=self.rol_ciudadano,
            id_sede=self.sede,
            nombre='Ciudadano',
            apellido='Fase4D',
            email='ciudadano.f4d@local.test',
            password='hash',
            activo=True,
        )
        self.tipo = TipoPeticion.objects.first()
        self.estado_inicial = (
            EstadoPeticiones.objects.filter(nombre__iexact='En revisión').first()
            or EstadoPeticiones.objects.order_by('id_estado').first()
        )
        self.ubic_base = Ubicaciones.objects.create(
            direccion='Parque Caldas Centro',
            latitud=2.4419000,
            longitud=-76.6063000,
        )
        self.p_base = Peticiones.objects.create(
            numero_radicado='PET-4D-0001',
            id_tipo=self.tipo,
            id_ubicacion=self.ubic_base,
            id_estado=self.estado_inicial,
            responsable=self.ciudadano,
            asignado_a=self.vet,
            asignado_por=self.juridico,
            fecha_asignacion=datetime(2026, 11, 12, 8, 0, 0),
            descripcion='Perro herido atropellado frente al parque Caldas centro',
            fecha=datetime(2026, 11, 12, 8, 0, 0),
            prioridad='ALTA',
            foto='https://res.cloudinary.com/demo/image/upload/v1/base_f4d.jpg',
        )
        EvidenciaPeticiones.objects.create(
            id_peticion=self.p_base,
            ruta_archivo='https://res.cloudinary.com/demo/image/upload/v1/base_extra_f4d.jpg',
            descripcion='Evidencia base Fase 4D',
        )
        HistorialAsignacionPeticion.objects.create(
            id_peticion=self.p_base,
            veterinario_anterior=None,
            veterinario_nuevo=self.vet,
            motivo='Asignacion inicial Fase 4D',
            asignado_por=self.juridico,
        )

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True)
    def test_iniciar_genera_alertas_pg_trgm_y_gps_sin_descargar_imagenes_ni_alterar_datos(self):
        client = APIClient()
        client.force_authenticate(user=self.ciudadano)

        snap_base = (
            self.p_base.id_estado_id,
            self.p_base.asignado_a_id,
            self.p_base.asignado_por_id,
            self.p_base.descripcion,
            self.p_base.foto,
        )
        historias_prev = HistoriaClinica.objects.count()
        consultas_prev = Consulta.objects.count()
        hist_asig_prev = HistorialAsignacionPeticion.objects.count()

        # Garantizar CERO descargas Cloudinary / HTTP externas durante /iniciar/
        with patch(
            'peticiones.services.imagen_hash_service.descargar_imagen_segura',
            side_effect=AssertionError('No debe descargar imágenes Cloudinary durante /iniciar/'),
        ), patch(
            'peticiones.services.duplicados_service.calcular_hashes_urls',
            side_effect=AssertionError('No debe calcular dHash durante /iniciar/'),
        ), patch(
            'requests.get',
            side_effect=AssertionError('No debe hacer requests.get en detección automática'),
        ):
            t0 = time.perf_counter()
            # A) Coincidencia por texto (pg_trgm) + fallback GPS (0,0) estilo Android offline + fotos URL
            resp_texto = client.post(
                '/api/peticiones/iniciar/',
                {
                    'id_tipo': self.tipo.id_tipo,
                    'descripcion': 'Canino herido atropellado frente al parque Caldas centro',
                    'prioridad': 'Media',
                    'direccion': 'Sin GPS exacto',
                    'latitud': 0.0,
                    'longitud': 0.0,
                    'foto': 'https://res.cloudinary.com/demo/image/upload/v1/android_1.jpg',
                    'fotos': [
                        'https://res.cloudinary.com/demo/image/upload/v1/android_1.jpg',
                        'https://res.cloudinary.com/demo/image/upload/v1/android_2.jpg',
                    ],
                },
                format='json',
            )
            latencia_ms = (time.perf_counter() - t0) * 1000.0

        self.assertLess(latencia_ms, 3000.0)
        self.assertEqual(resp_texto.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            set(resp_texto.data.keys()),
            {
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
            },
        )
        self.assertEqual(resp_texto.data['total_fotos'], 2)
        self.assertEqual(resp_texto.data['foto'], 'https://res.cloudinary.com/demo/image/upload/v1/android_1.jpg')

        id_pet_texto = resp_texto.data['id_peticion']
        alerta_texto = PeticionPosibleDuplicado.objects.filter(
            peticion_menor_id=self.p_base.id_peticion,
            peticion_mayor_id=id_pet_texto,
        ).first()
        self.assertIsNotNone(alerta_texto)
        self.assertEqual(alerta_texto.origen_deteccion, PeticionPosibleDuplicado.ORIGEN_REGISTRO)
        self.assertEqual(alerta_texto.estado_revision, PeticionPosibleDuplicado.ESTADO_PENDIENTE)
        self.assertTrue(alerta_texto.coincidencia_texto)
        self.assertFalse(alerta_texto.coincidencia_gps)
        self.assertFalse(alerta_texto.coincidencia_imagen)
        self.assertGreaterEqual(alerta_texto.similitud_texto, 0.35)

        marcador_txt = f'par #{alerta_texto.peticion_menor_id}-#{alerta_texto.peticion_mayor_id})'
        self.assertEqual(
            NotificacionJuridico.objects.filter(
                tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
                mensaje__contains=marcador_txt,
            ).count(),
            1,
        )
        self.assertEqual(
            HistorialPeticiones.objects.filter(
                id_peticion_id__in=[self.p_base.id_peticion, id_pet_texto],
                tipo_cambio=HISTORIAL_TIPO_DUPLICADO_DETECTADO,
            ).count(),
            2,
        )

        # B) Coincidencia únicamente por GPS (Haversine ~15m, texto completamente distinto)
        with patch(
            'peticiones.services.duplicados_service.calcular_hashes_urls',
            side_effect=AssertionError('No debe calcular dHash durante /iniciar/'),
        ):
            resp_gps = client.post(
                '/api/peticiones/iniciar/',
                {
                    'id_tipo': self.tipo.id_tipo,
                    'descripcion': 'Felino extraviado necesita rescate urgente en vivienda',
                    'prioridad': 'ALTA',
                    'direccion': 'Carrera 6 #4-10',
                    'latitud': 2.4420000,
                    'longitud': -76.6064000,
                },
                format='json',
            )
        self.assertEqual(resp_gps.status_code, status.HTTP_201_CREATED)
        id_pet_gps = resp_gps.data['id_peticion']
        alerta_gps = PeticionPosibleDuplicado.objects.filter(
            peticion_menor_id=self.p_base.id_peticion,
            peticion_mayor_id=id_pet_gps,
        ).first()
        self.assertIsNotNone(alerta_gps)
        self.assertTrue(alerta_gps.coincidencia_gps)
        self.assertFalse(alerta_gps.coincidencia_texto)
        self.assertIsNotNone(alerta_gps.distancia_metros)
        self.assertLess(alerta_gps.distancia_metros, 30.0)

        # C) Petición sin similitud de texto ni proximidad GPS -> HTTP 201 y sin falsas alertas
        total_antes_no_match = PeticionPosibleDuplicado.objects.count()
        resp_no_match = client.post(
            '/api/peticiones/iniciar/',
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Solicito jornada de vacunacion en vereda lejana del norte',
                'prioridad': 'Baja',
                'direccion': 'Vereda Norte',
                'latitud': 2.6000000,
                'longitud': -76.5000000,
            },
            format='json',
        )
        self.assertEqual(resp_no_match.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), total_antes_no_match)

        # D) Ningún registro existente ni clínica fue alterado o fusionado automáticamente
        self.p_base.refresh_from_db()
        self.assertEqual(
            (
                self.p_base.id_estado_id,
                self.p_base.asignado_a_id,
                self.p_base.asignado_por_id,
                self.p_base.descripcion,
                self.p_base.foto,
            ),
            snap_base,
        )
        self.assertEqual(HistoriaClinica.objects.count(), historias_prev)
        self.assertEqual(Consulta.objects.count(), consultas_prev)
        self.assertEqual(HistorialAsignacionPeticion.objects.count(), hist_asig_prev)

        # E) Endpoints anteriores continúan operando normalmente
        client.force_authenticate(user=self.juridico)
        resp_list = client.get('/api/peticiones/duplicados/alertas/')
        self.assertEqual(resp_list.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp_list.data['count'], 2)

        resp_pet_dups = client.get(f'/api/peticiones/{self.p_base.id_peticion}/duplicados/')
        self.assertEqual(resp_pet_dups.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp_pet_dups.data['count'], 2)

        resp_manual = client.post(
            '/api/peticiones/detectar-duplicados/',
            {'id_peticion': id_pet_texto},
            format='json',
        )
        self.assertEqual(resp_manual.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_manual.data['posible_duplicado'])

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True)
    def test_idempotencia_y_respeto_confirmado_descartado_en_postgresql(self):
        client = APIClient()
        client.force_authenticate(user=self.ciudadano)

        r1 = client.post(
            '/api/peticiones/iniciar/',
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Perro herido atropellado frente al parque Caldas centro',
                'prioridad': 'ALTA',
            },
            format='json',
        )
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        p_dup_confirmada = Peticiones.objects.get(pk=r1.data['id_peticion'])

        r2 = client.post(
            '/api/peticiones/iniciar/',
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Perro atropellado frente al parque Caldas centro reporte adicional',
                'prioridad': 'Media',
            },
            format='json',
        )
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        p_dup_descartada = Peticiones.objects.get(pk=r2.data['id_peticion'])

        alerta_conf = PeticionPosibleDuplicado.objects.get(
            peticion_menor=self.p_base,
            peticion_mayor=p_dup_confirmada,
        )
        alerta_desc = PeticionPosibleDuplicado.objects.get(
            peticion_menor=self.p_base,
            peticion_mayor=p_dup_descartada,
        )

        confirmar_duplicado(
            alerta_conf.id_alerta,
            peticion_principal=self.p_base.id_peticion,
            usuario_revisor=self.juridico,
            observacion='Confirmado manualmente',
        )
        descartar_duplicado(
            alerta_desc.id_alerta,
            usuario_revisor=self.admin,
            observacion='Descartado manualmente',
        )

        notifs_prev = NotificacionJuridico.objects.filter(tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO).count()
        alertas_prev = PeticionPosibleDuplicado.objects.count()

        # Reejecución idempotente sobre ambas peticiones: no crea nuevas alertas/notificaciones
        # ni modifica estado CONFIRMADO / DESCARTADO
        res_recheck_1 = detectar_y_registrar_duplicados_al_iniciar(p_dup_confirmada, usuario_accion=self.ciudadano)
        res_recheck_2 = detectar_y_registrar_duplicados_al_iniciar(p_dup_descartada, usuario_accion=self.ciudadano)

        self.assertTrue(res_recheck_1.ejecutada)
        self.assertTrue(res_recheck_2.ejecutada)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), alertas_prev)
        self.assertEqual(
            NotificacionJuridico.objects.filter(tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO).count(),
            notifs_prev,
        )

        alerta_conf.refresh_from_db()
        self.assertEqual(alerta_conf.estado_revision, PeticionPosibleDuplicado.ESTADO_CONFIRMADO)
        self.assertEqual(alerta_conf.peticion_principal_id, self.p_base.id_peticion)
        self.assertEqual(alerta_conf.observacion_revision, 'Confirmado manualmente')

        alerta_desc.refresh_from_db()
        self.assertEqual(alerta_desc.estado_revision, PeticionPosibleDuplicado.ESTADO_DESCARTADO)
        self.assertIsNone(alerta_desc.peticion_principal_id)
        self.assertEqual(alerta_desc.observacion_revision, 'Descartado manualmente')

        # Registrar una cuarta petición similar: las resoluciones previas siguen intactas
        r3 = client.post(
            '/api/peticiones/iniciar/',
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Perro herido atropellado frente al parque Caldas centro hoy',
                'prioridad': 'ALTA',
            },
            format='json',
        )
        self.assertEqual(r3.status_code, status.HTTP_201_CREATED)
        alerta_conf.refresh_from_db()
        alerta_desc.refresh_from_db()
        self.assertEqual(alerta_conf.estado_revision, PeticionPosibleDuplicado.ESTADO_CONFIRMADO)
        self.assertEqual(alerta_desc.estado_revision, PeticionPosibleDuplicado.ESTADO_DESCARTADO)

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True)
    def test_concurrencia_iniciar_y_deteccion_simultanea_en_postgresql(self):
        barrier = threading.Barrier(2)
        codigos = []
        ids_creados = []
        errores = []
        lock = threading.Lock()

        def worker(idx):
            local_client = APIClient()
            local_client.force_authenticate(user=self.ciudadano)
            try:
                barrier.wait(timeout=5)
                resp = local_client.post(
                    '/api/peticiones/iniciar/',
                    {
                        'id_tipo': self.tipo.id_tipo,
                        'descripcion': 'Perro herido atropellado frente al parque Caldas centro reporte concurrente',
                        'prioridad': 'ALTA',
                        'direccion': 'Parque Caldas',
                        'latitud': 2.4419100,
                        'longitud': -76.6063100,
                    },
                    format='json',
                )
                with lock:
                    codigos.append(resp.status_code)
                    if resp.status_code == status.HTTP_201_CREATED:
                        ids_creados.append(resp.data['id_peticion'])
            except Exception as exc:
                with lock:
                    errores.append(exc)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(errores, [])
        self.assertEqual(codigos, [status.HTTP_201_CREATED, status.HTTP_201_CREATED])
        self.assertEqual(len(set(ids_creados)), 2)

        for pid in ids_creados:
            id_m, id_M = min(self.p_base.id_peticion, pid), max(self.p_base.id_peticion, pid)
            self.assertEqual(
                PeticionPosibleDuplicado.objects.filter(
                    peticion_menor_id=id_m,
                    peticion_mayor_id=id_M,
                ).count(),
                1,
            )
            self.assertEqual(
                NotificacionJuridico.objects.filter(
                    tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
                    mensaje__contains=f'par #{id_m}-#{id_M})',
                ).count(),
                1,
            )

        # Además validar reejecución simultánea exacta sobre el mismo par desde 2 hilos
        p_extra = Peticiones.objects.get(pk=ids_creados[0])
        b2 = threading.Barrier(2)
        err2 = []

        def worker_recheck():
            try:
                b2.wait(timeout=5)
                detectar_y_registrar_duplicados_al_iniciar(p_extra, usuario_accion=self.ciudadano)
            except Exception as exc:
                err2.append(exc)
            finally:
                connections.close_all()

        h1 = threading.Thread(target=worker_recheck)
        h2 = threading.Thread(target=worker_recheck)
        h1.start()
        h2.start()
        h1.join(timeout=10)
        h2.join(timeout=10)
        self.assertEqual(err2, [])

    @override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=True)
    def test_fallo_detector_y_permisos_no_afectan_registro_en_postgresql(self):
        client = APIClient()

        # Veterinario sigue recibiendo 403 Forbidden sin crear peticiones ni alertas
        alertas_prev = PeticionPosibleDuplicado.objects.count()
        peticiones_prev = Peticiones.objects.count()
        client.force_authenticate(user=self.vet)
        resp_vet = client.post(
            '/api/peticiones/iniciar/',
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Perro herido atropellado frente al parque Caldas centro',
                'prioridad': 'ALTA',
            },
            format='json',
        )
        self.assertEqual(resp_vet.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Peticiones.objects.count(), peticiones_prev)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), alertas_prev)

        # Simular fallo de base de datos / statement_timeout del detector: el registro DEBE completarse con 201
        client.force_authenticate(user=self.ciudadano)
        with patch(
            'peticiones.services.alertas_duplicados_service.buscar_peticiones_similares_por_descripcion',
            side_effect=OperationalError('canceling statement due to statement timeout'),
        ):
            resp_fail = client.post(
                '/api/peticiones/iniciar/',
                {
                    'id_tipo': self.tipo.id_tipo,
                    'descripcion': 'Perro herido atropellado frente al parque Caldas centro',
                    'prioridad': 'ALTA',
                },
                format='json',
            )

        self.assertEqual(resp_fail.status_code, status.HTTP_201_CREATED)
        pet_guardada = Peticiones.objects.filter(pk=resp_fail.data['id_peticion']).first()
        self.assertIsNotNone(pet_guardada)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), alertas_prev)
        self.assertTrue(
            HistorialPeticiones.objects.filter(
                id_peticion=pet_guardada,
                tipo_cambio='CREACION',
            ).exists()
        )
        self.assertTrue(
            NotificacionPeticion.objects.filter(
                id_peticion=pet_guardada,
                tipo='PETICION_URGENTE',
            ).exists()
        )

        # Rollback de transacción externa NO debe ejecutar detección automática (garantía on_commit)
        with patch(
            'peticiones.api.views.detectar_y_registrar_duplicados_al_iniciar',
        ) as mock_det_rollback:
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    client.post(
                        '/api/peticiones/iniciar/',
                        {
                            'id_tipo': self.tipo.id_tipo,
                            'descripcion': 'Perro herido atropellado frente al parque Caldas centro',
                            'prioridad': 'Media',
                        },
                        format='json',
                    )
                    raise RuntimeError('Forzar rollback externo')
            mock_det_rollback.assert_not_called()

        # Con DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=False también registra normalmente sin alertas
        with override_settings(DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA=False):
            resp_off = client.post(
                '/api/peticiones/iniciar/',
                {
                    'id_tipo': self.tipo.id_tipo,
                    'descripcion': 'Perro herido atropellado frente al parque Caldas centro',
                    'prioridad': 'Media',
                },
                format='json',
            )
        self.assertEqual(resp_off.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), alertas_prev)

    def test_default_desactivado_conserva_iniciar_y_endpoint_manual_en_postgresql(self):
        self.assertIs(settings.DUPLICADOS_DETECCION_AUTOMATICA_ACTIVA, False)
        self.assertEqual(settings.DUPLICADOS_AUTO_STATEMENT_TIMEOUT_MS, 1500)
        self.assertIsNone(settings.DUPLICADOS_AUTO_MAX_CANDIDATOS)

        alertas_prev = PeticionPosibleDuplicado.objects.count()
        notifs_prev = NotificacionJuridico.objects.filter(
            tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
        ).count()

        client = APIClient()
        client.force_authenticate(user=self.ciudadano)
        resp_iniciar = client.post(
            '/api/peticiones/iniciar/',
            {
                'id_tipo': self.tipo.id_tipo,
                'descripcion': 'Perro herido atropellado frente al parque Caldas centro',
                'prioridad': 'Media',
            },
            format='json',
        )
        self.assertEqual(resp_iniciar.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('posibles_duplicados', resp_iniciar.data)
        self.assertEqual(
            set(resp_iniciar.data.keys()),
            {
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
            },
        )
        self.assertEqual(PeticionPosibleDuplicado.objects.count(), alertas_prev)
        self.assertEqual(
            NotificacionJuridico.objects.filter(
                tipo=TIPO_NOTIFICACION_POSIBLE_DUPLICADO,
            ).count(),
            notifs_prev,
        )

        # El endpoint manual de detección sigue funcionando normalmente aunque la automática esté en False
        client.force_authenticate(user=self.juridico)
        resp_manual = client.post(
            '/api/peticiones/detectar-duplicados/',
            {'id_peticion': resp_iniciar.data['id_peticion']},
            format='json',
        )
        self.assertEqual(resp_manual.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_manual.data['posible_duplicado'])
        self.assertGreaterEqual(resp_manual.data['total_candidatos'], 1)
        ids_candidatos = {c['id_peticion'] for c in resp_manual.data['posibles_duplicados']}
        self.assertIn(self.p_base.id_peticion, ids_candidatos)



