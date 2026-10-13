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


from animales.models import Animal


class HistoriaClinicaViewSet(viewsets.ModelViewSet):
    queryset = HistoriaClinica.objects.all()
    serializer_class = HistoriaClinicaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        id_animal = self.request.query_params.get('id_animal')
        if id_animal:
            queryset = queryset.filter(id_animal_id=id_animal)
        return queryset


class ConsultaViewSet(viewsets.ModelViewSet):
    queryset = Consulta.objects.all()
    serializer_class = ConsultaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset().order_by('-fecha_hora')
        id_animal = self.request.query_params.get('id_animal')
        if id_animal:
            queryset = queryset.filter(id_historia__id_animal_id=id_animal)
        id_historia = self.request.query_params.get('id_historia')
        if id_historia:
            queryset = queryset.filter(id_historia_id=id_historia)
        return queryset


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
        operation_description="Lista todas las historias clínicas consolidadas con datos del paciente, tutor y última consulta (si no se envía id_consulta), u obtiene el expediente de una consulta específica (si se envía id_consulta en la URL).",
        manual_parameters=[
            openapi.Parameter('search', openapi.IN_QUERY, description="Buscar por nombre de animal, número de historia o nombre/documento del tutor", type=openapi.TYPE_STRING),
            openapi.Parameter('especie', openapi.IN_QUERY, description="Filtrar por nombre de especie (ej. Canino, Felino)", type=openapi.TYPE_STRING),
            openapi.Parameter('page', openapi.IN_QUERY, description="Número de página (default: 1)", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Cantidad de registros por página (default: 10)", type=openapi.TYPE_INTEGER),
        ],
        responses={200: "Listado de historias clínicas o Detalle de consulta"}
    )
    def get(self, request, id_consulta=None, *args, **kwargs):
        from django.shortcuts import get_object_or_404
        from django.db.models import Q
        from django.core.paginator import Paginator, EmptyPage

        # CASO 1: Consulta puntual específica por id_consulta
        if id_consulta is not None:
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
                    "id_animal": animal.id_animal,
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

            # Anamnésicos
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

        # CASO 2: Listado Oficial de Historias Clínicas (cuando id_consulta is None)
        queryset = HistoriaClinica.objects.select_related(
            'id_animal',
            'id_animal__id_raza',
            'id_animal__id_raza__id_especie'
        ).prefetch_related(
            'id_animal__animalresponsable_set__id_responsable',
            'consulta_set',
            'consulta_set__diagnostico_set'
        ).order_by('-id_historia')

        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(numero_historia__icontains=search) |
                Q(id_animal__nombre__icontains=search) |
                Q(id_animal__animalresponsable__id_responsable__nombre__icontains=search) |
                Q(id_animal__animalresponsable__id_responsable__documento__icontains=search)
            ).distinct()

        especie = request.query_params.get('especie', '').strip()
        if especie:
            queryset = queryset.filter(
                id_animal__id_raza__id_especie__nombre__iexact=especie
            )

        page_number = request.query_params.get('page', 1)
        page_size = request.query_params.get('page_size', 10)
        try:
            page_size = int(page_size)
            if page_size <= 0:
                page_size = 10
        except ValueError:
            page_size = 10

        paginator = Paginator(queryset, page_size)
        try:
            page_obj = paginator.page(page_number)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages) if paginator.num_pages > 0 else []

        results = []
        for hc in page_obj:
            animal = hc.id_animal

            # Tutor / Responsable
            tenedor_data = {}
            if animal:
                relacion = animal.animalresponsable_set.filter(tipo_responsabilidad='Tenedor').last()
                if not relacion:
                    relacion = animal.animalresponsable_set.last()
                if relacion and relacion.id_responsable:
                    resp = relacion.id_responsable
                    tenedor_data = {
                        "id_responsable": resp.id_responsable,
                        "nombre": resp.nombre or "",
                        "documento": resp.documento or "",
                        "telefono": resp.telefono or "",
                        "direccion": resp.direccion or ""
                    }

            # Datos del Animal
            animal_data = {}
            if animal:
                especie_nombre = animal.id_raza.id_especie.nombre if (animal.id_raza and animal.id_raza.id_especie) else "Desconocida"
                raza_nombre = animal.id_raza.nombre if animal.id_raza else "Mestizo"
                animal_data = {
                    "id_animal": animal.id_animal,
                    "nombre": animal.nombre or "",
                    "especie": especie_nombre,
                    "raza": raza_nombre,
                    "sexo": animal.sexo or "",
                    "color": animal.color or "",
                    "peso": float(animal.peso) if animal.peso else None,
                    "foto_url": animal.foto_url or ""
                }

            # Última Consulta registrada
            consultas = list(hc.consulta_set.all().order_by('-fecha_hora'))
            ultima_consulta_data = None
            if consultas:
                ult = consultas[0]
                diag = ult.diagnostico_set.last()
                ultima_consulta_data = {
                    "id_consulta": ult.id_consulta,
                    "fecha_hora": ult.fecha_hora.isoformat() if ult.fecha_hora else "",
                    "motivo_consulta": ult.motivo_consulta or "",
                    "diagnostico_final": diag.descripcion if diag else "",
                    "ingresa_cba": bool(ult.ingresa_cba)
                }

            results.append({
                "id_historia": hc.id_historia,
                "numero_historia": hc.numero_historia or f"HC-{animal.id_animal if animal else hc.id_historia}",
                "fecha_apertura": hc.fecha_apertura.isoformat() if hc.fecha_apertura else "",
                "estado_general": hc.estado_general or "Bueno",
                "animal": animal_data,
                "tutor": tenedor_data,
                "ultima_consulta": ultima_consulta_data,
                "total_consultas": len(consultas)
            })

        return Response({
            "count": paginator.count,
            "total_pages": paginator.num_pages,
            "page": int(page_number) if str(page_number).isdigit() else 1,
            "results": results
        }, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_description="Crea o añade una consulta médica a la historia clínica del animal. Si el animal ya existe, reutiliza su historia clínica única.",
        request_body=ExpedienteCompletoSerializer,
        responses={201: "Atención médica registrada exitosamente", 400: "Datos inválidos"}
    )
    def post(self, request, *args, **kwargs):
        serializer = ExpedienteCompletoSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            result = serializer.save()
            return Response(result, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ExpedientePorAnimalAPIView(APIView):
    """
    GET /api/clinica/expediente-por-animal/<int:id_animal>/
    Obtiene los antecedentes y expediente médico consolidado de un animal dado su ID.
    Si ya tiene historia, retorna sus datos anamnésicos y consultas previas ordenadas por fecha.
    Si no tiene historia, retorna los datos conocidos del animal y del tenedor listos para precargar.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Obtiene el expediente médico consolidado y antecedentes de un animal por su id_animal.",
        responses={200: openapi.Response('Expediente y antecedentes del animal')}
    )
    def get(self, request, id_animal, *args, **kwargs):
        from django.shortcuts import get_object_or_404

        animal = get_object_or_404(
            Animal.objects.select_related('id_raza', 'id_raza__id_especie').prefetch_related(
                'animalresponsable_set__id_responsable'
            ),
            pk=id_animal
        )

        # Tenedor / Responsable
        tenedor_data = {}
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

        animal_data = {
            "id_animal": animal.id_animal,
            "especie": animal.id_raza.id_especie.nombre if (animal.id_raza and animal.id_raza.id_especie) else "Desconocida",
            "raza": animal.id_raza.nombre if animal.id_raza else "Mestizo",
            "nombre": animal.nombre or "",
            "sexo": animal.sexo or "",
            "color": animal.color or "",
            "peso": float(animal.peso) if animal.peso else None,
            "descripcionBreve": animal.observaciones or "",
            "caracteristicasEspeciales": animal.caracteristicas or "",
            "fotoUrl": animal.foto_url or "",
            "numeroHistoriaClinica": ""
        }

        historia = HistoriaClinica.objects.filter(id_animal=animal).first()
        if not historia:
            return Response({
                "tiene_historia": False,
                "id_animal": animal.id_animal,
                "animal": animal_data,
                "tenedor": tenedor_data,
                "anamnesicos": {},
                "consultas_anteriores": []
            }, status=status.HTTP_200_OK)

        animal_data["numeroHistoriaClinica"] = historia.numero_historia or ""

        anamnesicos_data = {
            "id_historia": historia.id_historia,
            "ultimaDesparasitacion": historia.fecha_ultima_desparasitacion.isoformat() if historia.fecha_ultima_desparasitacion else "",
            "productoDesparasitacion": historia.producto_desparasitacion or "",
            "vacunas": historia.vacunas or "",
            "enfermedadesAnteriores": historia.enfermedades_anteriores or "",
            "tratamientos": historia.tratamientos_anteriores or "",
            "evolucion": historia.evolucion_previa or "",
            "alimentacion": historia.alimentacion or "",
            "historiaReproductiva": historia.estado_reproductivo or "",
            "ultimoCelo": historia.fecha_ultimo_celo.isoformat() if historia.fecha_ultimo_celo else "",
            "fechaUltimoParto": historia.fecha_ultimo_parto.isoformat() if historia.fecha_ultimo_parto else ""
        }

        consultas_qs = historia.consulta_set.all().prefetch_related('diagnostico_set', 'tratamientos_set').order_by('-fecha_hora')
        consultas_anteriores = []
        for c in consultas_qs:
            diag = c.diagnostico_set.last()
            consultas_anteriores.append({
                "id_consulta": c.id_consulta,
                "fecha_hora": c.fecha_hora.isoformat() if c.fecha_hora else "",
                "motivo_consulta": c.motivo_consulta or "",
                "diagnostico_final": diag.descripcion if diag else "",
                "pronostico": c.pronostico or "",
                "ingresa_cba": c.ingresa_cba,
                "frecuencia_cardiaca": c.frecuencia_cardiaca,
                "frecuencia_respiratoria": c.frecuencia_respiratoria,
                "temperatura": float(c.temperatura) if c.temperatura else None,
                "tratamientos": [
                    {
                        "producto_base": t.producto_base or "",
                        "dosis_basica": t.dosis_basica or "",
                        "presentacion": t.presentacion or "",
                        "via": t.via_administracion or "",
                        "frecuencia": t.frecuencia_duracion or ""
                    }
                    for t in c.tratamientos_set.all()
                ]
            })

        return Response({
            "tiene_historia": True,
            "id_animal": animal.id_animal,
            "id_historia": historia.id_historia,
            "numero_historia": historia.numero_historia or "",
            "animal": animal_data,
            "tenedor": tenedor_data,
            "anamnesicos": anamnesicos_data,
            "consultas_anteriores": consultas_anteriores
        }, status=status.HTTP_200_OK)
