"""
Servicio para procesamiento de audio y extracción estructurada con Gemini AI.
Integra la API de Google Gemini para convertir la narración del médico veterinario
en campos normalizados del Acta de Atención en Campo (VETERICANO).
"""

import base64
import json
import logging
import math
import os
import time
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.conf import settings
from django.utils import timezone

from .acta_schema import FORM_ID_ACTA, FORM_VERSION_ACTA, obtener_campos_extractables_ia

logger = logging.getLogger(__name__)

# Modelos Gemini verificados en la API v1beta (máximo 3 en cascada)
GEMINI_API_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
]

TOTAL_BUDGET_SECONDS = 25.0
MAX_ATTEMPT_TIMEOUT_SECONDS = 10.0
RETRY_BACKOFF_SECONDS = 0.8
MIN_AUDIO_BYTES = 1024

MIME_NORMALIZATION_MAP = {
    "audio/mp4": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/aac": "audio/aac",
    "audio/3gpp": "audio/3gpp",
    "audio/3gp": "audio/3gpp",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/ogg": "audio/ogg",
    "audio/mp3": "audio/mp3",
    "audio/mpeg": "audio/mp3",
}

SELECT_SYNONYMS = {
    "paciente_especie": {
        "perro": "Canino",
        "perra": "Canino",
        "canina": "Canino",
        "cachorro": "Canino",
        "gato": "Felino",
        "gata": "Felino",
        "felina": "Felino",
        "gatito": "Felino",
        "caballo": "Equino",
        "yegua": "Equino",
        "equina": "Equino",
        "vaca": "Bovino",
        "toro": "Bovino",
        "bovina": "Bovino",
        "cerdo": "Porcino",
        "cerda": "Porcino",
        "porcina": "Porcino",
    },
    "paciente_sexo": {
        "masculino": "Macho",
        "macho": "Macho",
        "femenino": "Hembra",
        "hembra": "Hembra",
    },
}


def obtener_gemini_api_key() -> str:
    """
    Obtiene la API key de Gemini desde variables de entorno o settings.
    Garantiza que la clave no quede expuesta.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    return key


def normalizar_mime_audio(mime_type: Optional[str]) -> Optional[str]:
    """
    Normaliza y valida el MIME type contra la lista blanca soportada por Gemini.
    Retorna None si no pertenece a un formato de audio permitido.
    """
    if not mime_type or not str(mime_type).strip():
        return "audio/mp4"
    base_mime = str(mime_type).split(";", 1)[0].strip().lower()
    return MIME_NORMALIZATION_MAP.get(base_mime)


def _strip_accents(texto: str) -> str:
    normalized = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _fecha_referencia_actual() -> str:
    try:
        return timezone.localdate().isoformat()
    except Exception:
        return date.today().isoformat()


def construir_instruccion_sistema(campos_schema: List[Dict[str, Any]], fecha_actual: Optional[str] = None) -> str:
    """
    Genera la instrucción del sistema (systemInstruction) para Gemini con contexto
    veterinario colombiano, fecha actual y reglas estrictas contra alucinaciones.
    """
    fecha_ref = fecha_actual or _fecha_referencia_actual()
    lista_campos_str = json.dumps(campos_schema, indent=2, ensure_ascii=False)

    return f"""Eres un asistente médico veterinario experto para la aplicación VETERICANO (Secretaría de Salud y Bienestar Animal, Colombia).
Tu función es escuchar la grabación de voz de un médico veterinario en campo, transcribirla literalmente y extraer de forma estricta únicamente los datos expresamente mencionados para el Acta de Atención en Campo.

CONTEXTO OPERATIVO:
- Idioma del audio: Español (Colombia).
- Fecha actual de referencia: {fecha_ref} (si el médico dice "hoy", usa {fecha_ref}; si dice "ayer", calcula la fecha exacta respecto a {fecha_ref}; si no menciona fecha, déjala en null).
- Vocabulario clínico esperado: especies (Canino, Felino, Equino, Bovino, Porcino), razas comunes en Colombia (Mestizo, Criollo, Labrador, Poodle, Criollo colombiano, etc.), constantes fisiológicas (lpm, rpm), mucosas (Rosadas, Pálidas, Ictéricas, Cianóticas, Congestivas), signos clínicos (vómito, diarrea, caquexia, deshidratación, miasis, claudicación, alopecia, ectoparásitos), tratamientos (desparasitación, antibiótico, AINE, curación), Ley 1774 de 2016, rescate al Centro de Bienestar Animal (CBA Popayán).

