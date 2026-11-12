"""
Servicio para procesamiento de audio y extracción estructurada con Gemini AI.
Integra la API de Google Gemini para convertir la narración del médico veterinario
en campos normalizados del Acta de Atención en Campo (VETERICANO).
"""

import os
import json
import base64
import logging
import requests
from typing import Dict, Any, Tuple
from django.conf import settings
from .acta_schema import FORM_ID_ACTA, FORM_VERSION_ACTA, obtener_campos_extractables_ia

logger = logging.getLogger(__name__)

GEMINI_API_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
]

def obtener_gemini_api_key() -> str:
    """
    Obtiene la API key de Gemini desde variables de entorno o configuración.
    Garantiza que la clave no quede expuesta.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        # Fallback a claves alternativas presentes en el proyecto
        key = os.environ.get("API_URL_ASISTENTE", "").strip()
    return key


def construir_prompt_extraccion(campos_schema: list) -> str:
    """
    Genera las directrices e instrucciones de extracción estricta para Gemini.
    """
    lista_campos_str = json.dumps(campos_schema, indent=2, ensure_ascii=False)

    return f"""
Eres un asistente veterinario experto para la aplicación VETERICANO de la Secretaría de Salud y Bienestar Animal.
Tu tarea es escuchar el audio adjunto grabado por un médico veterinario en campo y extraer de forma
precisa y fidedigna los datos correspondientes a los campos del Acta de Atención.

CAMPOS DEL ACTA DISPONIBLES:
{lista_campos_str}

REGLAS OBLIGATORIAS:
1. NO INVENTES INFORMACIÓN. Extrae únicamente hechos, datos y observaciones expresamente narrados.
2. Si un dato no se menciona en la narración, su valor debe ser null y source="missing".
3. No asumas fechas que no se digan explícitamente.
4. No asumas nombres de personas, documentos de identidad ni direcciones que no se mencionen.
5. Para campos booleanos (ej. esterilizacion_paciente, desparasitacion, requiere_rescate o síntomas clínicos sw_*):
   - Asigna true si el veterinario afirma explícitamente la presencia o realización del hecho.
   - Asigna false si afirma explícitamente que no se realizó o que no está presente.
   - Si no se menciona, asigna false con confidence=0.0 o null.
6. Si hay ambigüedad o dudas razonables en lo escuchado, incluye una nota en la lista "warnings".
7. Cada campo en "fields" debe ser un objeto con:
   - "value": valor extraído (texto, número, booleano o null).
   - "confidence": número flotante entre 0.0 y 1.0 indicando nivel de certeza acústica y semántica.
   - "source": "voice" si se detectó en el audio, o "missing" si no se mencionó.

