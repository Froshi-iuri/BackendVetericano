import json
import base64
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from peticiones.api.views import FormularioActaSchemaView, DictadoVozActaView
from peticiones.services.acta_schema import FORM_ID_ACTA, FORM_VERSION_ACTA, obtener_schema_formulario_acta
from peticiones.services.gemini_dictado_service import procesar_audio_con_gemini


class MockUser:
    """Usuario simulado para pruebas de permisos."""
    def __init__(self, id_usuario=1, rol_id=3, nombre="Vet", apellido="Test", email="vet@cba.gov.co"):
        self.id_usuario = id_usuario
        self.id_rol_id = rol_id
        self.id_rol = MagicMock(id_rol=rol_id)
        self.nombre = nombre
        self.apellido = apellido
        self.email = email
        self.activo = True
        self.is_authenticated = True
        self.pk = id_usuario


class FormularioActaSchemaTestCase(SimpleTestCase):
    """Pruebas unitarias para el endpoint de esquema dinámico del formulario."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = FormularioActaSchemaView.as_view()

    def test_schema_requiere_autenticacion(self):
        request = self.factory.get('/api/peticiones/seguimiento/formulario-schema/')
        request.user = MagicMock(is_authenticated=False)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_schema_retorna_estructura_completa_veterinario(self):
        request = self.factory.get('/api/peticiones/seguimiento/formulario-schema/')
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual(data["form_id"], FORM_ID_ACTA)
        self.assertEqual(data["form_version"], FORM_VERSION_ACTA)
        self.assertIn("sections", data)
        self.assertGreaterEqual(len(data["sections"]), 4)

        # Verificar secciones esperadas
        section_ids = [s["id"] for s in data["sections"]]
        self.assertIn("sec_datos_generales", section_ids)
        self.assertIn("sec_pacientes_clinica", section_ids)
        self.assertIn("sec_pruebas_cierre", section_ids)
        self.assertIn("sec_rescate_cba", section_ids)


class DictadoVozActaViewTestCase(SimpleTestCase):
    """Pruebas unitarias para el endpoint de dictado por voz y extracción con Gemini AI."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DictadoVozActaView.as_view()

    def test_dictado_denegado_para_rol_no_veterinario(self):
        request = self.factory.post('/api/peticiones/seguimiento/dictado-voz/')
        request.user = MockUser(id_usuario=20, rol_id=4)  # Peticionario
        force_authenticate(request, user=request.user)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_dictado_error_si_no_se_envia_audio(self):
        request = self.factory.post('/api/peticiones/seguimiento/dictado-voz/', {}, format='json')
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_dictado_audio_base64_sin_api_key_retorna_aviso_controlado(self):
        audio_dummy = base64.b64encode(b"FAKE_AUDIO_DATA_FOR_TESTING_PURPOSES_1234567890").decode('utf-8')
        request = self.factory.post(
            '/api/peticiones/seguimiento/dictado-voz/',
            {'audio': audio_dummy},
            format='json'
        )
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)

        with patch('peticiones.services.gemini_dictado_service.obtener_gemini_api_key', return_value=""):
            response = self.view(request)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data.get("status"), "error")
            self.assertTrue(response.data.get("requires_review"))
            self.assertIn("GEMINI_API_KEY", response.data.get("message", ""))

    @patch('peticiones.services.gemini_dictado_service.requests.post')
    def test_dictado_audio_multipart_exito_con_gemini(self, mock_requests_post):
        mock_gemini_resp = MagicMock()
        mock_gemini_resp.status_code = 200
        mock_gemini_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "form_id": FORM_ID_ACTA,
                                    "form_version": 1,
                                    "status": "draft",
                                    "extracted_summary": "Atención a canino macho con gastroenteritis en El Ejido.",
                                    "fields": {
                                        "nombre_paciente": {"value": "Rocky", "confidence": 0.95, "source": "voice"},
                                        "paciente_especie": {"value": "Canino", "confidence": 0.98, "source": "voice"},
                                        "paciente_sexo": {"value": "Macho", "confidence": 0.92, "source": "voice"},
                                        "propietario_nombre": {"value": "Juan Pérez", "confidence": 0.9, "source": "voice"},
                                        "anamnesis_descripcion_queja": {"value": "Vómito y diarrea desde hace 2 días.", "confidence": 0.96, "source": "voice"},
                                        "tratamiento_realizado": {"value": "Hidratación y antibiótico.", "confidence": 0.88, "source": "voice"},
                                        "sw_digestivo_vomito": {"value": True, "confidence": 0.95, "source": "voice"},
                                        "sw_digestivo_diarrea": {"value": True, "confidence": 0.95, "source": "voice"}
                                    },
                                    "warnings": [],
                                    "requires_review": True
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_requests_post.return_value = mock_gemini_resp

        audio_content = b"RIFF....WAVEfmt ....dataFAKE_AUDIO_SAMPLE_12345678901234567890" * 10
        audio_file = SimpleUploadedFile("grabacion_prueba.m4a", audio_content, content_type="audio/mp4")

        request = self.factory.post(
            '/api/peticiones/seguimiento/dictado-voz/',
            {'audio': audio_file},
            format='multipart'
        )
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)

        with patch('peticiones.services.gemini_dictado_service.obtener_gemini_api_key', return_value="dummy_api_key"):
            response = self.view(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.data
            self.assertEqual(data["status"], "draft")
            self.assertTrue(data["requires_review"])
            self.assertIn("Rocky", data["fields"]["nombre_paciente"]["value"])
            self.assertEqual(data["fields"]["paciente_especie"]["value"], "Canino")
            self.assertTrue(data["fields"]["sw_digestivo_vomito"]["value"])
