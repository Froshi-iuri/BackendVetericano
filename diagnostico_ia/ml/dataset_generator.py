"""
Generador de dataset sintético clínico veterinario coherente para caninos y felinos.
Genera miles de casos clínicos simulados basados en la base de conocimiento veterinario,
con variabilidad realista en signos clínicos, tests rápidos y exámenes complementarios.
"""

import random
from typing import List, Dict, Any, Tuple
import numpy as np

from diagnostico_ia.ml.knowledge_base import (
    TESTS_RAPIDOS_CANONICOS,
    EXAMENES_CLINICOS_CANONICOS,
    SIGNOS_CLINICOS_CANONICOS,
    ETAPAS_VIDA,
    PATOLOGIAS_KNOWLEDGE,
)


def construir_nombres_caracteristicas() -> List[str]:
    """
    Retorna la lista ordenada de nombres de todas las características (features)
    que utiliza el modelo vectorial de scikit-learn.
    """
    features = [
        'especie_canino',
        'especie_felino',
        'etapa_cachorro_gatito',
        'etapa_joven',
        'etapa_adulto',
        'etapa_senior',
    ]

    # Signos clínicos binarios
    for signo in SIGNOS_CLINICOS_CANONICOS:
        features.append(f'signo_{signo}')

    # Tests rápidos: positivo (+1), negativo (-1), no_realizado (0)
    # Codificados como dos columnas binarias por test: _pos y _neg
    for test in TESTS_RAPIDOS_CANONICOS:
        features.append(f'{test}_pos')
        features.append(f'{test}_neg')

    # Exámenes clínicos: alterado (+1), normal (-1), no_realizado (0)
    for examen in EXAMENES_CLINICOS_CANONICOS:
        features.append(f'{examen}_alt')
        features.append(f'{examen}_nor')

    # Hallazgos textuales clave detectados
    tokens_hallazgos = [
        'hallazgo_leucopenia',
        'hallazgo_leucocitosis',
        'hallazgo_anemia',
        'hallazgo_azotemia_creatinina',
        'hallazgo_hiperglucemia',
        'hallazgo_cristales_urolitos',
        'hallazgo_efusion_liquido',
        'hallazgo_quistes_parasitos',
        'hallazgo_acaros_hongos',
        'hallazgo_cardiomegalia_soplo',
        'hallazgo_timpanismo_vólvulo',
        'hallazgo_lesion_corneal'
    ]
    features.extend(tokens_hallazgos)

    return features


FEATURE_NAMES = construir_nombres_caracteristicas()
FEATURE_INDEX_MAP = {name: idx for idx, name in enumerate(FEATURE_NAMES)}


