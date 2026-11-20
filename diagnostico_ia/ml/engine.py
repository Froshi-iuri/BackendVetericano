"""
Motor de inferencia y razonamiento clínico veterinario.
Carga el modelo local .joblib, realiza la vectorización de entradas,
aplica filtros biológicos de especie y reglas clínicas veterinarias,
y genera sugerencias diagnósticas (top 3 a 5), exámenes recomendados y resumen clínico.
"""

import os
import joblib
from typing import Dict, Any, List, Optional
import numpy as np

from diagnostico_ia.ml.dataset_generator import (
    vectorizar_caso_clinico,
    FEATURE_NAMES,
)
from diagnostico_ia.ml.knowledge_base import (
    PATOLOGIAS_KNOWLEDGE,
    ALIAS_TESTS,
    ALIAS_EXAMENES,
    ALIAS_SIGNOS,
    NOMBRES_HUMANOS_TESTS,
    NOMBRES_HUMANOS_EXAMENES,
)

MODEL_PATH = os.path.join(os.path.dirname(__file__), 'modelo_diagnostico.joblib')

_MOTOR_SINGLETON = None


class MotorDiagnosticoIA:
    def __init__(self, model_path: str = MODEL_PATH):
        self.model_path = model_path
        self.paquete = None
        self.model = None
        self.classes = []
        self.knowledge = PATOLOGIAS_KNOWLEDGE
        self.metadata = {}
        self._cargar_o_entrenar()

    def _cargar_o_entrenar(self):
        """
        Carga el modelo serializado de disco. Si no existe (ej. despliegue inicial),
        lo entrena automáticamente de forma local.
        """
        if not os.path.exists(self.model_path):
            from diagnostico_ia.ml.model_trainer import entrenar_y_guardar_modelo
            self.paquete = entrenar_y_guardar_modelo(casos_por_patologia=150, verbose=False)
        else:
            try:
                self.paquete = joblib.load(self.model_path)
            except Exception:
                from diagnostico_ia.ml.model_trainer import entrenar_y_guardar_modelo
                self.paquete = entrenar_y_guardar_modelo(casos_por_patologia=150, verbose=False)

        self.model = self.paquete['model']
        self.classes = list(self.paquete['classes'])
        self.knowledge = self.paquete.get('knowledge_base', PATOLOGIAS_KNOWLEDGE)
        self.metadata = self.paquete.get('metadata', {})

    def normalizar_entradas(
        self,
        especie_raw: str,
        edad_etapa_raw: Any,
        signos_raw: Any,
        tests_raw: Any,
        examenes_raw: Any
    ) -> Dict[str, Any]:
        """
        Normaliza cualquier estructura de datos recibida desde la API REST a la
        estructura canónica que entiende el vectorizador y el motor clínico.
        """
        # 1. Especie
        esp_str = str(especie_raw or '').strip().lower()
        if any(w in esp_str for w in ('perro', 'canino', 'can', 'dog')):
            especie = 'canino'
        elif any(w in esp_str for w in ('gato', 'felino', 'fel', 'cat')):
            especie = 'felino'
        else:
            especie = 'canino' if 'can' in esp_str else ('felino' if 'fel' in esp_str else 'desconocida')

        # 2. Etapa de vida
        edad_str = str(edad_etapa_raw or '').strip().lower()
        etapa = 'adulto'
        if any(w in edad_str for w in ('cachorro', 'gatito', 'bebe', 'cría', 'puppy', 'kitten', 'mes', 'meses')):
            etapa = 'cachorro_gatito'
        elif any(w in edad_str for w in ('joven', 'junior', '1 año', '2 años', '3 años')):
            etapa = 'joven'
        elif any(w in edad_str for w in ('senior', 'geronte', 'anciano', 'viejo', '8 años', '9 años', '10 años', '11 años', '12 años')):
            etapa = 'senior'

        # 3. Signos clínicos
        signos_normalizados = []
        if isinstance(signos_raw, str):
            candidatos = [s.strip().lower() for s in signos_raw.replace(';', ',').replace('.', ',').split(',') if s.strip()]
        elif isinstance(signos_raw, (list, tuple, set)):
            candidatos = [str(s).strip().lower() for s in signos_raw if str(s).strip()]
        elif isinstance(signos_raw, dict):
            candidatos = [k.strip().lower() for k, v in signos_raw.items() if v]
        else:
            candidatos = []

        for cand in candidatos:
            # Buscar coincidencia en alias o subcadenas
            cand_clean = cand.strip()
            if cand_clean in ALIAS_SIGNOS:
                signos_normalizados.append(ALIAS_SIGNOS[cand_clean])
            else:
                encontrado = False
                for alias_k, canon_v in ALIAS_SIGNOS.items():
                    if alias_k in cand_clean:
                        signos_normalizados.append(canon_v)
                        encontrado = True
                        break
                if not encontrado:
                    signos_normalizados.append(cand_clean)

        signos_normalizados = list(set(signos_normalizados))

        # 4. Tests rápidos
        tests_normalizados = {}
        if isinstance(tests_raw, dict):
            for t_k, t_v in tests_raw.items():
                k_clean = str(t_k).strip().lower()
                canon_k = ALIAS_TESTS.get(k_clean, k_clean)
                val_clean = str(t_v).strip().lower() if not isinstance(t_v, dict) else str(t_v.get('estado', '')).strip().lower()
                if val_clean in ('positivo', 'pos', 'reactivo', '+', 'true', '1'):
                    tests_normalizados[canon_k] = 'positivo'
                elif val_clean in ('negativo', 'neg', 'no reactivo', '-', 'false', '0'):
                    tests_normalizados[canon_k] = 'negativo'
                else:
                    tests_normalizados[canon_k] = 'no_realizado'
        elif isinstance(tests_raw, list):
            for item in tests_raw:
                if isinstance(item, dict):
                    t_k = item.get('nombre') or item.get('test') or item.get('nombre_tipo_examen') or ''
                    t_v = item.get('resultado') or item.get('estado') or ''
                    k_clean = str(t_k).strip().lower()
                    canon_k = ALIAS_TESTS.get(k_clean, k_clean)
                    val_clean = str(t_v).strip().lower()
                    if val_clean in ('positivo', 'pos', 'reactivo', '+', 'true', '1'):
                        tests_normalizados[canon_k] = 'positivo'
                    elif val_clean in ('negativo', 'neg', 'no reactivo', '-', 'false', '0'):
                        tests_normalizados[canon_k] = 'negativo'
                    else:
                        tests_normalizados[canon_k] = 'no_realizado'

        # 5. Exámenes clínicos
        examenes_normalizados = {}
        if isinstance(examenes_raw, dict):
            for e_k, e_v in examenes_raw.items():
                k_clean = str(e_k).strip().lower()
                canon_k = ALIAS_EXAMENES.get(k_clean, k_clean)
                if isinstance(e_v, dict):
                    estado_str = str(e_v.get('estado') or e_v.get('resultado') or '').strip().lower()
                    hallazgos_str = str(e_v.get('hallazgos') or e_v.get('descripcion') or e_v.get('descripcion_hallazgos') or '')
                else:
                    estado_str = str(e_v).strip().lower()
                    hallazgos_str = ''

                if estado_str in ('alterado', 'positivo', 'anormal', 'reactivo', 'patologico', '+'):
                    est_norm = 'alterado'
                elif estado_str in ('normal', 'negativo', 'sin hallazgos', '-'):
                    est_norm = 'normal'
                else:
                    est_norm = 'no_realizado'

                examenes_normalizados[canon_k] = {
                    'estado': est_norm,
                    'hallazgos': hallazgos_str
                }
        elif isinstance(examenes_raw, list):
            for item in examenes_raw:
                if isinstance(item, dict):
                    e_k = item.get('nombre') or item.get('examen') or item.get('nombre_tipo_examen') or ''
                    k_clean = str(e_k).strip().lower()
                    canon_k = ALIAS_EXAMENES.get(k_clean, k_clean)
                    estado_str = str(item.get('estado') or item.get('resultado') or '').strip().lower()
                    hallazgos_str = str(item.get('hallazgos') or item.get('descripcion') or item.get('descripcion_hallazgos') or '')
                    if estado_str in ('alterado', 'positivo', 'anormal', 'reactivo', 'patologico', '+'):
                        est_norm = 'alterado'
                    elif estado_str in ('normal', 'negativo', 'sin hallazgos', '-'):
                        est_norm = 'normal'
                    else:
                        est_norm = 'no_realizado'

                    examenes_normalizados[canon_k] = {
                        'estado': est_norm,
                        'hallazgos': hallazgos_str
                    }

        return {
            'especie': especie,
            'etapa': etapa,
            'signos': signos_normalizados,
            'tests_rapidos': tests_normalizados,
            'examenes_clinicos': examenes_normalizados
        }

    def predecir(
        self,
        especie: str,
        edad_etapa: Any = 'adulto',
        signos: Any = None,
        tests_rapidos: Any = None,
        examenes_clinicos: Any = None,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        Ejecuta la inferencia diagnóstica completa y genera la estructura de respuesta clínica.
        """
        top_k = max(3, min(top_k or 5, 5))

        # 1. Normalizar entradas
        caso_norm = self.normalizar_entradas(
            especie_raw=especie,
            edad_etapa_raw=edad_etapa,
            signos_raw=signos or [],
            tests_raw=tests_rapidos or {},
            examenes_raw=examenes_clinicos or {}
        )

        especie_final = caso_norm['especie']
        tests_dict = caso_norm['tests_rapidos']
        examenes_dict = caso_norm['examenes_clinicos']
        signos_set = set(caso_norm['signos'])

        # 2. Vectorizar caso
        vec = vectorizar_caso_clinico(caso_norm)
        vec_2d = vec.reshape(1, -1)

        # 3. Predicción probabilística por scikit-learn Random Forest
        probabilidades = self.model.predict_proba(vec_2d)[0]
        clases = self.classes

        # 4. Post-procesamiento clínico de probabilidades (Filtros de especie y reglas patognomónicas)
        prob_dict = {}
        for idx, clase in enumerate(clases):
            p = float(probabilidades[idx])
            perfil = self.knowledge.get(clase, {})
            esp_perfil = perfil.get('especie', 'ambos')

            # Filtro 1: Incompatibilidad biológica estricta de especie
            if especie_final == 'canino' and esp_perfil == 'felino':
                prob_dict[clase] = 0.0
                continue
            elif especie_final == 'felino' and esp_perfil == 'canino':
                prob_dict[clase] = 0.0
                continue

            # Filtro 2: Tests rápidos negativos
            # Si un test rápido específico asociado dio NEGATIVO, descartar o penalizar severamente
            test_asoc = perfil.get('test_asociado', {})
            for t_k, t_val_esperado in test_asoc.items():
                resultado_paciente = tests_dict.get(t_k, 'no_realizado')
                if t_val_esperado == 'positivo' and resultado_paciente == 'negativo':
                    p *= 0.02  # Casi nula probabilidad si el test confirmatorio fue negativo

            # Filtro 3: Tests rápidos positivos
            # Si el test rápido dio POSITIVO y coincide la especie, amplificar la probabilidad
            for t_k, t_val_esperado in test_asoc.items():
                resultado_paciente = tests_dict.get(t_k, 'no_realizado')
                if t_val_esperado == 'positivo' and resultado_paciente == 'positivo':
                    p = max(p * 3.0, 0.90)

            # Filtro 4: Coincidencia de signos patognomónicos clave
            signos_clave = set(perfil.get('signos_clave', []))
            coincidentes = signos_clave.intersection(signos_set)
            if coincidentes:
                frac = len(coincidentes) / max(1, len(signos_clave))
                p *= (1.0 + 0.5 * frac)

            prob_dict[clase] = p


        # Normalizar probabilidades sumando 1.0 (si suma > 0)
        total_p = sum(prob_dict.values())
        if total_p > 0:
            for k in prob_dict:
                prob_dict[k] = prob_dict[k] / total_p
        else:
            # Si todo cayó a 0 por filtro, fallback equitativo entre las compatibles con la especie
            compatibles = [c for c in clases if self.knowledge.get(c, {}).get('especie') in (especie_final, 'ambos')]
            for c in compatibles:
                prob_dict[c] = 1.0 / len(compatibles)

        # 5. Ordenar patologías por probabilidad descendente
        ranking = sorted(prob_dict.items(), key=lambda x: x[1], reverse=True)
        top_diagnosticos_raw = ranking[:top_k]

        # 6. Construir lista de diagnósticos sugeridos con justificaciones detalladas
        diagnosticos_sugeridos = []
        examenes_recomendados_set = {}

        for clase_nom, prob in top_diagnosticos_raw:
            perfil = self.knowledge.get(clase_nom, {})
            id_patologia = perfil.get('id_patologia')
            tipo_enfermedad = perfil.get('tipo', 'General')
            justificacion_base = perfil.get('justificacion', '')

            # Personalizar justificación según los hallazgos reales ingresados
            signos_clave = perfil.get('signos_clave', [])
            signos_presentes_coincidentes = [s for s in signos_clave if s in signos_set]
            
            explicacion_detallada = justificacion_base
            detalles_partes = []
            if signos_presentes_coincidentes:
                detalles_partes.append(f"Signos coincidentes: {', '.join(signos_presentes_coincidentes)}")

            # Exámenes o tests que apoyaron
            test_asoc = perfil.get('test_asociado', {})
            for t_k, t_val in test_asoc.items():
                if tests_dict.get(t_k) == t_val:
                    t_nombre_humano = NOMBRES_HUMANOS_TESTS.get(t_k, t_k)
                    detalles_partes.append(f"{t_nombre_humano} ({t_val})")

            for e_k, e_val in perfil.get('examenes_tipicos', {}).items():
                ex_info = examenes_dict.get(e_k, {})
                if ex_info.get('estado') == e_val:
                    e_nombre_humano = NOMBRES_HUMANOS_EXAMENES.get(e_k, e_k)
                    detalles_partes.append(f"{e_nombre_humano} ({e_val})")

            if detalles_partes:
                explicacion_detallada += f" [Hallazgos: {'; '.join(detalles_partes)}]."

            diagnosticos_sugeridos.append({
                'id_patologia': int(id_patologia) if id_patologia is not None else None,
                'nombre': str(clase_nom),
                'tipo_de_enfermedad': str(tipo_enfermedad),
                'probabilidad': float(round(prob, 4)),
                'porcentaje_confianza': f"{prob * 100:.1f}%",
                'justificacion': str(explicacion_detallada)
            })

            # Recolectar recomendaciones de exámenes si aún no han sido realizados
            for rec in perfil.get('examenes_recomendados', []):
                nom_ex = rec['examen']
                alias_t = ALIAS_TESTS.get(nom_ex.lower(), nom_ex.lower())
                alias_e = ALIAS_EXAMENES.get(nom_ex.lower(), nom_ex.lower())

                # Descartar si el test no aplica a la especie actual
                if especie_final == 'canino' and any(w in nom_ex.lower() for w in ('felino', 'leucemia', 'sida', 'fiv', 'felv')):
                    continue
                if especie_final == 'felino' and any(w in nom_ex.lower() for w in ('canino', 'distemper', 'moquillo')):
                    continue

                # Verificar si ya fue realizado en el paciente actual
                ya_realizado = False
                if alias_t in tests_dict and tests_dict[alias_t] != 'no_realizado':
                    ya_realizado = True
                if alias_e in examenes_dict and examenes_dict[alias_e].get('estado') != 'no_realizado':
                    ya_realizado = True

                if not ya_realizado and nom_ex not in examenes_recomendados_set:
                    examenes_recomendados_set[nom_ex] = rec


        # Priorizar exámenes recomendados (Crítica > Alta > Media > Baja)
        orden_prioridad = {'Crítica': 1, 'Alta': 2, 'Media': 3, 'Baja': 4}
        lista_recomendaciones = list(examenes_recomendados_set.values())
        lista_recomendaciones.sort(key=lambda x: orden_prioridad.get(x.get('prioridad', 'Media'), 3))

        # 7. Redactar resumen clínico coherente
        if diagnosticos_sugeridos:
            diag_top = diagnosticos_sugeridos[0]
            conf_str = diag_top['porcentaje_confianza']
            nom_top = diag_top['nombre']

            otros_diag = [d['nombre'] for d in diagnosticos_sugeridos[1:3]]
            otros_str = f", seguido como diagnóstico diferencial de {', '.join(otros_diag)}" if otros_diag else ""

            resumen_clinico = (
                f"El análisis clínico computacional orienta con una confianza estimada de {conf_str} hacia {nom_top}{otros_str}. "
                f"Esta sugerencia se fundamenta en la especie ({especie_final}), etapa de vida ({caso_norm['etapa']}) "
                f"y la concordancia con los signos y exámenes reportados. "
                f"Se recomienda contrastar con las pruebas complementarias sugeridas antes de iniciar tratamiento definitivo."
            )
        else:
            resumen_clinico = "No se encontraron correlaciones patológicas con suficiente confianza para los signos ingresados."

        return {
            'aviso_legal': (
                "Este módulo de inteligencia artificial local ofrece sugerencias de carácter exclusivamente "
                "orientativo y de apoyo a la decisión clínica veterinaria. La determinación diagnóstica definitiva "
                "y conducta terapéutica corresponden de manera privativa al médico veterinario tratante."
            ),
            'diagnosticos_sugeridos': diagnosticos_sugeridos,
            'examenes_recomendados': lista_recomendaciones,
            'resumen_clinico': resumen_clinico,
            'contexto_procesado': {
                'especie': especie_final,
                'etapa': caso_norm['etapa'],
                'signos_detectados': caso_norm['signos'],
                'tests_evaluados': {k: v for k, v in tests_dict.items() if v != 'no_realizado'},
                'examenes_evaluados': {k: v['estado'] for k, v in examenes_dict.items() if v['estado'] != 'no_realizado'},
            },
            'modelo_info': {
                'version': self.metadata.get('version', '1.0.0'),
                'algoritmo': self.metadata.get('algoritmo', 'RandomForestClassifier (scikit-learn)'),
                'modo_ejecucion': 'Inferencia local en backend (disponible de forma remota vía API)',
                'total_patologias_evaluadas': len(self.classes)
            }
        }


def obtener_motor_diagnostico() -> MotorDiagnosticoIA:
    """Retorna la instancia Singleton del motor diagnóstico para optimizar memoria y tiempo de respuesta."""
    global _MOTOR_SINGLETON
    if _MOTOR_SINGLETON is None:
        _MOTOR_SINGLETON = MotorDiagnosticoIA()
    return _MOTOR_SINGLETON