REGLAS OBLIGATORIAS DE EXTRACCIÓN:
1. Incluye siempre el campo "transcripcion" con la transcripción literal y fiel en español de lo que se escuchó en el audio (sin inventar palabras). Si el audio es inaudible o solo ruido, deja "transcripcion" vacía "".
2. NO INVENTES INFORMACIÓN. Extrae únicamente hechos, datos y observaciones expresamente narrados en el audio.
3. Si un campo NO se menciona en el audio (INCLUYENDO cualquier campo booleano como esterilizacion_paciente, desparasitacion, requiere_rescate o síntomas sw_*):
   - "value" DEBE ser null
   - "source" DEBE ser "missing"
   - "confidence" DEBE ser 0.0
   (NUNCA pongas false ni valores por defecto para campos no mencionados).
4. Para campos booleanos:
   - Asigna true SOLO si el veterinario afirma explícitamente que está presente o se realizó.
   - Asigna false SOLO si el veterinario niega explícitamente su presencia o realización (ej. "no está esterilizado", "sin vómito", "no requiere rescate").
   - En cualquier otro caso (no mencionado o dudoso): value=null, source="missing", confidence=0.0.
5. Corrección de errores de voz/transcripción:
   - Corrige términos mal pronunciados o transcritos fonéticamente ÚNICAMENTE cuando coincidan claramente con las opciones válidas definidas en el esquema (por ejemplo: especies, sexo, mucosas, solicitud_atencion_por, quien_reporta, estado_cierre o razas conocidas).
   - Nunca inventes nombres propios, cédulas, teléfonos, direcciones ni fechas no dictadas.
6. Para cada campo reportado en "fields", usa exactamente:
   - "value": valor extraído acorde al tipo del campo, o null si no se mencionó.
   - "confidence": número entre 0.0 y 1.0 según certeza acústica y semántica.
   - "source": "voice" si se escuchó en el audio, o "missing" si no se mencionó.
7. Si hay ambigüedades, números poco claros o términos dudosos, agrega una nota breve en "warnings".

ESQUEMA DE CAMPOS EXTRACTABLES DEL ACTA:
{lista_campos_str}
"""


def construir_prompt_extraccion(campos_schema: list) -> str:
    """
    Genera el mensaje de usuario complementario para la petición de extracción.
    """
    return (
        f"Analiza el audio adjunto de la visita veterinaria para el formulario "
        f"'{FORM_ID_ACTA}' (versión {FORM_VERSION_ACTA}). "
        f"Devuelve el JSON estricto con la transcripción literal, el resumen clínico, "
        f"los {len(campos_schema)} campos aplicables del esquema y cualquier advertencia. "
        f"Recuerda: cualquier campo (incluidos booleanos) no mencionado debe llevar value=null y source='missing'."
    )


def construir_response_schema(campos_schema: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Construye el responseSchema OpenAPI/JSON estricto para generationConfig de Gemini.
    """
    fields_properties: Dict[str, Any] = {}
    for campo in campos_schema:
        field_id = campo["id"]
        field_type = campo.get("type", "text")
        if field_type == "boolean":
            value_schema: Dict[str, Any] = {"type": "BOOLEAN", "nullable": True}
        elif field_type == "integer":
            value_schema = {"type": "INTEGER", "nullable": True}
        elif field_type == "decimal":
            value_schema = {"type": "NUMBER", "nullable": True}
        else:
            value_schema = {"type": "STRING", "nullable": True}

        fields_properties[field_id] = {
            "type": "OBJECT",
            "properties": {
                "value": value_schema,
                "confidence": {"type": "NUMBER"},
                "source": {"type": "STRING", "enum": ["voice", "missing"]},
            },
            "required": ["value", "confidence", "source"],
        }

    return {
        "type": "OBJECT",
        "properties": {
            "form_id": {"type": "STRING"},
            "form_version": {"type": "INTEGER"},
            "status": {"type": "STRING"},
            "transcripcion": {"type": "STRING"},
            "extracted_summary": {"type": "STRING"},
            "fields": {
                "type": "OBJECT",
                "properties": fields_properties,
            },
            "warnings": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "requires_review": {"type": "BOOLEAN"},
        },
        "required": [
            "form_id",
            "form_version",
            "status",
            "transcripcion",
            "extracted_summary",
            "fields",
            "warnings",
            "requires_review",
        ],
    }


