"""
Entrenador del modelo ligero de Random Forest para apoyo diagnóstico veterinario.
Guarda el modelo entrenado, metadatos y mapeos en un archivo local .joblib.
"""

import os
import joblib
from datetime import datetime
from typing import Dict, Any

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

from diagnostico_ia.ml.dataset_generator import generar_dataset_sintetico, FEATURE_NAMES
from diagnostico_ia.ml.knowledge_base import PATOLOGIAS_KNOWLEDGE

MODEL_PATH = os.path.join(os.path.dirname(__file__), 'modelo_diagnostico.joblib')


def entrenar_y_guardar_modelo(casos_por_patologia: int = 150, verbose: bool = True) -> Dict[str, Any]:
    """
    Entrena el clasificador Random Forest con el dataset sintético clínico y
    persiste el paquete serializado en disk (.joblib).
    """
    if verbose:
        print("[Diagnóstico IA] Generando dataset clínico sintético canino y felino...")

    X, y, feature_names = generar_dataset_sintetico(casos_por_patologia=casos_por_patologia)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )

    if verbose:
        print(f"[Diagnóstico IA] Entrenando Random Forest con {X.shape[0]} casos y {X.shape[1]} características...")

    clf = RandomForestClassifier(
        n_estimators=120,
        max_depth=25,
        min_samples_split=3,
        random_state=42,
        class_weight='balanced',
        n_jobs=-1
    )

    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)

    if verbose:
        print(f"[Diagnóstico IA] Exactitud de validación (Accuracy): {acc * 100:.2f}%")

    # Re-entrenar con todo el dataset para máxima robustez en producción
    clf.fit(X, y)

    paquete_modelo = {
        'model': clf,
        'classes': list(clf.classes_),
        'feature_names': feature_names,
        'knowledge_base': PATOLOGIAS_KNOWLEDGE,
        'metadata': {
            'version': '1.0.0',
            'fecha_entrenamiento': datetime.now().isoformat(),
            'total_muestras': int(X.shape[0]),
            'total_caracteristicas': int(X.shape[1]),
            'total_patologias': len(clf.classes_),
            'exactitud_validacion': float(acc),
            'algoritmo': 'RandomForestClassifier (scikit-learn)'
        }
    }

    # Guardar en archivo local
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(paquete_modelo, MODEL_PATH, compress=3)

    if verbose:
        tamano_mb = os.path.getsize(MODEL_PATH) / (1024 * 1024)
        print(f"[Diagnóstico IA] Modelo guardado con éxito en: {MODEL_PATH} ({tamano_mb:.2f} MB)")

    return paquete_modelo


if __name__ == '__main__':
    entrenar_y_guardar_modelo(casos_por_patologia=150, verbose=True)
