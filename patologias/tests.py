"""
Pruebas aisladas para el módulo de Patologías (filtros GET, compatibilidad con IA y carga masiva).
No escribe ni depende de Supabase.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from diagnostico_ia.ml.knowledge_base import PATOLOGIAS_KNOWLEDGE
from patologias.api.views import PatologiaViewSet, _normalizar_especie_filtro, _nombres_por_especie_ia
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


class PatologiaCompatibilidadYFiltrosTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = _dummy_user(ROL_ADMINISTRADOR, uid=1)
        self.vet = _dummy_user(ROL_VETERINARIO, uid=2)

    def test_catalogo_ia_cubre_84_patologias_con_tipo_y_especie(self):
        self.assertEqual(len(PATOLOGIAS_KNOWLEDGE), 84)
        for nombre, meta in PATOLOGIAS_KNOWLEDGE.items():
            self.assertTrue(meta.get('id_patologia'), f"Falta id_patologia en {nombre}")
            self.assertTrue(meta.get('tipo'), f"Falta tipo en {nombre}")
            self.assertIn(meta.get('especie'), ('canino', 'felino', 'ambos'))

    def test_normalizacion_especie_y_nombres_ia(self):
        self.assertEqual(_normalizar_especie_filtro('perro'), 'canino')
        self.assertEqual(_normalizar_especie_filtro('Gato'), 'felino')
        caninos = _nombres_por_especie_ia('canino')
        felinos = _nombres_por_especie_ia('felino')
        self.assertEqual(len(caninos), 68)
        self.assertEqual(len(felinos), 50)
        self.assertIn('Parvovirosis Canina (CPV-2)', caninos)
        self.assertNotIn('Panleucopenia Felina (FPLV)', caninos)
        self.assertIn('Panleucopenia Felina (FPLV)', felinos)
        self.assertNotIn('Parvovirosis Canina (CPV-2)', felinos)

    def test_get_queryset_aplica_filtros_clasificacion_activo_search_y_especie(self):
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs

        viewset = PatologiaViewSet()
        req = self.factory.get('/api/patologias/patologias/', {
            'tipo_de_enfermedad': 'Infecciosa',
            'activo': 'true',
            'search': 'Parvo',
            'especie': 'canino',
        })
        drf_req = MagicMock(query_params=req.GET)
        viewset.request = drf_req

        with patch.object(PatologiaViewSet, 'queryset', mock_qs):
            viewset.get_queryset()

        # Verificar que se aplicaron los 4 filtros sin romper cuando no hay BD
        self.assertEqual(mock_qs.filter.call_count, 4)

    def test_carga_masiva_evita_duplicados_y_no_sobrescribe_campos_vacios(self):
        existente = SimpleNamespace(
            id_patologia=9,
            nombre='Parvovirosis Canina (CPV-2)',
            descripcion='Descripción clínica existente',
            tipo_de_enfermedad='Infecciosa',
            activo=True,
            save=MagicMock(),
        )
        qs_mock = MagicMock()
        qs_mock.order_by.return_value.first.side_effect = [existente, None]

        nuevo = SimpleNamespace(nombre='Nueva Patología Prueba')
        view = PatologiaViewSet.as_view({'post': 'carga_masiva'})
        payload = [
            {'Nombre': '  parvovirosis   canina (cpv-2)  ', 'descripcion': '', 'tipo_de_enfermedad': '', 'activo': None},
            {'nombre': 'Nueva Patología Prueba', 'descripcion': 'Detalle', 'tipo_de_enfermedad': 'Digestiva'},
            {'nombre': ''},
            {'nombre': 'A' * 151},
            {'nombre': 'Estado Malo', 'activo': 'quizas'},
            123,
        ]
        req = self.factory.post('/api/patologias/patologias/carga-masiva/', payload, format='json')
        force_authenticate(req, user=self.admin)

        with patch('patologias.api.views.transaction.atomic'), \
             patch('patologias.api.views.Patologia.objects.filter', return_value=qs_mock), \
             patch('patologias.api.views.Patologia.objects.create', return_value=nuevo) as create_mock:
            resp = view(req)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['actualizados_count'], 1)
        self.assertEqual(resp.data['creados_count'], 1)
        self.assertEqual(len(resp.data['errores']), 4)
        # No debe borrar descripcion, tipo_de_enfermedad ni activo existentes
        self.assertEqual(existente.descripcion, 'Descripción clínica existente')
        self.assertEqual(existente.tipo_de_enfermedad, 'Infecciosa')
        self.assertTrue(existente.activo)
        existente.save.assert_not_called()
        create_mock.assert_called_once()