def _clamp_confidence(raw_conf: Any, default: float = 0.5) -> float:
    try:
        val = float(raw_conf)
        if math.isnan(val) or math.isinf(val):
            return default
        return max(0.0, min(1.0, round(val, 4)))
    except (TypeError, ValueError):
        return default


def _coaccionar_booleano(val: Any) -> Optional[bool]:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if val == 1:
            return True
        if val == 0:
            return False
        return None
    if isinstance(val, str):
        norm = _strip_accents(val.strip().lower())
        if norm in ("true", "si", "1", "yes", "verdadero"):
            return True
        if norm in ("false", "no", "0", "falso"):
            return False
    return None


def _coaccionar_entero(val: Any) -> Optional[int]:
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val) or not val.is_integer():
            return None
        return int(val)
    if isinstance(val, str):
        s = val.strip().replace(",", ".")
        if not s:
            return None
        try:
            num = float(s)
            if math.isnan(num) or math.isinf(num) or not num.is_integer():
                return None
            return int(num)
        except ValueError:
            return None
    return None


def _coaccionar_decimal(val: Any) -> Optional[float]:
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        fval = float(val)
        return None if (math.isnan(fval) or math.isinf(fval)) else round(fval, 3)
    if isinstance(val, str):
        s = val.strip().replace(",", ".")
        if not s:
            return None
        try:
            fval = float(s)
            return None if (math.isnan(fval) or math.isinf(fval)) else round(fval, 3)
        except ValueError:
            return None
    return None


def _coaccionar_fecha(val: Any) -> Optional[str]:
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _validar_select(field_id: str, val: Any, options: List[str]) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s in options:
        return s

    s_norm = _strip_accents(s.lower())
    for opt in options:
        if _strip_accents(opt.lower()) == s_norm:
            return opt

    synonyms = SELECT_SYNONYMS.get(field_id, {})
    mapped = synonyms.get(s_norm)
    if mapped and mapped in options:
        return mapped
    return None


def sanear_respuesta_gemini(
    raw_parsed: Any,
    form_id: str = FORM_ID_ACTA,
    form_version: int = FORM_VERSION_ACTA,
) -> Dict[str, Any]:
    """
    Valida y sanea la salida cruda de Gemini contra el esquema canónico de acta_schema:
    - Descarta field IDs desconocidos.
    - Coacciona tipos (boolean, integer, decimal, date, text).
    - Valida opciones de campos select contra el esquema (null + warning si no coincide).
    - Hace clamp de confidence en [0.0, 1.0].
    - Normaliza campos 'missing' o vacíos a value=None, source='missing', confidence=0.0.
    """
    if not isinstance(raw_parsed, dict):
        raw_parsed = {}

    campos_schema = obtener_campos_extractables_ia()
    schema_by_id = {c["id"]: c for c in campos_schema}

    warnings_raw = raw_parsed.get("warnings")
    warnings: List[str] = []
    if isinstance(warnings_raw, list):
        for w in warnings_raw:
            if w is not None and str(w).strip():
                warnings.append(str(w).strip())

    raw_fields = raw_parsed.get("fields")
    if not isinstance(raw_fields, dict):
        raw_fields = {}

    sanitized_fields: Dict[str, Dict[str, Any]] = {}
    voice_count = 0

    for field_id, raw_entry in raw_fields.items():
        if field_id not in schema_by_id:
            continue

        campo_def = schema_by_id[field_id]
        field_type = campo_def.get("type", "text")
        options = campo_def.get("options")

        if isinstance(raw_entry, dict):
            raw_val = raw_entry.get("value")
            raw_conf = raw_entry.get("confidence")
            raw_source = str(raw_entry.get("source") or "").strip().lower()
        else:
            raw_val = raw_entry
            raw_conf = 0.5
            raw_source = "voice" if raw_val is not None else "missing"

        if raw_source == "missing" or raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
            sanitized_fields[field_id] = {
                "value": None,
                "confidence": 0.0,
                "source": "missing",
            }
            continue

        conf = _clamp_confidence(raw_conf, default=0.7)
        coerced_val: Any = None
        invalid_reason: Optional[str] = None

        if field_type == "boolean":
            coerced_val = _coaccionar_booleano(raw_val)
            if coerced_val is None:
                invalid_reason = f"Valor booleano inválido para '{campo_def['label']}'."
        elif field_type == "integer":
            coerced_val = _coaccionar_entero(raw_val)
            if coerced_val is None:
                invalid_reason = f"Valor entero inválido para '{campo_def['label']}'."
        elif field_type == "decimal":
            coerced_val = _coaccionar_decimal(raw_val)
            if coerced_val is None:
                invalid_reason = f"Valor numérico inválido para '{campo_def['label']}'."
        elif field_type == "date":
            coerced_val = _coaccionar_fecha(raw_val)
            if coerced_val is None:
                invalid_reason = f"Fecha inválida para '{campo_def['label']}' (se esperaba YYYY-MM-DD)."
        elif field_type == "select" and isinstance(options, list):
            coerced_val = _validar_select(field_id, raw_val, options)
            if coerced_val is None:
                invalid_reason = (
                    f"La opción detectada para '{campo_def['label']}' no coincide con el catálogo permitido."
                )
        else:
            texto = str(raw_val).strip()
            coerced_val = texto if texto else None

        if coerced_val is None:
            if invalid_reason:
                warnings.append(invalid_reason)
            sanitized_fields[field_id] = {
                "value": None,
                "confidence": 0.0,
                "source": "missing",
            }
        else:
            sanitized_fields[field_id] = {
                "value": coerced_val,
                "confidence": conf,
                "source": "voice",
            }
            voice_count += 1

    transcripcion = raw_parsed.get("transcripcion")
    transcripcion_str = str(transcripcion).strip() if isinstance(transcripcion, str) else ""

    summary = raw_parsed.get("extracted_summary")
    summary_str = str(summary).strip() if isinstance(summary, str) else ""

    if voice_count == 0:
        return {
            "form_id": form_id,
            "form_version": form_version,
            "status": "error",
            "error_code": "sin_datos",
            "message": (
                "No se identificaron datos específicos del acta en el audio. "
                "Intenta hablar más cerca del micrófono o continúa diligenciando manualmente."
            ),
            "transcripcion": transcripcion_str,
            "extracted_summary": summary_str,
            "fields": sanitized_fields,
            "warnings": warnings or ["No se detectaron campos del formulario en la grabación."],
            "requires_review": True,
        }

    if not summary_str:
        summary_str = f"Se extrajeron {voice_count} campos desde la narración de voz."

    return {
        "form_id": form_id,
        "form_version": form_version,
        "status": "draft",
        "transcripcion": transcripcion_str,
        "extracted_summary": summary_str,
        "fields": sanitized_fields,
        "warnings": warnings,
        "requires_review": True,
    }


