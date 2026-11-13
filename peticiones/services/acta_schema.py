"""
Definición canónica y centralizada del Formulario Dinámico de Acta de Atención
y Valoración Clínica en Campo (Seres Sintientes) - VETERICANO.

Permite versionar el formulario, gestionar la visibilidad condicional de campos,
validar datos y alimentar la extracción de información por Inteligencia Artificial (Gemini).
"""

FORM_ID_ACTA = "acta_atencion_campo"
FORM_VERSION_ACTA = 1

ACTA_FORM_SCHEMA = {
    "form_id": FORM_ID_ACTA,
    "form_version": FORM_VERSION_ACTA,
    "title": "Acta de Atención y Valoración en Campo",
    "description": "Formulario oficial para registro de visitas veterinarias, valoración clínica, rescates y cierres legales.",
    "sections": [
        {
            "id": "sec_datos_generales",
            "title": "Datos Generales de la Atención",
            "step": 1,
            "description": "Información administrativa de la visita, solicitante y propietario.",
            "fields": [
                {
                    "id": "numero_radicado",
                    "label": "Número de Radicado",
                    "type": "text",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Código de radicado asignado a la petición (ej. VIS-2024-001 o número proporcionado)."
                },
                {
                    "id": "fecha_atencion",
                    "label": "Fecha de Atención",
                    "type": "date",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Fecha en que se llevó a cabo la visita (formato YYYY-MM-DD)."
                },
                {
                    "id": "solicitud_atencion_por",
                    "label": "Solicitud de Atención Por",
                    "type": "select",
                    "required": True,
                    "voice_extractable": True,
                    "options": [
                        "Queja o Denuncia Ciudadana",
                        "Oficio de Autoridad Competente",
                        "Ronda de Inspección Rutinaria",
                        "Solicitud Institucional",
                        "Emergencia en Vía Pública",
                        "Otro"
                    ],
                    "description": "Canal o motivo de inicio de la visita."
                },
                {
                    "id": "quien_reporta",
                    "label": "¿Quién Reporta?",
                    "type": "select",
                    "required": True,
                    "voice_extractable": True,
                    "options": [
                        "Propietario / Tenedor",
                        "Vecino o Ciudadano Afectado",
                        "Policía Ambiental",
                        "Veeduría Animalista",
                        "Comunitaria / JAC",
                        "Otro"
                    ],
                    "description": "Persona o entidad que dio aviso."
                },
                {
                    "id": "quien_reporta_otro",
                    "label": "Especifique Quién Reporta",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "conditional_rule": {
                        "field": "quien_reporta",
                        "operator": "equals",
                        "value": "Otro"
                    },
                    "description": "Detalle adicional si quién reporta es 'Otro'."
                },
                {
                    "id": "propietario_nombre",
                    "label": "Nombre del Propietario / Tenedor",
                    "type": "text",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Nombre completo de la persona responsable del animal en el predio."
                },
                {
                    "id": "propietario_cedula",
                    "label": "Cédula / Documento de Identidad",
                    "type": "text",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Número de cédula o documento de identidad del propietario."
                },
                {
                    "id": "propietario_telefono",
                    "label": "Teléfono de Contacto",
                    "type": "text",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Número telefónico o celular del propietario."
                },
                {
                    "id": "propietario_email",
                    "label": "Correo Electrónico",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Email de contacto del propietario."
                },
                {
                    "id": "propietario_barrio",
                    "label": "Barrio / Vereda",
                    "type": "text",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Barrio, comuna o vereda donde se realiza la atención."
                },
                {
                    "id": "propietario_direccion",
                    "label": "Dirección del Predio",
                    "type": "text",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Dirección exacta del predio visitado."
                },
                {
                    "id": "lugar_atencion",
                    "label": "Lugar de Atención",
                    "type": "text",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Descripción general de la ubicación o predio de atención."
                }
            ]
        },
        {
            "id": "sec_pacientes_clinica",
            "title": "Pacientes y Evaluación Clínica",
            "step": 2,
            "description": "Identificación de los animales atendidos, signos vitales y tratamiento instaurado.",
            "fields": [
                {
                    "id": "nombre_paciente",
                    "label": "Nombre del Animal / Paciente",
                    "type": "text",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Nombre del animal evaluado (o 'Sin nombre' si es callejero/desconocido)."
                },
                {
                    "id": "paciente_especie",
                    "label": "Especie",
                    "type": "select",
                    "required": True,
                    "voice_extractable": True,
                    "options": ["Canino", "Felino", "Equino", "Bovino", "Porcino", "Otro"],
                    "description": "Especie del paciente."
                },
                {
                    "id": "paciente_raza",
                    "label": "Raza",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Raza del animal (ej. Mestizo, Criollo, Labrador, etc.)."
                },
                {
                    "id": "paciente_sexo",
                    "label": "Sexo",
                    "type": "select",
                    "required": True,
                    "voice_extractable": True,
                    "options": ["Macho", "Hembra", "Indeterminado"],
                    "description": "Sexo biológico del animal."
                },
                {
                    "id": "paciente_color",
                    "label": "Color / Pelaje",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Color y señas particulares del pelaje."
                },
                {
                    "id": "paciente_edad",
                    "label": "Edad Aproximada",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Edad aproximada (ej. 2 años, 6 meses, adulto, cachorro, senior)."
                },
                {
                    "id": "peso_paciente",
                    "label": "Peso Estimado (kg)",
                    "type": "decimal",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Peso en kilogramos del animal (número decimal)."
                },
                {
                    "id": "esterilizacion_paciente",
                    "label": "Esterilizado / Castrado",
                    "type": "boolean",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Indica si el animal se encuentra esterilizado o castrado (true o false)."
                },
                {
                    "id": "frecuencia_cardiaca",
                    "label": "Frecuencia Cardíaca (lpm)",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Frecuencia cardíaca en latidos por minuto o descripción clínica."
                },
                {
                    "id": "frecuencia_respiratoria",
                    "label": "Frecuencia Respiratoria (rpm)",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Frecuencia respiratoria en respiraciones por minuto."
                },
                {
                    "id": "mucosas",
                    "label": "Estado de Mucosas",
                    "type": "select",
                    "required": False,
                    "voice_extractable": True,
                    "options": ["Rosadas", "Pálidas", "Ictéricas", "Cianóticas", "Congestivas"],
                    "description": "Color y perfusión de las mucosas aparentes."
                },
                {
                    "id": "anamnesis_descripcion_queja",
                    "label": "Anamnesis / Motivo de la Queja o Consulta",
                    "type": "text_long",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Descripción de los antecedentes, hechos reportados, tiempo de evolución y hallazgos iniciales."
                },
                {
                    "id": "tratamiento_realizado",
                    "label": "Tratamiento Realizado / Conducta Médica",
                    "type": "text_long",
                    "required": True,
                    "voice_extractable": True,
                    "description": "Procedimientos clínicos aplicados, administración de medicamentos o primeros auxilios."
                },
                {
                    "id": "desparasitacion",
                    "label": "Desparasitación Aplicada",
                    "type": "boolean",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Indica si se administró desparasitante durante la atención (true o false)."
                }
            ]
        },
        {
            "id": "sec_pruebas_cierre",
            "title": "Pruebas Complementarias, Cierre y Compromisos",
            "step": 3,
            "description": "Exámenes paraclínicos, estado de cierre de la visita y compromisos adquiridos.",
            "fields": [
                {
                    "id": "tipo_prueba",
                    "label": "Tipo de Prueba Solicitada",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Pruebas o test rápidos efectuados (ej. Parvovirus, Distemper, Hemoparásitos, etc.)."
                },
                {
                    "id": "pruebas_complementarias",
                    "label": "Pruebas Complementarias Realizadas",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Nombre de las pruebas o exámenes de laboratorio requeridos o ejecutados."
                },
                {
                    "id": "resultado_pruebas",
                    "label": "Resultado de Pruebas",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Resultado de las pruebas aplicadas (Positivo, Negativo, Pendiente, etc.)."
                },
                {
                    "id": "compromisos",
                    "label": "Compromisos Adquiridos por el Tenedor",
                    "type": "text_long",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Obligaciones fijadas al propietario (alimentación, techo, vacunas, visita periódica)."
                },
                {
                    "id": "plazo_dias_cumplimiento",
                    "label": "Plazo en Días para Cumplimiento",
                    "type": "integer",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Cantidad de días otorgados para verificar el cumplimiento del compromiso."
                },
                {
                    "id": "fundamento_legal",
                    "label": "Fundamento Legal",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "default": "Ley 1774 de 2016",
                    "description": "Normativa legal aplicable a la visita veterinaria."
                },
                {
                    "id": "estado_cierre",
                    "label": "Estado de Cierre de la Visita",
                    "type": "select",
                    "required": False,
                    "voice_extractable": True,
                    "options": [
                        "En tratamiento",
                        "Caso Cerrado - Favorable",
                        "Remitido a CBA Popayán",
                        "Traslado por Protección Animal",
                        "En Seguimiento Posterior"
                    ],
                    "description": "Determinación final de la visita de campo."
                },
                {
                    "id": "observacion",
                    "label": "Observaciones Generales",
                    "type": "text_long",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Anotaciones adicionales sobre el procedimiento o entorno."
                }
            ]
        },
        {
            "id": "sec_rescate_cba",
            "title": "Módulo de Rescate CBA (Centro de Bienestar Animal)",
            "step": 3,
            "description": "Aplica si se efectúa rescate y traslado del animal al CBA según Formato Popayán F-GS-C01-03-33.",
            "visibility_rule": {
                "field": "requiere_rescate",
                "operator": "equals",
                "value": True
            },
            "fields": [
                {
                    "id": "requiere_rescate",
                    "label": "¿Requiere Rescate y Traslado al CBA?",
                    "type": "boolean",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Indica si el animal es retirado del sitio y trasladado al Centro de Bienestar Animal (true o false)."
                },
                {
                    "id": "motivo_rescate",
                    "label": "Motivo del Rescate",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Causa del rescate (ej. Maltrato crítico, Abandono en vía pública, Atropellamiento, Incautación)."
                },
                {
                    "id": "persona_atiende_rescatista",
                    "label": "Nombre del Rescatista / Ciudadano Entrega",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Nombre de quien realiza el rescate o entrega el animal al veterinario."
                },
                {
                    "id": "barrio_rescate",
                    "label": "Barrio / Sector del Rescate",
                    "type": "text",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Sector específico donde se ejecutó el rescate."
                },
                {
                    "id": "observaciones_rescate",
                    "label": "Observaciones del Rescate",
                    "type": "text_long",
                    "required": False,
                    "voice_extractable": True,
                    "description": "Detalles del estado del animal al momento de ser rescatado."
                }
            ]
        },
        {
            "id": "sec_evaluacion_sistemas",
            "title": "Evaluación Clínica por Sistemas (Hallazgos Patológicos)",
            "step": 3,
            "description": "Signos clínicos específicos detectados en los sistemas orgánicos del animal.",
            "fields": [
                {"id": "sw_respiratorio_moco", "label": "Presencia de Secreción / Moco", "type": "boolean", "voice_extractable": True, "description": "Secreción nasal o moco abundante."},
                {"id": "sw_respiratorio_dificultad", "label": "Dificultad Respiratoria / Disnea", "type": "boolean", "voice_extractable": True, "description": "Respiración forzada o disnea."},
                {"id": "sw_respiratorio_tos", "label": "Tos Frecuente", "type": "boolean", "voice_extractable": True, "description": "Tos persistente o estertores."},
                {"id": "sw_respiratorio_laganeo", "label": "Lagañas / Secreción Ocular", "type": "boolean", "voice_extractable": True, "description": "Secreción ocular o conjuntivitis."},
                {"id": "sw_digestivo_vomito", "label": "Vómito Reportado u Observado", "type": "boolean", "voice_extractable": True, "description": "Episodios de vómito."},
                {"id": "sw_digestivo_diarrea", "label": "Diarrea", "type": "boolean", "voice_extractable": True, "description": "Deposiciones diarreicas."},
                {"id": "sw_digestivo_desnutrido", "label": "Desnutrición Marcada / Caquexia", "type": "boolean", "voice_extractable": True, "description": "Baja condición corporal o caquexia."},
                {"id": "sw_digestivo_deshidratado", "label": "Deshidratación Evidente", "type": "boolean", "voice_extractable": True, "description": "Pliegue cutáneo persistente o deshidratación."},
                {"id": "sw_musculo_caida_patas", "label": "Debilidad / Caída de Tren Posterior", "type": "boolean", "voice_extractable": True, "description": "Inestabilidad locomotora o caída de patas."},
                {"id": "sw_musculo_cojo", "label": "Claudicación / Cojera", "type": "boolean", "voice_extractable": True, "description": "Cojera o dolor en extremidades."},
                {"id": "sw_musculo_herida_abierta", "label": "Herida Abierta / Traumatismo", "type": "boolean", "voice_extractable": True, "description": "Heridas o laceraciones abiertas."},
                {"id": "sw_musculo_gusanos", "label": "Miasis / Presencia de Larvas", "type": "boolean", "voice_extractable": True, "description": "Miasis o gusanos en heridas."},
                {"id": "sw_musculo_postrado", "label": "Animal Postrado / Incapaz de Levantarse", "type": "boolean", "voice_extractable": True, "description": "Postración severa."},
                {"id": "sw_dermato_peladuras", "label": "Alopecias / Peladuras / Sarna", "type": "boolean", "voice_extractable": True, "description": "Zonas alopécicas o lesiones de sarna."},
                {"id": "sw_dermato_heridas_sangre", "label": "Heridas Sangrantes", "type": "boolean", "voice_extractable": True, "description": "Hemorragia activa o heridas sangrantes."},
                {"id": "sw_dermato_pulgas", "label": "Infestación Pulgas / Garrapatas", "type": "boolean", "voice_extractable": True, "description": "Ectoparásitos abundantes."}
            ]
        }
    ]
}


def obtener_schema_formulario_acta() -> dict:
    """Retorna la definición completa del esquema del formulario."""
    return ACTA_FORM_SCHEMA


def obtener_campos_extractables_ia() -> list:
    """
    Retorna una lista aplanada de todos los campos que la IA puede completar
    a partir de la narración de audio, junto con su descripción y tipo.
    """
    campos = []
    for section in ACTA_FORM_SCHEMA["sections"]:
        for field in section["fields"]:
            if field.get("voice_extractable", False):
                campos.append({
                    "id": field["id"],
                    "label": field["label"],
                    "type": field["type"],
                    "description": field.get("description", ""),
                    "options": field.get("options", None)
                })
    return campos

