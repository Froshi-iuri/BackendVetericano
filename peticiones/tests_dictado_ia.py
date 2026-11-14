import base64
import json
from unittest.mock import MagicMock, patch

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from peticiones.api.views import DictadoVozActaView, FormularioActaSchemaView
from peticiones.services.acta_schema import FORM_ID_ACTA, FORM_VERSION_ACTA
from peticiones.services.gemini_dictado_service import (
    GEMINI_API_MODELS,
    procesar_audio_con_gemini,
    sanear_respuesta_gemini,
)


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


def _make_valid_audio_bytes(size: int = 2048) -> bytes:
    header = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A mp42isom"
    return (header + b"A" * max(0, size - len(header)))[:size]


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
        self.assertEqual(response.data.get("error_code"), "permiso_denegado")

    def test_dictado_error_si_no_se_envia_audio(self):
        request = self.factory.post('/api/peticiones/seguimiento/dictado-voz/', {}, format='json')
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("error_code"), "audio_invalido")

    def test_dictado_rechaza_audio_demasiado_corto(self):
        audio_file = SimpleUploadedFile("corto.m4a", b"1234567890", content_type="audio/mp4")
        request = self.factory.post(
            '/api/peticiones/seguimiento/dictado-voz/',
            {'audio': audio_file},
            format='multipart',
        )
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("error_code"), "audio_invalido")

    def test_dictado_rechaza_mime_no_permitido(self):
        audio_file = SimpleUploadedFile("nota.txt", _make_valid_audio_bytes(2048), content_type="text/plain")
        request = self.factory.post(
            '/api/peticiones/seguimiento/dictado-voz/',
            {'audio': audio_file},
            format='multipart',
        )
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("error_code"), "audio_invalido")

    def test_dictado_rechaza_content_length_excedido_antes_de_leer(self):
        request = self.factory.post(
            '/api/peticiones/seguimiento/dictado-voz/',
            {},
            format='json',
            CONTENT_LENGTH=str(40 * 1024 * 1024),
        )
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        self.assertEqual(response.data.get("error_code"), "audio_invalido")

    def test_dictado_rechaza_archivo_grande_sin_leer_completo(self):
        mock_archivo = MagicMock()
        mock_archivo.size = 26 * 1024 * 1024
        mock_archivo.content_type = "audio/mp4"

        wsgi_request = self.factory.post('/api/peticiones/seguimiento/dictado-voz/', {}, format='multipart')
        view_instance = DictadoVozActaView()
        drf_request = view_instance.initialize_request(wsgi_request)
        drf_request._user = MockUser(id_usuario=10, rol_id=3)
        drf_request._files = {'audio': mock_archivo}
        drf_request._full_data = {'audio': mock_archivo}
        response = view_instance.post(drf_request)

        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        self.assertEqual(response.data.get("error_code"), "audio_invalido")
        mock_archivo.read.assert_not_called()

    def test_dictado_audio_base64_sin_api_key_retorna_503_controlado(self):
        audio_dummy = base64.b64encode(_make_valid_audio_bytes(2048)).decode('utf-8')
        request = self.factory.post(
            '/api/peticiones/seguimiento/dictado-voz/',
            {'audio': audio_dummy},
            format='json',
        )
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)

        with patch('peticiones.services.gemini_dictado_service.obtener_gemini_api_key', return_value=""):
            response = self.view(request)
            self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
            self.assertEqual(response.data.get("status"), "error")
            self.assertEqual(response.data.get("error_code"), "ia_no_configurada")
            self.assertTrue(response.data.get("requires_review"))

    @patch('peticiones.services.gemini_dictado_service.requests.post')
    def test_dictado_audio_multipart_exito_con_gemini_y_payload_seguro(self, mock_requests_post):
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
                                    "transcripcion": "Paciente canino macho llamado Rocky, propietario Juan Pérez.",
                                    "extracted_summary": "Atención a canino macho con gastroenteritis.",
                                    "fields": {
                                        "nombre_paciente": {"value": "Rocky", "confidence": 0.95, "source": "voice"},
                                        "paciente_especie": {"value": "Canino", "confidence": 0.98, "source": "voice"},
                                        "paciente_sexo": {"value": "Macho", "confidence": 0.92, "source": "voice"},
                                        "propietario_nombre": {"value": "Juan Pérez", "confidence": 0.9, "source": "voice"},
                                        "anamnesis_descripcion_queja": {"value": "Vómito y diarrea desde hace 2 días.", "confidence": 0.96, "source": "voice"},
                                        "tratamiento_realizado": {"value": "Hidratación y antibiótico.", "confidence": 0.88, "source": "voice"},
                                        "sw_digestivo_vomito": {"value": True, "confidence": 0.95, "source": "voice"},
                                        "sw_digestivo_diarrea": {"value": True, "confidence": 0.95, "source": "voice"},
                                        "esterilizacion_paciente": {"value": None, "confidence": 0.0, "source": "missing"},
                                    },
                                    "warnings": [],
                                    "requires_review": True,
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_requests_post.return_value = mock_gemini_resp

        audio_content = _make_valid_audio_bytes(2048)
        audio_file = SimpleUploadedFile("grabacion_prueba.m4a", audio_content, content_type="audio/mp4")

        request = self.factory.post(
            '/api/peticiones/seguimiento/dictado-voz/',
            {'audio': audio_file},
            format='multipart',
        )
        request.user = MockUser(id_usuario=10, rol_id=3)
        force_authenticate(request, user=request.user)

        with patch('peticiones.services.gemini_dictado_service.obtener_gemini_api_key', return_value="secret_key_test"):
            response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual(data["status"], "draft")
        self.assertTrue(data["requires_review"])
        self.assertIn("Rocky", data["transcripcion"])
        self.assertEqual(data["fields"]["nombre_paciente"]["value"], "Rocky")
        self.assertEqual(data["fields"]["paciente_especie"]["value"], "Canino")
        self.assertTrue(data["fields"]["sw_digestivo_vomito"]["value"])
        self.assertIsNone(data["fields"]["esterilizacion_paciente"]["value"])
        self.assertEqual(data["fields"]["esterilizacion_paciente"]["source"], "missing")

        # Verificar seguridad y estructura del llamado a Gemini
        called_args, called_kwargs = mock_requests_post.call_args
        self.assertNotIn("key=", called_args[0])
        self.assertEqual(called_kwargs["headers"]["x-goog-api-key"], "secret_key_test")
        payload = called_kwargs["json"]
        self.assertIn("systemInstruction", payload)
        self.assertEqual(payload["generationConfig"]["temperature"], 0)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("responseSchema", payload["generationConfig"])

    def test_sanear_respuesta_descarta_desconocidos_coacciona_y_valida_enums(self):
        raw = {
            "transcripcion": "Perro mestizo de 12,5 kilos atendido el 14/11/2026.",
            "extracted_summary": "Valoración clínica.",
            "fields": {
                "campo_inventado_ia": {"value": "abc", "confidence": 0.99, "source": "voice"},
                "paciente_especie": {"value": "perro", "confidence": 1.4, "source": "voice"},
                "paciente_sexo": {"value": "desconocido_invalido", "confidence": 0.8, "source": "voice"},
                "peso_paciente": {"value": "12,5", "confidence": -0.3, "source": "voice"},
                "plazo_dias_cumplimiento": {"value": "8", "confidence": 0.9, "source": "voice"},
                "fecha_atencion": {"value": "14/11/2026", "confidence": 0.95, "source": "voice"},
                "esterilizacion_paciente": {"value": False, "confidence": 0.0, "source": "missing"},
                "desparasitacion": {"value": "sí", "confidence": 0.91, "source": "voice"},
            },
            "warnings": ["Duda menor"],
        }

        sanitized = sanear_respuesta_gemini(raw)
        fields = sanitized["fields"]

        self.assertNotIn("campo_inventado_ia", fields)
        self.assertEqual(fields["paciente_especie"]["value"], "Canino")
        self.assertEqual(fields["paciente_especie"]["confidence"], 1.0)
        self.assertIsNone(fields["paciente_sexo"]["value"])
        self.assertEqual(fields["paciente_sexo"]["source"], "missing")
        self.assertEqual(fields["peso_paciente"]["value"], 12.5)
        self.assertEqual(fields["peso_paciente"]["confidence"], 0.0)
        self.assertEqual(fields["plazo_dias_cumplimiento"]["value"], 8)
        self.assertEqual(fields["fecha_atencion"]["value"], "2026-11-14")
        # Boolean marcado como missing NO debe quedar como false
        self.assertIsNone(fields["esterilizacion_paciente"]["value"])
        self.assertEqual(fields["esterilizacion_paciente"]["source"], "missing")
        self.assertTrue(fields["desparasitacion"]["value"])
        self.assertGreaterEqual(len(sanitized["warnings"]), 2)

    def test_sanear_respuesta_malformada_no_falla(self):
        sanitized = sanear_respuesta_gemini(["no", "es", "dict"])
        self.assertEqual(sanitized["status"], "error")
        self.assertEqual(sanitized["error_code"], "sin_datos")
        self.assertTrue(sanitized["requires_review"])

    @patch('peticiones.services.gemini_dictado_service.requests.post')
    def test_sin_datos_cuando_todos_los_campos_son_missing(self, mock_requests_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "form_id": FORM_ID_ACTA,
                            "form_version": 1,
                            "status": "draft",
                            "transcripcion": "Hola probando micrófono",
                            "extracted_summary": "",
                            "fields": {
                                "esterilizacion_paciente": {"value": None, "confidence": 0.0, "source": "missing"}
                            },
                            "warnings": [],
                            "requires_review": True,
                        })
                    }]
                }
            }]
        }
        mock_requests_post.return_value = mock_resp

        with patch('peticiones.services.gemini_dictado_service.obtener_gemini_api_key', return_value="dummy"):
            data, code = procesar_audio_con_gemini(_make_valid_audio_bytes(2048), "audio/mp4")

        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error_code"], "sin_datos")
        self.assertEqual(data["transcripcion"], "Hola probando micrófono")

    @patch('peticiones.services.gemini_dictado_service.time.sleep')
    @patch('peticiones.services.gemini_dictado_service.requests.post')
    def test_reintento_429_y_saturacion_controlada(self, mock_requests_post, mock_sleep):
        mock_429 = MagicMock(status_code=429)
        mock_requests_post.return_value = mock_429

        with patch('peticiones.services.gemini_dictado_service.obtener_gemini_api_key', return_value="dummy"):
            data, code = procesar_audio_con_gemini(_make_valid_audio_bytes(2048), "audio/mp4")

        self.assertEqual(code, 503)
        self.assertEqual(data["error_code"], "ia_saturada")
        self.assertLessEqual(len(GEMINI_API_MODELS), 3)
        self.assertTrue(mock_sleep.called)

    @patch('peticiones.services.gemini_dictado_service.requests.post')
    def test_timeout_retorna_504_y_error_code_timeout(self, mock_requests_post):
        mock_requests_post.side_effect = requests.Timeout("timed out")

        with patch('peticiones.services.gemini_dictado_service.obtener_gemini_api_key', return_value="dummy"):
            data, code = procesar_audio_con_gemini(_make_valid_audio_bytes(2048), "audio/mp4")

        self.assertEqual(code, 504)
        self.assertEqual(data["error_code"], "timeout")