FORMATO DE RESPUESTA REQUERIDO (JSON puro):
{{
  "form_id": "{FORM_ID_ACTA}",
  "form_version": {FORM_VERSION_ACTA},
  "status": "draft",
  "extracted_summary": "Resumen en una frase clara de lo que se escuchó y realizó en la visita.",
  "fields": {{
    "nombre_paciente": {{"value": "Toby", "confidence": 0.95, "source": "voice"}},
    "paciente_especie": {{"value": "Canino", "confidence": 0.98, "source": "voice"}},
    "anamnesis_descripcion_queja": {{"value": "Animal presenta decaimiento de 2 días...", "confidence": 0.9, "source": "voice"}}
  }},
  "warnings": [],
  "requires_review": true
}}
"""


def procesar_audio_con_gemini(
    audio_bytes: bytes,
    mime_type: str = "audio/m4a",
    form_id: str = FORM_ID_ACTA,
    form_version: int = FORM_VERSION_ACTA
) -> Tuple[Dict[str, Any], int]:
    """
    Envía el audio a Google Gemini mediante REST API segura y procesa la respuesta estructurada.
    Retorna (data_dict, http_status_code).
    """
    api_key = obtener_gemini_api_key()

    if not api_key:
        logger.warning("GEMINI_API_KEY no configurada en el entorno.")
        return {
            "form_id": form_id,
            "form_version": form_version,
            "status": "error",
            "message": "La API Key de Gemini (GEMINI_API_KEY) no está configurada en el servidor. Puede continuar diligenciando el formulario manualmente.",
            "fields": {},
            "warnings": ["Servicio de IA no configurado: falta GEMINI_API_KEY en variables de entorno."],
            "requires_review": True
        }, 200

    if not audio_bytes or len(audio_bytes) < 100:
        return {
            "form_id": form_id,
            "form_version": form_version,
            "status": "error",
            "message": "El archivo de audio recibido está vacío o es demasiado corto.",
            "fields": {},
            "warnings": ["Audio inválido o vacío"],
            "requires_review": True
        }, 400

    # Normalizar mime_type para Gemini
    mime_lower = mime_type.lower()
    if "mp4" in mime_lower or "m4a" in mime_lower:
        normalized_mime = "audio/mp4"
    elif "aac" in mime_lower:
        normalized_mime = "audio/aac"
    elif "wav" in mime_lower:
        normalized_mime = "audio/wav"
    elif "ogg" in mime_lower:
        normalized_mime = "audio/ogg"
    elif "mp3" in mime_lower or "mpeg" in mime_lower:
        normalized_mime = "audio/mp3"
    elif "3gpp" in mime_lower or "3gp" in mime_lower:
        normalized_mime = "audio/3gpp"
    else:
        normalized_mime = "audio/mp4"

    campos_schema = obtener_campos_extractables_ia()
    prompt_text = construir_prompt_extraccion(campos_schema)
    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": normalized_mime,
                            "data": audio_base64
                        }
                    },
                    {
                        "text": prompt_text
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1
        }
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    last_error_msg = ""
    for model_name in GEMINI_API_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=45)
            if resp.status_code == 200:
                resp_json = resp.json()
                # Extraer texto de la respuesta
                candidates = resp_json.get("candidates", [])
                if not candidates:
                    logger.warning(f"Gemini {model_name} devolvió 0 candidatos.")
                    continue
                
                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    continue
                
                raw_text = parts[0].get("text", "").strip()
                # Limpiar posibles bloques ```json ... ```
                if raw_text.startswith("```"):
                    lineas = raw_text.splitlines()
                    if lineas[0].startswith("```"):
                        lineas = lineas[1:]
                    if lineas and lineas[-1].startswith("```"):
                        lineas = lineas[:-1]
                    raw_text = "\n".join(lineas).strip()

                try:
                    resultado_parsed = json.loads(raw_text)
                    # Asegurar campos requeridos
                    resultado_parsed["form_id"] = form_id
                    resultado_parsed["form_version"] = form_version
                    resultado_parsed["requires_review"] = True
                    if "fields" not in resultado_parsed:
                        resultado_parsed["fields"] = {}
                    if "warnings" not in resultado_parsed:
                        resultado_parsed["warnings"] = []
                    resultado_parsed["status"] = "draft"
                    
                    return resultado_parsed, 200
                except json.JSONDecodeError as jde:
                    logger.error(f"Error parseando JSON de Gemini ({model_name}): {jde}. Texto: {raw_text[:200]}")
                    last_error_msg = f"Respuesta de IA no tiene formato JSON válido."
                    continue

            elif resp.status_code in (404, 400, 429, 503):
                # Puede ser que el modelo no esté disponible en la región o versión, o esté saturado temporalmente; probar siguiente modelo
                last_error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.info(f"Modelo {model_name} respondió {resp.status_code}, probando fallback...")
                continue
            else:
                last_error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning(f"Gemini respondió con error {resp.status_code}: {resp.text[:200]}")
                break

        except requests.Timeout:
            logger.error(f"Tiempo de espera agotado al conectar con Gemini ({model_name}).")
            last_error_msg = "Tiempo de espera agotado al conectar con el servicio de IA."
            break
        except Exception as exc:
            logger.error(f"Excepción comunicándose con Gemini: {exc}")
            last_error_msg = str(exc)
            break

    # Si ningún modelo respondió con éxito, devolver respuesta defensiva
    return {
        "form_id": form_id,
        "form_version": form_version,
        "status": "error",
        "message": f"No fue posible interpretar el audio con IA ({last_error_msg}). Puede diligenciar el formulario manualmente.",
        "fields": {},
        "warnings": [f"Error de procesamiento de IA: {last_error_msg}"],
        "requires_review": True
    }, 200