def vectorizar_caso_clinico(caso: Dict[str, Any]) -> np.ndarray:
    """
    Convierte un diccionario con datos de un caso clínico en un vector numérico 1D
    compatible con el modelo scikit-learn.
    """
    vec = np.zeros(len(FEATURE_NAMES), dtype=np.float32)

    # 1. Especie
    esp = str(caso.get('especie', '')).strip().lower()
    if esp in ('canino', 'perro', 'dog'):
        vec[FEATURE_INDEX_MAP['especie_canino']] = 1.0
    elif esp in ('felino', 'gato', 'cat'):
        vec[FEATURE_INDEX_MAP['especie_felino']] = 1.0
    else:
        # Por defecto si no especifica, equiprobable
        vec[FEATURE_INDEX_MAP['especie_canino']] = 0.5
        vec[FEATURE_INDEX_MAP['especie_felino']] = 0.5

    # 2. Etapa de vida
    etapa = str(caso.get('etapa', '')).strip().lower()
    if any(k in etapa for k in ('cachorro', 'gatito', 'bebe', 'puppy', 'kitten', '<1')):
        vec[FEATURE_INDEX_MAP['etapa_cachorro_gatito']] = 1.0
    elif any(k in etapa for k in ('joven', 'young', '1 a 3')):
        vec[FEATURE_INDEX_MAP['etapa_joven']] = 1.0
    elif any(k in etapa for k in ('adulto', 'adult')):
        vec[FEATURE_INDEX_MAP['etapa_adulto']] = 1.0
    elif any(k in etapa for k in ('senior', 'geronte', 'anciano', 'viejo', '>7')):
        vec[FEATURE_INDEX_MAP['etapa_senior']] = 1.0
    else:
        vec[FEATURE_INDEX_MAP['etapa_adulto']] = 1.0

    # 3. Signos clínicos
    signos_presentes = caso.get('signos', [])
    if isinstance(signos_presentes, str):
        signos_presentes = [s.strip().lower() for s in signos_presentes.replace(';', ',').split(',') if s.strip()]

    for s in signos_presentes:
        s_norm = str(s).strip().lower()
        feature_key = f'signo_{s_norm}'
        if feature_key in FEATURE_INDEX_MAP:
            vec[FEATURE_INDEX_MAP[feature_key]] = 1.0

    # 4. Tests rápidos
    tests = caso.get('tests_rapidos', {})
    for test_name, estado in tests.items():
        t_key = test_name.strip().lower()
        est_val = str(estado).strip().lower()
        if f'{t_key}_pos' in FEATURE_INDEX_MAP and f'{t_key}_neg' in FEATURE_INDEX_MAP:
            if est_val in ('positivo', 'pos', 'reactivo', '+', 'true', '1'):
                vec[FEATURE_INDEX_MAP[f'{t_key}_pos']] = 1.0
            elif est_val in ('negativo', 'neg', 'no reactivo', '-', 'false', '0'):
                vec[FEATURE_INDEX_MAP[f'{t_key}_neg']] = 1.0

    # 5. Exámenes clínicos
    examenes = caso.get('examenes_clinicos', {})
    for exam_name, exam_data in examenes.items():
        e_key = exam_name.strip().lower()
        estado = ''
        hallazgos = ''
        if isinstance(exam_data, dict):
            estado = str(exam_data.get('estado', '')).strip().lower()
            hallazgos = str(exam_data.get('hallazgos', '') or exam_data.get('descripcion', '')).lower()
        elif isinstance(exam_data, str):
            estado = exam_data.strip().lower()

        if f'{e_key}_alt' in FEATURE_INDEX_MAP and f'{e_key}_nor' in FEATURE_INDEX_MAP:
            if estado in ('alterado', 'positivo', 'anormal', 'reactivo', 'patologico', '+'):
                vec[FEATURE_INDEX_MAP[f'{e_key}_alt']] = 1.0
            elif estado in ('normal', 'negativo', 'sin hallazgos', '-'):
                vec[FEATURE_INDEX_MAP[f'{e_key}_nor']] = 1.0

        # Mapear hallazgos clínicos de texto a tokens
        texto_comb = f"{estado} {hallazgos}".lower()
        if any(w in texto_comb for w in ('leucopenia', 'linfopenia', 'neutropenia', 'leucos bajos')):
            vec[FEATURE_INDEX_MAP['hallazgo_leucopenia']] = 1.0
        if any(w in texto_comb for w in ('leucocitosis', 'neutrofilia', 'desvio izquierda')):
            vec[FEATURE_INDEX_MAP['hallazgo_leucocitosis']] = 1.0
        if any(w in texto_comb for w in ('anemia', 'hematocrito bajo', 'palidez')):
            vec[FEATURE_INDEX_MAP['hallazgo_anemia']] = 1.0
        if any(w in texto_comb for w in ('creatinina', 'urea', 'bun', 'azotemia', 'renal')):
            vec[FEATURE_INDEX_MAP['hallazgo_azotemia_creatinina']] = 1.0
        if any(w in texto_comb for w in ('glucosa', 'glucosuria', 'hiperglucemia', 'azucar')):
            vec[FEATURE_INDEX_MAP['hallazgo_hiperglucemia']] = 1.0
        if any(w in texto_comb for w in ('cristales', 'estruvita', 'oxalato', 'lito', 'calculo')):
            vec[FEATURE_INDEX_MAP['hallazgo_cristales_urolitos']] = 1.0
        if any(w in texto_comb for w in ('efusion', 'ascitis', 'rivalta', 'liquido libre')):
            vec[FEATURE_INDEX_MAP['hallazgo_efusion_liquido']] = 1.0
        if any(w in texto_comb for w in ('giardia', 'quistes', 'parasito', 'huevos', 'trofozoito')):
            vec[FEATURE_INDEX_MAP['hallazgo_quistes_parasitos']] = 1.0
        if any(w in texto_comb for w in ('sarcoptes', 'demodex', 'acaros', 'malassezia', 'tiña', 'microsporum')):
            vec[FEATURE_INDEX_MAP['hallazgo_acaros_hongos']] = 1.0
        if any(w in texto_comb for w in ('cardiomegalia', 'soplo', 'edema pulmonar', 'insuficiencia cardiaca')):
            vec[FEATURE_INDEX_MAP['hallazgo_cardiomegalia_soplo']] = 1.0
        if any(w in texto_comb for w in ('dilatacion gastrica', 'volvulo', 'timpanismo', 'doble burbuja')):
            vec[FEATURE_INDEX_MAP['hallazgo_timpanismo_vólvulo']] = 1.0
        if any(w in texto_comb for w in ('fluoresceina', 'ulcera corneal', 'desprendimiento', 'blefarospasmo')):
            vec[FEATURE_INDEX_MAP['hallazgo_lesion_corneal']] = 1.0

    return vec


