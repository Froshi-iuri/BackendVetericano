"""
Serializadores DRF para el módulo de Apoyo Diagnóstico IA.
"""

from rest_framework import serializers


class DiagnosticoIAPrediccionInputSerializer(serializers.Serializer):
    especie = serializers.CharField(
        required=True,
        help_text="Especie del animal: 'perro'/'canino' o 'gato'/'felino'."
    )
    edad_etapa = serializers.CharField(
        required=False,
        default='adulto',
        help_text="Edad o etapa de vida: 'cachorro'/'gatito', 'joven', 'adulto', 'senior', o texto con edad (ej. '3 meses', '6 años')."
    )
    signos = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text="Lista de signos clínicos principales (ej. ['fiebre', 'vómito', 'diarrea con sangre', 'letargia'])."
    )
    tests_rapidos = serializers.DictField(
        required=False,
        default=dict,
        help_text=(
            "Resultados de tests rápidos. Claves esperadas: 'Test Parvovirus', 'Test de Distemper (Moquillo)', "
            "'Test de Leucemia (Felinos)', 'Test de Sida (Felinos)', 'Test Coronavirus'. "
            "Valores: 'positivo', 'negativo', 'no_realizado'."
        )
    )
    examenes_clinicos = serializers.DictField(
        required=False,
        default=dict,
        help_text=(
            "Resultados de exámenes clínicos. Claves: Cuadro Hemático, Química Sanguínea, Parcial de Orina, "
            "Coprológico, Coproscópico, Frotis (raspado piel), Cultivos, Antibiograma, Biopsia, Rayos X, "
            "Ecografía, ECG, EEG, Endoscopia. "
            "Valores: string con estado ('normal', 'alterado', 'positivo', 'negativo', 'no_realizado') o "
            "objeto {'estado': 'alterado', 'hallazgos': 'texto breve de hallazgos'}."
        )
    )
    id_consulta = serializers.IntegerField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Opcional: ID de consulta médica existente para precargar automáticamente especie, signos y exámenes."
    )
    id_animal = serializers.IntegerField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Opcional: ID del animal registrado en la base de datos para autocompletar especie y edad."
    )
    top_k = serializers.IntegerField(
        required=False,
        default=5,
        min_value=3,
        max_value=5,
        help_text="Número de patologías sugeridas ordenadas por probabilidad a retornar (entre 3 y 5)."
    )
