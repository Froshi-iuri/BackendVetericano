"""Ejecutar con vetericano.test_settings y TRIAJE_PRUEBA_POSTGRES=1."""
import os
import threading
import unittest
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from django.db import connection, connections
from django.test import override_settings
from rest_framework.test import APIClient

from peticiones.models import HistorialAsignacionPeticion, NotificacionPeticion, PeticionPosibleDuplicado
from peticiones.services.alertas_duplicados_service import descartar_duplicado
from triaje_ia.services import registrar_resultado_triaje
from triaje_ia.tests import _BaseTriajeSqliteTestCase, _MODELOS_AISLADOS


@unittest.skipUnless(os.getenv('TRIAJE_PRUEBA_POSTGRES') == '1', 'Requiere PostgreSQL local de pruebas')
@override_settings(TRIAJE_ASIGNACION_AUTOMATICA=True, TRIAJE_MAX_CASOS_ACTIVOS_VETERINARIO=1)
class AsignacionPostgresTests(_BaseTriajeSqliteTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertIn(connection.settings_dict['HOST'], ('127.0.0.1', 'localhost'))
        self.assertEqual(connection.settings_dict['NAME'], 'test_vetericano')
        self.schema = 'triaje_test_' + uuid.uuid4().hex
        with connection.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + self.schema)
        self.addCleanup(self.limpiar)
        self.usar_schema()
        with connection.schema_editor() as editor:
            for modelo in _MODELOS_AISLADOS:
                editor.create_model(modelo)
        self._crear_datos_base()

    def usar_schema(self):
        with connection.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema)
            cur.execute("SET lock_timeout TO '8s'")

    def limpiar(self):
        with connection.cursor() as cur:
            cur.execute('RESET search_path')
            cur.execute('DROP SCHEMA ' + self.schema + ' CASCADE')
        connection.close()

    def completar(self, pk, candidatos=None):
        return registrar_resultado_triaje(pk, {
            'posibles_duplicados': candidatos or [], 'prioridad_sugerida': 'MEDIA',
            'resultado_raw': {'duplicados_meta': {'texto_activo': True}},
        })

    def test_ráfaga_con_reintentos_y_cupo(self):
        casos = [self._crear_peticion() for _ in range(4)]
        ids = [p.pk for p in casos] * 2
        barrera = threading.Barrier(len(ids))
        def enviar(pk):
            try:
                self.usar_schema()
                barrera.wait(timeout=10)
                self.completar(pk)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(ids)) as pool:
            list(pool.map(enviar, ids))
        for p in casos:
            p.refresh_from_db()
            asignado = p.asignado_a_id is not None
            self.assertEqual(HistorialAsignacionPeticion.objects.filter(id_peticion=p).count(), int(asignado))
            aviso = NotificacionPeticion.objects.get(id_peticion=p)
            self.assertEqual(aviso.tipo, 'ASIGNACION' if asignado else 'SIN_VETERINARIO')
            self.assertEqual(aviso.id_usuario_destino_id, p.asignado_a_id or self.juridico.pk)
        self.assertEqual(Counter(p.asignado_a_id for p in casos), {None: 2, self.vet_1.pk: 1, self.vet_2.pk: 1})

    def test_descartar_ultima_alerta_desbloquea(self):
        a, b, c = [self._crear_peticion() for _ in range(3)]
        alertas = [PeticionPosibleDuplicado.objects.create(peticion_menor=a, peticion_mayor=p) for p in (b, c)]
        self.completar(a.pk, [{'id_peticion': b.pk}, {'id_peticion': c.pk}])
        for alerta, esperado in zip(alertas, (None, self.vet_1.pk)):
            descartar_duplicado(alerta.pk, usuario_revisor=self.juridico, observacion='Casos independientes')
            a.refresh_from_db()
            self.assertEqual(a.asignado_a_id, esperado)
        self.assertEqual(NotificacionPeticion.objects.filter(id_peticion=a, tipo='ASIGNACION').count(), 1)

    def test_asignacion_manual_persiste_tras_callback(self):
        p = self._crear_peticion()
        cliente = APIClient()
        cliente.force_authenticate(user=self.juridico)
        with override_settings(TRIAJE_ASIGNACION_AUTOMATICA=False):
            resp = cliente.patch(f'/api/peticiones/{p.pk}/asignar/', {'asignado_a': self.vet_2.pk, 'motivo': 'Manual'}, format='json')
            self.assertEqual(resp.status_code, 200, resp.data)
        self.completar(p.pk)
        p.refresh_from_db()
        self.assertEqual(p.asignado_a_id, self.vet_2.pk)
        self.assertEqual(NotificacionPeticion.objects.filter(id_peticion=p, tipo='ASIGNACION').count(), 1)
