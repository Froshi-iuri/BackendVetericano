"""
Vistas API REST para el módulo de Apoyo Diagnóstico IA Local.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from users.api.permissions import PermisoClinica
from diagnostico_ia.serializers import DiagnosticoIAPrediccionInputSerializer
from diagnostico_ia.ml.knowledge_base import (
    NOMBRES_HUMANOS_TESTS,
    NOMBRES_HUMANOS_EXAMENES,
    SIGNOS_CLINICOS_CANONICOS,
    PATOLOGIAS_KNOWLEDGE,
)


def obtener_motor_diagnostico():
    from diagnostico_ia.ml.engine import obtener_motor_diagnostico as _obtener
    return _obtener()


class DiagnosticoIAPredictView(APIView):
    """
    Endpoint principal para sugerencia diagnóstica orientativa con IA local.
    Recibe la especie, etapa de vida, signos clínicos y resultados de exámenes o tests rápidos.
    Retorna el top 3 a 5 de patologías probables ordenadas por confianza,
    exámenes complementarios recomendados y justificación clínica de los hallazgos.
    """
    permission_classes = [PermisoClinica]

    @swagger_auto_schema(
        operation_summary="Sugerir patologías y exámenes con IA Local",
        operation_description=(
            "Analiza el contexto clínico del canino o felino (signos, tests rápidos y exámenes clínicos) "
            "mediante un modelo ligero local entrenado (scikit-learn RandomForest) y entrega recomendaciones "
            "orientativas sin depender de servicios externos."
        ),
        request_body=DiagnosticoIAPrediccionInputSerializer,
        responses={
            200: openapi.Response(
                description="Predicción diagnóstica exitosa con sugerencias, justificaciones y exámenes recomendados.",
                examples={
                    "application/json": {
                        "aviso_legal": "Este módulo de IA local ofrece sugerencias orientativas y de apoyo clínico...",
                        "diagnosticos_sugeridos": [
                            {
                                "id_patologia": 9,
                                "nombre": "Parvovirosis Canina (CPV-2)",
                                "tipo_de_enfermedad": "Infecciosa",
                                "probabilidad": 0.942,
                                "porcentaje_confianza": "94.2%",
                                "justificacion": "Cuadro agudo gastroentérico con vómitos, diarrea fétida o hemorrágica..."
                            }
                        ],
                        "examenes_recomendados": [
                            {
                                "examen": "Cuadro Hemático",
                                "tipo": "Examen Clínico",
                                "motivo": "Monitorear leucopenia profunda y anemia secundaria.",
                                "prioridad": "Alta"
                            }
                        ],
                        "resumen_clinico": "El análisis clínico computacional orienta con una confianza de 94.2%...",
                        "contexto_procesado": {
                            "especie": "canino",
                            "etapa": "cachorro_gatito",
                            "signos_detectados": ["vomito", "diarrea_hemorragica", "fiebre"],
                            "tests_evaluados": {"test_parvovirus": "positivo"},
                            "examenes_evaluados": {}
                        }
                    }
                }
            ),
            400: "Datos de entrada inválidos o faltantes.",
            403: "Acceso no autorizado para este rol."
        }
    )
    def post(self, request, *args, **kwargs):
        serializer = DiagnosticoIAPrediccionInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"detail": "Error en los datos de entrada", "errores": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        especie = data.get('especie')
        edad_etapa = data.get('edad_etapa', 'adulto')
        signos = list(data.get('signos', []))
        tests_rapidos = dict(data.get('tests_rapidos', {}))
        examenes_clinicos = dict(data.get('examenes_clinicos', {}))
        id_consulta = data.get('id_consulta')
        id_animal = data.get('id_animal')
        top_k = data.get('top_k', 5)

        # 1. Integración opcional con registros existentes: Animal
        if id_animal:
            try:
                from animales.models import Animal
                animal = Animal.objects.select_related('id_raza__id_especie').filter(id_animal=id_animal).first()
                if animal:
                    if not especie and animal.id_raza and animal.id_raza.id_especie:
                        especie = animal.id_raza.id_especie.nombre
                    # Estimar etapa por edad si no fue enviada explícitamente
                    if (not edad_etapa or edad_etapa == 'adulto') and getattr(animal, 'edad', None):
                        edad_val = str(animal.edad).lower()
                        if any(w in edad_val for w in ('mes', 'meses', 'cachorro', 'gatito')):
                            edad_etapa = 'cachorro_gatito'
            except Exception:
                pass

        # 2. Integración opcional con registros existentes: Consulta médica previa
        if id_consulta:
            try:
                from clinica.models import Consulta
                consulta = Consulta.objects.select_related(
                    'id_historia__id_animal__id_raza__id_especie'
                ).filter(id_consulta=id_consulta).first()

                if consulta:
                    animal_c = getattr(consulta.id_historia, 'id_animal', None)
                    if animal_c and not especie and animal_c.id_raza and animal_c.id_raza.id_especie:
                        especie = animal_c.id_raza.id_especie.nombre

                    # Extraer texto de anamnesis o motivo para enriquecer signos si la lista venía vacía
                    if not signos:
                        texto_consulta = f"{consulta.motivo_consulta or ''} {consulta.anamnesis or ''} {consulta.hallazgos_examen or ''}"
                        if texto_consulta.strip():
                            signos.append(texto_consulta)

                    # Extraer exámenes complementarios guardados en JSON si existen
                    if isinstance(consulta.examenes_complementarios, dict):
                        for k, v in consulta.examenes_complementarios.items():
                            if k not in examenes_clinicos and k not in tests_rapidos:
                                examenes_clinicos[k] = v

                    # Extraer exámenes relacionados en la tabla examen si existen
                    from examenes_clinicos.models import Examen
                    examenes_db = Examen.objects.filter(id_consulta=consulta)
                    for ex in examenes_db:
                        nombre_t = ex.nombre_tipo_examen or ''
                        if nombre_t:
                            estado_e = ex.resultado or ex.estado or 'alterado'
                            hallazgo_e = ex.descripcion_hallazgos or ''
                            examenes_clinicos[nombre_t] = {
                                'estado': estado_e,
                                'hallazgos': hallazgo_e
                            }
            except Exception:
                pass

        # 3. Invocar motor diagnóstico IA local
        try:
            motor = obtener_motor_diagnostico()
            resultado = motor.predecir(
                especie=especie,
                edad_etapa=edad_etapa,
                signos=signos,
                tests_rapidos=tests_rapidos,
                examenes_clinicos=examenes_clinicos,
                top_k=top_k
            )
            return Response(resultado, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {
                    "detail": "Ocurrió un error al procesar el análisis diagnóstico computacional.",
                    "error": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DiagnosticoIAInfoView(APIView):
    """
    Endpoint informativo que lista los catálogos y capacidades del módulo IA local.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Información y catálogo del módulo de Apoyo Diagnóstico IA",
        operation_description="Retorna metadatos del modelo local, tests rápidos soportados, exámenes y catálogo de patologías."
    )
    def get(self, request, *args, **kwargs):
        motor = obtener_motor_diagnostico()
        return Response({
            'modelo_info': motor.metadata,
            'tests_rapidos_soportados': NOMBRES_HUMANOS_TESTS,
            'examenes_clinicos_soportados': NOMBRES_HUMANOS_EXAMENES,
            'signos_clinicos_reconocidos': SIGNOS_CLINICOS_CANONICOS,
            'total_patologias_entrenadas': len(motor.classes),
            'total_patologias_evaluadas': len(motor.classes),
            'patologias_catalogo': [
                {
                    'id_patologia': v.get('id_patologia'),
                    'nombre': k,
                    'tipo': v.get('tipo'),
                    'especie': v.get('especie')
                }
                for k, v in PATOLOGIAS_KNOWLEDGE.items()
            ]
        }, status=status.HTTP_200_OK)
