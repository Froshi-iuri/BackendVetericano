"""
Pruebas aisladas para el módulo de Exámenes Clínicos (serializers, filtros GET y carga masiva).
Se ejecutan contra la base de datos local de pruebas (test_vetericano) con rollback automático.
"""

from unittest.mock import MagicMock
from django.db import transaction
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from examenes_clinicos.api.serializers import ExamenSerializer
from examenes_clinicos.api.views import ExamenViewSet
from examenes_clinicos.models import Examen
from users.constants import ROL_ADMINISTRADOR, ROL_VETERINARIO


def _dummy_user(rol_id, uid=1):
    user = MagicMock()
    user.is_authenticated = True
    user.activo = True
    user.id_usuario = uid
    user.pk = uid
    user.id_rol_id = rol_id
    user.id_rol = MagicMock(id_rol=rol_id)
    return user


class ExamenClinicoTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = _dummy_user(ROL_ADMINISTRADOR, uid=1)
        self.vet = _dummy_user(ROL_VETERINARIO, uid=2)

    def test_serializer_create_and_update_no_aborts_postgres_transaction(self):
        """Verifica que create/update en ExamenSerializer no ejecute DDL que aborte la transacción."""
        with transaction.atomic():
            ser = ExamenSerializer(data={
                'nombre_tipo': 'Hemograma Prueba',
                'descripcion': 'Conteo celular completo',
                'ruta_archivo_resultado': 'Laboratorio Hematológico',
                'estado': 'Activo',
            })
            self.assertTrue(ser.is_valid(), ser.errors)
            instancia = ser.save()
            self.assertEqual(instancia.estado, 'activo')

            ser_upd = ExamenSerializer(
                instancia,
                data={'estado': 'INACTIVO', 'descripcion': 'Actualizado'},
                partial=True,
            )
            self.assertTrue(ser_upd.is_valid(), ser_upd.errors)
            actualizada = ser_upd.save()
            self.assertEqual(actualizada.estado, 'inactivo')
            self.assertEqual(ExamenSerializer(actualizada).data['estado'], 'inactivo')

    def test_get_filters_search_estado_y_solo_catalogo(self):
        Examen.objects.create(
            nombre_tipo_examen='Prueba Bioquímica Test',
            descripcion_hallazgos='Perfil hepático',
            ruta_archivo_resultado='Química Sanguínea',
            estado='activo',
        )
        Examen.objects.create(
            nombre_tipo_examen='Examen Inactivo Test',
            descripcion_hallazgos='Oculto',
            ruta_archivo_resultado='Dermatología',
            estado='inactivo',
        )

        view = ExamenViewSet.as_view({'get': 'list'})
        req = self.factory.get('/api/examenes-clinicos/examenes/', {'search': 'Bioquímica', 'estado': 'activo', 'solo_catalogo': 'true'})
        force_authenticate(req, user=self.vet)
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        nombres = [item['nombre_tipo_examen'] for item in resp.data]
        self.assertEqual(nombres, ['Prueba Bioquímica Test'])

    def test_carga_masiva_soporta_duplicado_previo_preserva_datos_y_valida_errores(self):
        # Simular escenario real de Supabase donde ya existen dos filas con el mismo nombre
        Examen.objects.create(
            nombre_tipo_examen='Test Parvovirus',
            descripcion_hallazgos='Descripción original preservada',
            ruta_archivo_resultado='Inmunodiagnóstico Rápido',
            estado='activo',
        )
        Examen.objects.create(
            nombre_tipo_examen='Test Parvovirus',
            descripcion_hallazgos=None,
            ruta_archivo_resultado='Cardiología',
            estado='activo',
        )

        view = ExamenViewSet.as_view({'post': 'carga_masiva'})
        payload = [
            {'nombre': '  test   parvovirus  ', 'descripcion': '', 'tipo': ''},
            {'nombre': 'Uroanálisis Test', 'descripcion': 'Análisis físico-químico', 'tipo': 'Urianálisis', 'estado': 'activo'},
            {'nombre': ''},
            {'nombre': 'X' * 105},
            'fila_invalida',
        ]
        req = self.factory.post('/api/examenes-clinicos/examenes/carga-masiva/', payload, format='json')
        force_authenticate(req, user=self.admin)
        resp = view(req)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['actualizados_count'], 1)
        self.assertEqual(resp.data['creados_count'], 1)
        self.assertEqual(len(resp.data['errores']), 3)

        primero = Examen.objects.filter(nombre_tipo_examen='Test Parvovirus').order_by('id_examen').first()
        self.assertEqual(primero.descripcion_hallazgos, 'Descripción original preservada')
        self.assertEqual(primero.ruta_archivo_resultado, 'Inmunodiagnóstico Rápido')
        self.assertEqual(Examen.objects.filter(nombre_tipo_examen='Uroanálisis Test').count(), 1)