def generar_dataset_sintetico(casos_por_patologia: int = 120, seed: int = 42) -> Tuple[np.ndarray, List[str], List[str]]:
    """
    Genera una matriz numpy X con características vectorizadas y una lista y con
    las patologías asignadas según perfiles clínicos coherentes.
    """
    random.seed(seed)
    np.random.seed(seed)

    X_list = []
    y_list = []

    for nombre_patologia, perfil in PATOLOGIAS_KNOWLEDGE.items():
        esp_perfil = perfil['especie']
        etapas_perfil = perfil['etapas']
        signos_clave = perfil['signos_clave']
        signos_sec = perfil['signos_secundarios']
        test_asociado = perfil.get('test_asociado', {})
        examenes_tipicos = perfil.get('examenes_tipicos', {})
        hallazgos_clave = perfil.get('hallazgos_clave', [])

        for i in range(casos_por_patologia):
            # 1. Determinar especie
            if esp_perfil == 'canino':
                especie = 'canino'
            elif esp_perfil == 'felino':
                especie = 'felino'
            else:
                especie = random.choice(['canino', 'felino'])

            # 2. Determinar etapa
            etapa = random.choice(etapas_perfil)

            # 3. Determinar signos clínicos (variabilidad clínica)
            signos_caso = set()
            # De 70% a 100% de los signos clave están presentes
            k_clave = max(1, int(len(signos_clave) * random.uniform(0.65, 1.0)))
            signos_caso.update(random.sample(signos_clave, k_clave))

            # Añadir 0 a 2 signos secundarios
            if signos_sec:
                num_sec = random.randint(0, min(2, len(signos_sec)))
                signos_caso.update(random.sample(signos_sec, num_sec))

            # Añadir ruido leve en 10% de los casos (signo general común como letargia o anorexia)
            if random.random() < 0.15:
                signos_caso.add(random.choice(['letargia', 'anorexia']))

            # 4. Determinar tests rápidos
            tests_caso = {}
            for t_nom in TESTS_RAPIDOS_CANONICOS:
                if t_nom in test_asociado:
                    # En el 85% de los casos el test asociado se ha realizado y da el valor esperado
                    if random.random() < 0.85:
                        tests_caso[t_nom] = test_asociado[t_nom]
                    else:
                        # En 15% de casos aún no se ha realizado
                        tests_caso[t_nom] = 'no_realizado'
                else:
                    # Otros tests: 70% no realizado, 30% negativo
                    if random.random() < 0.30:
                        tests_caso[t_nom] = 'negativo'
                    else:
                        tests_caso[t_nom] = 'no_realizado'

            # 5. Determinar exámenes clínicos
            examenes_caso = {}
            for e_nom in EXAMENES_CLINICOS_CANONICOS:
                if e_nom in examenes_tipicos:
                    # En el 80% de los casos el examen típico concuerda
                    if random.random() < 0.80:
                        estado_e = examenes_tipicos[e_nom]
                        hallazgo_txt = random.choice(hallazgos_clave) if hallazgos_clave and estado_e in ('alterado', 'positivo') else ''
                        examenes_caso[e_nom] = {'estado': estado_e, 'hallazgos': hallazgo_txt}
                    else:
                        examenes_caso[e_nom] = {'estado': 'no_realizado', 'hallazgos': ''}
                else:
                    # Para exámenes no típicos: mayoría no realizado, algunos normales
                    r = random.random()
                    if r < 0.25:
                        examenes_caso[e_nom] = {'estado': 'normal', 'hallazgos': ''}
                    else:
                        examenes_caso[e_nom] = {'estado': 'no_realizado', 'hallazgos': ''}

            caso_dict = {
                'especie': especie,
                'etapa': etapa,
                'signos': list(signos_caso),
                'tests_rapidos': tests_caso,
                'examenes_clinicos': examenes_caso
            }

            vec = vectorizar_caso_clinico(caso_dict)
            X_list.append(vec)
            y_list.append(nombre_patologia)

    X = np.array(X_list, dtype=np.float32)
    return X, y_list, FEATURE_NAMES