def _construir_error_respuesta(
    form_id: str,
    form_version: int,
    error_code: str,
    message: str,
    warning: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "form_id": form_id,
        "form_version": form_version,
        "status": "error",
        "error_code": error_code,
        "message": message,
        "transcripcion": "",
        "fields": {},
        "warnings": [warning] if warning else [],
        "requires_review": True,
    }


def procesar_audio_con_gemini(
    audio_bytes: bytes,
    mime_type: str = "audio/m4a",
    form_id: str = FORM_ID_ACTA,
    form_version: int = FORM_VERSION_ACTA,
) -> Tuple[Dict[str, Any], int]:
    """
    Envía el audio a Google Gemini mediante REST API segura (key solo en header)
    con presupuesto de tiempo acotado y procesa la respuesta estructurada.
    Retorna (data_dict, http_status_code).
    """
    api_key = obtener_gemini_api_key()
    if not api_key:
        logger.warning("Servicio de dictado IA sin GEMINI_API_KEY configurada.")
        return (
            _construir_error_respuesta(
                form_id,
                form_version,
                "ia_no_configurada",
                "El servicio de dictado por voz no está disponible en el servidor en este momento. Puedes continuar diligenciando el acta manualmente.",
                "Servicio de IA no configurado en el servidor.",
            ),
            503,
        )

    if not audio_bytes or len(audio_bytes) < MIN_AUDIO_BYTES:
        return (
            _construir_error_respuesta(
                form_id,
                form_version,
                "audio_invalido",
                "El audio recibido está vacío o es demasiado corto para transcribirlo.",
                "Audio inválido o demasiado corto.",
            ),
            400,
        )

    normalized_mime = normalizar_mime_audio(mime_type)
    if not normalized_mime:
        return (
            _construir_error_respuesta(
                form_id,
                form_version,
                "audio_invalido",
                "El formato de audio enviado no es compatible.",
                "Formato MIME de audio no permitido.",
            ),
            400,
        )

    campos_schema = obtener_campos_extractables_ia()
    system_instruction_text = construir_instruccion_sistema(campos_schema)
    user_prompt_text = construir_prompt_extraccion(campos_schema)
    response_schema = construir_response_schema(campos_schema)
    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    payload = {
        "systemInstruction": {
            "parts": [{"text": system_instruction_text}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": normalized_mime,
                            "data": audio_base64,
                        }
                    },
                    {"text": user_prompt_text},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
            "temperature": 0,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    failure_kind = "error_ia"

    for model_name in GEMINI_API_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        max_attempts = 2  # 1 intento inicial + 1 reintento solo para 429/503

        for attempt in range(max_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                failure_kind = "timeout"
                break

            attempt_timeout = min(MAX_ATTEMPT_TIMEOUT_SECONDS, remaining)
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=attempt_timeout)
            except requests.Timeout:
                logger.warning("Timeout conectando con modelo Gemini %s.", model_name)
                failure_kind = "timeout"
                break
            except requests.RequestException as exc:
                logger.warning("Error de red comunicando con Gemini (%s): %s", model_name, type(exc).__name__)
                failure_kind = "error_ia"
                break

            if resp.status_code == 200:
                try:
                    resp_json = resp.json()
                    candidates = resp_json.get("candidates", []) if isinstance(resp_json, dict) else []
                    if not candidates:
                        logger.warning("Gemini (%s) devolvió 0 candidatos.", model_name)
                        failure_kind = "error_ia"
                        break

                    content = candidates[0].get("content", {}) if isinstance(candidates[0], dict) else {}
                    parts = content.get("parts", []) if isinstance(content, dict) else []
                    if not parts or not isinstance(parts[0], dict):
                        failure_kind = "error_ia"
                        break

                    raw_text = str(parts[0].get("text", "")).strip()
                    if raw_text.startswith("```"):
                        lineas = raw_text.splitlines()
                        if lineas and lineas[0].startswith("```"):
                            lineas = lineas[1:]
                        if lineas and lineas[-1].startswith("```"):
                            lineas = lineas[:-1]
                        raw_text = "\n".join(lineas).strip()

                    resultado_parsed = json.loads(raw_text)
                    sanitized = sanear_respuesta_gemini(
                        resultado_parsed,
                        form_id=form_id,
                        form_version=form_version,
                    )
                    return sanitized, 200
                except (ValueError, KeyError, TypeError) as parse_err:
                    logger.warning(
                        "Respuesta malformada de Gemini (%s): %s",
                        model_name,
                        type(parse_err).__name__,
                    )
                    failure_kind = "error_ia"
                    break

            if resp.status_code in (429, 503):
                failure_kind = "ia_saturada"
                logger.info("Gemini (%s) respondió HTTP %s.", model_name, resp.status_code)
                if attempt + 1 < max_attempts:
                    remaining_after = deadline - time.monotonic()
                    sleep_for = min(RETRY_BACKOFF_SECONDS, max(0.0, remaining_after - 1.0))
                    if sleep_for > 0.1:
                        time.sleep(sleep_for)
                        continue
                break

            if resp.status_code in (400, 404):
                logger.info("Gemini (%s) no disponible (HTTP %s), probando siguiente modelo.", model_name, resp.status_code)
                failure_kind = "error_ia"
                break

            logger.warning("Gemini (%s) respondió HTTP %s.", model_name, resp.status_code)
            failure_kind = "error_ia"
            break

        if (deadline - time.monotonic()) <= 0.5:
            failure_kind = "timeout"
            break

    if failure_kind == "timeout":
        return (
            _construir_error_respuesta(
                form_id,
                form_version,
                "timeout",
                "El procesamiento de voz tardó demasiado tiempo. Intenta con un audio más corto o continúa manualmente.",
                "Tiempo de espera agotado al procesar con IA.",
            ),
            504,
        )

    if failure_kind == "ia_saturada":
        return (
            _construir_error_respuesta(
                form_id,
                form_version,
                "ia_saturada",
                "El servicio de IA está temporalmente saturado. Intenta de nuevo en unos segundos o continúa manualmente.",
                "Servicio de IA temporalmente saturado (429/503).",
            ),
            503,
        )

    return (
        _construir_error_respuesta(
            form_id,
            form_version,
            "error_ia",
            "No fue posible interpretar el audio en este momento. Puedes intentar de nuevo o diligenciar el acta manualmente.",
            "Fallo al procesar la respuesta del servicio de IA.",
        ),
        502,
    )
