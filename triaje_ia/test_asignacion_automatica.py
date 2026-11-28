"""Cobertura del cierre de triaje en una base efímera."""
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import connections
from django.test import override_settings

from peticiones.models import HistorialAsignacionPeticion, NotificacionPeticion
from peticiones.utils import calcular_tiempos_peticion
from triaje_ia.models import TriajePeticion
from triaje_ia.services import ejecutar_triaje_local_privado, registrar_resultado_triaje
from triaje_ia.tests import _BaseTriajeSqliteTestCase, _crear_conexion_sqlite


@override_settings(TRIAJE_ASIGNACION_AUTOMATICA=True, TRIAJE_MAX_CASOS_ACTIVOS_VETERINARIO=1)
class AsignacionTests(_BaseTriajeSqliteTestCase):
    def completar(self, peticion, **datos):
        return registrar_resultado_triaje(peticion.pk, {
            'prioridad_sugerida': 'MEDIA', 'posibles_duplicados': [],
            'resultado_raw': {'duplicados_meta': {'usar_dinov2': True}}, **datos,
        })

    def test_carga_empate_capacidad_notificacion_y_reintento(self):
        self._crear_peticion(asignado_a=self.vet_1, estado=self.estado_fin)
        a, b, c = [self._crear_peticion() for _ in range(3)]
        for p in (a, a, b, c):
            self.completar(p)
            p.refresh_from_db()
        self.assertEqual([a.asignado_a_id, b.asignado_a_id, c.asignado_a_id],
                         [self.vet_1.pk, self.vet_2.pk, None])
        self.assertIsNone(a.prioridad)
        self.assertTrue(calcular_tiempos_peticion(a)['puede_reasignar'])
        self.assertEqual(HistorialAsignacionPeticion.objects.count(), 2)
        self.assertEqual(NotificacionPeticion.objects.filter(tipo='ASIGNACION').count(), 2)
        self.assertTrue(NotificacionPeticion.objects.filter(id_peticion=c, id_usuario_destino=self.juridico).exists())

    def test_rotacion_tras_finalizar(self):
        a, b = self._crear_peticion(), self._crear_peticion()
        self.completar(a)
        a.id_estado = self.estado_fin
        a.save(update_fields=['id_estado'])
        self.completar(b)
        b.refresh_from_db()
        self.assertEqual(b.asignado_a_id, self.vet_2.pk)

    def test_sede_e_inactivos(self):
        self.vet_1.activo = False
        self.vet_1.save()
        self.vet_2.id_sede = self.sede_secundaria
        self.vet_2.save()
        p = self._crear_peticion()
        self.completar(p)
        p.refresh_from_db()
        self.assertIsNone(p.asignado_a_id)
        self.assertTrue(NotificacionPeticion.objects.filter(id_peticion=p, tipo='SIN_VETERINARIO').exists())

    def test_duplicado_o_detector_sin_resultado(self):
        for datos in ({'posibles_duplicados': [{'id_peticion': 9}]}, {'resultado_raw': {}}):
            p = self._crear_peticion()
            self.completar(p, **datos)
            p.refresh_from_db()
            self.assertIsNone(p.asignado_a_id)

    @override_settings(TRIAJE_ASIGNACION_AUTOMATICA=False)
    def test_manual(self):
        p = self._crear_peticion()
        self.completar(p)
        self.assertFalse(HistorialAsignacionPeticion.objects.exists())

    @patch('triaje_ia.services.ejecutar_deteccion_duplicados_triaje', return_value={'posibles_duplicados': []})
    def test_respaldo_local(self, detector):
        p = self._crear_peticion()
        t = TriajePeticion.objects.create(peticion=p, clave_idempotencia='local')
        ejecutar_triaje_local_privado(p, t, motivos_privacidad=['prueba'])
        p.refresh_from_db()
        self.assertEqual(p.asignado_a_id, self.vet_1.pk)
        self.assertIsNone(p.prioridad)

    def test_concurrencia(self):
        a, b = self._crear_peticion(), self._crear_peticion()
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, 'asignaciones.sqlite3')
            principal = _crear_conexion_sqlite(file_uri=ruta)
            try:
                self._sqlite_conn.connection.backup(principal.connection)
                inicio = threading.Barrier(3)
                def asignar(p):
                    conn = _crear_conexion_sqlite(file_uri=ruta, transaction_mode='IMMEDIATE')
                    connections._connections.default = conn
                    try:
                        inicio.wait(timeout=10)
                        self.completar(p)
                        p.refresh_from_db()
                        return p.asignado_a_id
                    finally:
                        conn.close()
                with ThreadPoolExecutor(max_workers=3) as pool:
                    ids = list(pool.map(asignar, (a, b, type(a)(pk=a.pk))))
                self.assertEqual(ids[0], ids[2])
                self.assertEqual(set(ids), {self.vet_1.pk, self.vet_2.pk})
                with principal.cursor() as cur:
                    cur.execute('SELECT COUNT(*) FROM ' + HistorialAsignacionPeticion._meta.db_table)
                    self.assertEqual(cur.fetchone()[0], 2)
            finally:
                principal.close()
