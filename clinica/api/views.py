from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from clinica.models import (
    HistoriaClinica,
    Consulta,
    HospitalizacionSeresSintientes,
    Tratamientos,
    Diagnostico,
    SeguimientosClinicos,
    DiagnosticoPatologia,
    SeguimientoHospitalario,
)
from .serializers import (
    HistoriaClinicaSerializer,
    ConsultaSerializer,
    HospitalizacionSeresSintientesSerializer,
    TratamientosSerializer,
    DiagnosticoSerializer,
    SeguimientosClinicosSerializer,
    DiagnosticoPatologiaSerializer,
    SeguimientoHospitalarioSerializer,
)


class HistoriaClinicaViewSet(viewsets.ModelViewSet):
    queryset = HistoriaClinica.objects.all()
    serializer_class = HistoriaClinicaSerializer
    permission_classes = [IsAuthenticated]


class ConsultaViewSet(viewsets.ModelViewSet):
    queryset = Consulta.objects.all()
    serializer_class = ConsultaSerializer
    permission_classes = [IsAuthenticated]


class HospitalizacionSeresSintientesViewSet(viewsets.ModelViewSet):
    queryset = HospitalizacionSeresSintientes.objects.all()
    serializer_class = HospitalizacionSeresSintientesSerializer
    permission_classes = [IsAuthenticated]


class TratamientosViewSet(viewsets.ModelViewSet):
    queryset = Tratamientos.objects.all()
    serializer_class = TratamientosSerializer
    permission_classes = [IsAuthenticated]


class DiagnosticoViewSet(viewsets.ModelViewSet):
    queryset = Diagnostico.objects.all()
    serializer_class = DiagnosticoSerializer
    permission_classes = [IsAuthenticated]


class SeguimientosClinicosViewSet(viewsets.ModelViewSet):
    queryset = SeguimientosClinicos.objects.all()
    serializer_class = SeguimientosClinicosSerializer
    permission_classes = [IsAuthenticated]


class DiagnosticoPatologiaViewSet(viewsets.ModelViewSet):
    queryset = DiagnosticoPatologia.objects.all()
    serializer_class = DiagnosticoPatologiaSerializer
    permission_classes = [IsAuthenticated]


class SeguimientoHospitalarioViewSet(viewsets.ModelViewSet):
    queryset = SeguimientoHospitalario.objects.all()
    serializer_class = SeguimientoHospitalarioSerializer
    permission_classes = [IsAuthenticated]


from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import ExpedienteCompletoSerializer

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

class ExpedienteCompletoAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Obtiene un expediente médico clínico completo mediante el ID de la consulta, devolviendo la misma estructura anidada del POST.",
        responses={200: ExpedienteCompletoSerializer()}
    )
    def get(self, request, id_consulta, *args, **kwargs):
        from django.shortcuts import get_object_or_404
        
        consulta = get_object_or_404(
            Consulta.objects.select_related(
                'id_historia',
                'id_historia__id_animal',
                'id_historia__id_animal__id_raza',
                'id_historia__id_animal__id_raza__id_especie'
            ).prefetch_related(
                'tratamientos_set',
                'diagnostico_set',
                'id_historia__id_animal__animalresponsable_set__id_responsable'
            ),
            pk=id_consulta
        )

        historia = consulta.id_historia
        animal = historia.id_animal if historia else None

        # Tenedor / Responsable
        tenedor_data = {}
        if animal:
            relacion = animal.animalresponsable_set.filter(tipo_responsabilidad='Tenedor').last()
            if not relacion:
                relacion = animal.animalresponsable_set.last()
            
            if relacion and relacion.id_responsable:
                resp = relacion.id_responsable
                tenedor_data = {
                    "documentoIdentidad": resp.documento or "",
                    "nombres": resp.nombre or "",
                    "telefono": resp.telefono or "",
                    "correoElectronico": resp.correo or "",
                    "direccion": resp.direccion or "",
                }

        # Animal Data
        animal_data = {}
        if animal:
            animal_data = {
                "especie": animal.id_raza.id_especie.nombre if (animal.id_raza and animal.id_raza.id_especie) else "Desconocida",
                "raza": animal.id_raza.nombre if animal.id_raza else "Mestizo",
                "nombre": animal.nombre or "",
                "sexo": animal.sexo or "",
                "color": animal.color or "",
                "peso": float(animal.peso) if animal.peso else None,
                "descripcionBreve": animal.observaciones or "",
                "caracteristicasEspeciales": animal.caracteristicas or "",
                "fotoUrl": animal.foto_url or "",
                "numeroHistoriaClinica": historia.numero_historia or ""
            }

        # Anamnesicos
        anamnesicos_data = {
            "ultimaDesparasitacion": historia.fecha_ultima_desparasitacion.isoformat() if historia.fecha_ultima_desparasitacion else "",
            "vacunas": historia.vacunas or "",
            "enfermedadesAnteriores": historia.enfermedades_anteriores or "",
            "tratamientos": historia.tratamientos_anteriores or "",
            "evolucion": historia.evolucion_previa or "",
            "alimentacion": historia.alimentacion or "",
            "historiaReproductiva": historia.estado_reproductivo or "",
            "ultimoCelo": historia.fecha_ultimo_celo.isoformat() if historia.fecha_ultimo_celo else "",
            "fechaUltimoParto": historia.fecha_ultimo_parto.isoformat() if historia.fecha_ultimo_parto else ""
        }

        # Examen Clínico
        examen_data = {
            "frecuenciaRespiratoria": consulta.frecuencia_respiratoria,
            "frecuenciaCardiaca": consulta.frecuencia_cardiaca,
            "temperatura": float(consulta.temperatura) if consulta.temperatura else None,
            "pulso": consulta.pulso or "",
            "tiempoLlenadoCapilar": consulta.tllc or "",
            "gangliosLinfaticos": consulta.ganglios_linfaticos or "",
            "mucosas": consulta.mucosas or "",
            "actitudTemperamento": consulta.actitud_temperamento or ""
        }

        # Diagnósticos y Tratamientos
        diagnostico_obj = consulta.diagnostico_set.last()
        lista_problemas = diagnostico_obj.lista_problemas.split('; ') if diagnostico_obj and diagnostico_obj.lista_problemas else []
        diagnostico_final = diagnostico_obj.descripcion if diagnostico_obj else ""

        tratamientos_data = [
            {
                "productoBase": t.producto_base or "",
                "dosisBasica": t.dosis_basica or "",
                "presentacion": t.presentacion or "",
                "via": t.via_administracion or "",
                "frecuenciaDuracion": t.frecuencia_duracion or ""
            }
            for t in consulta.tratamientos_set.all()
        ]

        # Hoja 2
        hoja2_data = {
            "descripcionHallazgos": consulta.hallazgos_examen or "",
            "materialesUtilizados": consulta.materiales_utilizados or "",
            "planProcedimientos": consulta.plan_procedimientos or "",
            "pronostico": consulta.pronostico or "",
            "examenes": consulta.examenes_complementarios or {},
            "listaProblemas": lista_problemas,
            "diagnosticoFinal": diagnostico_final,
            "tratamientos": tratamientos_data
        }

        # Hoja 3
        hoja3_data = {
            "ingresaCBA": "SI" if consulta.ingresa_cba else "NO",
            "motivoIngresoNoIngreso": consulta.motivo_ingreso_cba or "",
            "canilAsignado": consulta.canil_asignado or "",
            "funcionarioRecibeCBA": consulta.funcionario_recibe_cba or ""
        }

        # Estructura Final (Simetría con el POST)
        datos = {
            "animal": animal_data,
            "tenedor": tenedor_data,
            "anamnesicos": anamnesicos_data,
            "motivoConsulta": consulta.motivo_consulta or "",
            "examenClinico": examen_data,
            "organosSistemas": consulta.evaluacion_sistemas or {},
            "hoja2": hoja2_data,
            "hoja3": hoja3_data,
            "rescate": {}
        }

        return Response(datos, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = ExpedienteCompletoSerializer(data=request.data)
        if serializer.is_valid():
            result = serializer.save()
            return Response(result, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
