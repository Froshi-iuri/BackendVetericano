"""
Pruebas unitarias y de integración para el módulo de Apoyo Diagnóstico IA Local.
"""

from unittest.mock import MagicMock
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from users.constants import (
    ROL_ADMINISTRADOR,
    ROL_VETERINARIO,
    ROL_PETICIONARIO,
    ROL_JURIDICO,
)
from users.models import Usuarios
from diagnostico_ia.ml.engine import obtener_motor_diagnostico
from diagnostico_ia.views import DiagnosticoIAPredictView, DiagnosticoIAInfoView


class DummyUser:
    """Usuario simulado para pruebas de permisos sin requerir base de datos activa."""
    _meta = Usuarios._meta

    def __init__(self, id_usuario, rol_id, activo=True, email="vet@cba.gov.co"):
        self.id_usuario = id_usuario
        self.pk = id_usuario
        self.id_rol_id = rol_id
        self.id_rol = MagicMock(id_rol=rol_id)
        self.activo = activo
        self.is_active = activo
        self.email = email
        self.nombre = "Dr. Veterinario"
        self.apellido = "Prueba"

    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False


class TestDiagnosticoIAMotor(SimpleTestCase):
    """Pruebas del motor algorítmico y reglas de inferencia clínica."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.motor = obtener_motor_diagnostico()

    def test_normalizacion_entradas_y_alias(self):
        """Verifica que sinónimos y variaciones textuales se mapeen a términos canónicos."""
        norm = self.motor.normalizar_entradas(
            especie_raw="perro",
            edad_etapa_raw="cachorrito de 3 meses",
            signos_raw=["vómito", "heces con sangre", "fiebre"],
            tests_raw={"Test Parvovirosis": "positivo"},
            examenes_raw={"Hemograma completo": {"estado": "alterado", "hallazgos": "leucopenia severa"}}
        )

        self.assertEqual(norm['especie'], 'canino')
        self.assertEqual(norm['etapa'], 'cachorro_gatito')
        self.assertIn('vomito', norm['signos'])
        self.assertIn('diarrea_hemorragica', norm['signos'])
        self.assertIn('fiebre', norm['signos'])
        self.assertEqual(norm['tests_rapidos'].get('test_parvovirus'), 'positivo')
        self.assertEqual(norm['examenes_clinicos'].get('cuadro_hematico', {}).get('estado'), 'alterado')

    def test_inferencia_canino_parvovirus(self):
        """Caso clásico canino: cachorro con vómito, diarrea sanguinolenta y test rápido positivo."""
        res = self.motor.predecir(
            especie='canino',
            edad_etapa='cachorro',
            signos=['vomito', 'diarrea con sangre', 'fiebre', 'letargia'],
            tests_rapidos={'Test Parvovirus': 'positivo'},
            examenes_clinicos={'Cuadro Hemático': {'estado': 'alterado', 'hallazgos': 'leucopenia marcada'}}
        )

        # 1. Avisos y estructura
        self.assertIn('aviso_legal', res)
        self.assertTrue(len(res['diagnosticos_sugeridos']) >= 3)

        # 2. Top 1 debe ser Parvovirosis Canina
        top_diag = res['diagnosticos_sugeridos'][0]
        self.assertEqual(top_diag['nombre'], 'Parvovirosis Canina (CPV-2)')
        self.assertTrue(top_diag['probabilidad'] > 0.70)
        self.assertIn('Test Parvovirus (positivo)', top_diag['justificacion'])

        # 3. No debe sugerir patologías exclusivas de felinos
        nombres_sugeridos = [d['nombre'] for d in res['diagnosticos_sugeridos']]
        self.assertNotIn('Panleucopenia Felina (FPLV)', nombres_sugeridos)
        self.assertNotIn('Leucemia Viral Felina (FeLV)', nombres_sugeridos)
        self.assertNotIn('Inmunodeficiencia Felina (FIV)', nombres_sugeridos)

        # 4. Exámenes recomendados no deben incluir Test Parvovirus ni Cuadro Hemático (ya realizados)
        recs = [r['examen'] for r in res['examenes_recomendados']]
        self.assertNotIn('Test Parvovirus', recs)
        self.assertNotIn('Cuadro Hemático', recs)

    def test_inferencia_canino_distemper(self):
        """Caso canino con secreción oculonasal, tos, fiebre y test moquillo positivo."""
        res = self.motor.predecir(
            especie='perro',
            edad_etapa='joven',
            signos=['tos', 'secrecion nasal', 'secrecion ocular', 'fiebre', 'temblores'],
            tests_rapidos={'Test de Distemper (Moquillo)': 'positivo'}
        )

        top_diag = res['diagnosticos_sugeridos'][0]
        self.assertEqual(top_diag['nombre'], 'Moquillo Canino (Distémper)')
        self.assertTrue(top_diag['probabilidad'] > 0.80)

    def test_inferencia_felino_felv(self):
        """Caso felino: anemia, anorexia, pérdida de peso y test FeLV positivo."""
        res = self.motor.predecir(
            especie='gato',
            edad_etapa='adulto',
            signos=['perdida de peso', 'anorexia', 'letargia', 'ganglios inflamados'],
            tests_rapidos={'Test de Leucemia (Felinos)': 'positivo'},
            examenes_clinicos={'Cuadro Hemático': {'estado': 'alterado', 'hallazgos': 'anemia no regenerativa'}}
        )

        top_diag = res['diagnosticos_sugeridos'][0]
        self.assertEqual(top_diag['nombre'], 'Leucemia Viral Felina (FeLV)')
        self.assertTrue(top_diag['probabilidad'] > 0.75)

        # No debe sugerir patologías caninas
        nombres_sugeridos = [d['nombre'] for d in res['diagnosticos_sugeridos']]
        self.assertNotIn('Parvovirosis Canina (CPV-2)', nombres_sugeridos)
        self.assertNotIn('Moquillo Canino (Distémper)', nombres_sugeridos)

    def test_inferencia_felino_asma(self):
        """Caso felino con dificultad respiratoria y rayos X torácico alterado."""
        res = self.motor.predecir(
            especie='felino',
            edad_etapa='adulto',
            signos=['tos', 'disnea', 'dificultad respiratoria'],
            examenes_clinicos={'Rayos X': {'estado': 'alterado', 'hallazgos': 'patron bronquial difuso'}}
        )

        nombres_sugeridos = [d['nombre'] for d in res['diagnosticos_sugeridos']]
        self.assertIn('Asma Felina (Bronquitis alérgica)', nombres_sugeridos)

    def test_descarte_por_test_rapido_negativo(self):
        """Cuando un test rápido es negativo, la patología asociada no debe ser el diagnóstico primario."""
        res = self.motor.predecir(
            especie='canino',
            edad_etapa='cachorro',
            signos=['vomito', 'diarrea', 'letargia'],
            tests_rapidos={'Test Parvovirus': 'negativo'}
        )

        top_diag = res['diagnosticos_sugeridos'][0]
        # Con test negativo, Parvovirosis no debe liderar
        self.assertNotEqual(top_diag['nombre'], 'Parvovirosis Canina (CPV-2)')

    def test_priorizacion_y_resumen_clinico(self):
        """Verifica que el resumen clínico explique coherentemente la sospecha."""
        res = self.motor.predecir(
            especie='perro',
            edad_etapa='adulto',
            signos=['prurito', 'alopecia', 'costras'],
            examenes_clinicos={'Frotis (raspado piel)': {'estado': 'positivo', 'hallazgos': 'acaros Sarcoptes'}}
        )

        self.assertIn('resumen_clinico', res)
        self.assertTrue(len(res['resumen_clinico']) > 50)
        self.assertEqual(res['diagnosticos_sugeridos'][0]['nombre'], 'Sarna Sarcóptica (Sarcoptes scabiei)')

    def test_inferencia_canino_dvg(self):
        """Caso agudo: perro grande con distensión abdominal, arcadas improductivas y timpanismo."""
        res = self.motor.predecir(
            especie='canino',
            edad_etapa='adulto',
            signos=['distension abdominal', 'dolor abdominal', 'vomito', 'letargia'],
            examenes_clinicos={'Rayos X': {'estado': 'alterado', 'hallazgos': 'timpanismo gástrico con signo de doble burbuja'}}
        )

        nombres_sugeridos = [d['nombre'] for d in res['diagnosticos_sugeridos']]
        self.assertIn('Dilatación Vólvulo Gástrico (DVG)', nombres_sugeridos)

    def test_inferencia_felino_cif(self):
        """Caso felino urinario: estranguria, hematuria y parcial de orina alterado."""
        res = self.motor.predecir(
            especie='felino',
            edad_etapa='joven',
            signos=['disuria', 'hematuria', 'dolor abdominal'],
            examenes_clinicos={'Parcial de Orina': {'estado': 'alterado', 'hallazgos': 'hematuria microscopica'}}
        )

        nombres_sugeridos = [d['nombre'] for d in res['diagnosticos_sugeridos']]
        self.assertIn('Cistitis Idiopática Felina (CIF)', nombres_sugeridos)

    def test_top_k_configurable(self):
        """Verifica que el parámetro top_k controle adecuadamente la cantidad de sugerencias (3 a 5)."""
        res_3 = self.motor.predecir(especie='perro', signos=['vomito'], top_k=3)
        self.assertEqual(len(res_3['diagnosticos_sugeridos']), 3)

        res_5 = self.motor.predecir(especie='perro', signos=['vomito'], top_k=5)
        self.assertEqual(len(res_5['diagnosticos_sugeridos']), 5)



class TestDiagnosticoIAAPI(SimpleTestCase):
    """Pruebas de la API REST DRF (permisos, respuestas HTTP y validaciones)."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.vet_user = DummyUser(id_usuario=10, rol_id=ROL_VETERINARIO)
        self.admin_user = DummyUser(id_usuario=1, rol_id=ROL_ADMINISTRADOR)
        self.peticionario_user = DummyUser(id_usuario=50, rol_id=ROL_PETICIONARIO)
        self.juridico_user = DummyUser(id_usuario=20, rol_id=ROL_JURIDICO)

    def test_post_sugerir_veterinario_ok(self):
        """Un usuario con rol Veterinario puede consultar el endpoint de apoyo diagnóstico."""
        url = '/api/diagnostico-ia/sugerir/'
        payload = {
            "especie": "perro",
            "edad_etapa": "cachorro",
            "signos": ["vomito", "diarrea", "fiebre"],
            "tests_rapidos": {
                "Test Parvovirus": "positivo"
            },
            "examenes_clinicos": {
                "Cuadro Hemático": "alterado"
            },
            "top_k": 3
        }

        request = self.factory.post(url, payload, format='json')
        force_authenticate(request, user=self.vet_user)
        view = DiagnosticoIAPredictView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertIn('diagnosticos_sugeridos', data)
        self.assertEqual(len(data['diagnosticos_sugeridos']), 3)
        self.assertIn('examenes_recomendados', data)
        self.assertIn('resumen_clinico', data)
        self.assertIn('aviso_legal', data)
        self.assertEqual(data['modelo_info']['total_patologias_evaluadas'], 84)

    def test_post_sugerir_admin_ok(self):
        """Un Administrador tiene acceso clínico y puede consultar el endpoint."""
        url = '/api/diagnostico-ia/predecir/'
        payload = {
            "especie": "gato",
            "edad_etapa": "senior",
            "signos": ["perdida_peso", "vomito", "poliuria"],
            "examenes_clinicos": {
                "Química Sanguínea": {"estado": "alterado", "hallazgos": "creatinina alta"}
            }
        }

        request = self.factory.post(url, payload, format='json')
        force_authenticate(request, user=self.admin_user)
        view = DiagnosticoIAPredictView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data['diagnosticos_sugeridos']) >= 3)

    def test_post_sugerir_peticionario_denegado(self):
        """Un usuario Peticionario tiene denegado el acceso al módulo de diagnóstico clínico."""
        url = '/api/diagnostico-ia/sugerir/'
        payload = {"especie": "perro", "signos": ["tos"]}

        request = self.factory.post(url, payload, format='json')
        force_authenticate(request, user=self.peticionario_user)
        view = DiagnosticoIAPredictView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_sugerir_juridico_denegado(self):
        """Un usuario Jurídico no tiene permiso para atención clínica diagnóstica."""
        url = '/api/diagnostico-ia/sugerir/'
        payload = {"especie": "perro", "signos": ["vomito"]}

        request = self.factory.post(url, payload, format='json')
        force_authenticate(request, user=self.juridico_user)
        view = DiagnosticoIAPredictView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_sugerir_validacion_especie_requerida(self):
        """Si falta la especie, debe retornar error 400 Bad Request."""
        url = '/api/diagnostico-ia/sugerir/'
        payload = {"signos": ["vomito"]}  # Falta 'especie'

        request = self.factory.post(url, payload, format='json')
        force_authenticate(request, user=self.vet_user)
        view = DiagnosticoIAPredictView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('errores', response.data)
        self.assertIn('especie', response.data['errores'])

    def test_get_info_catalogo(self):
        """Cualquier usuario autenticado puede consultar los metadatos y catálogo del modelo."""
        url = '/api/diagnostico-ia/info/'
        request = self.factory.get(url)
        force_authenticate(request, user=self.vet_user)
        view = DiagnosticoIAInfoView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertIn('modelo_info', data)
        self.assertIn('tests_rapidos_soportados', data)
        self.assertIn('examenes_clinicos_soportados', data)
        self.assertIn('patologias_catalogo', data)
        self.assertEqual(data['total_patologias_evaluadas'], 84)
        self.assertEqual(len(data['patologias_catalogo']), 84)
