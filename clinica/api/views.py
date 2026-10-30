from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
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


from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from animales.models import Animal


class RestrictDeleteProtectionMixin:
    """
    Captura errores de restricción de integridad referencial al intentar eliminar
    registros clínicos que poseen otros registros asociados que dependen de ellos.
    """
    def destroy(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                return super().destroy(request, *args, **kwargs)
        except (ProtectedError, RestrictedError, IntegrityError):
            return Response(
                {
                    "error": "No se puede eliminar este registro porque tiene otros registros asociados que dependen de él.",
                    "codigo": "REGISTRO_CON_DEPENDENCIAS",
                },
                status=status.HTTP_409_CONFLICT,
            )


class HistoriaClinicaViewSet(RestrictDeleteProtectionMixin, viewsets.ModelViewSet):
    queryset = HistoriaClinica.objects.all()
    serializer_class = HistoriaClinicaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        id_animal = self.request.query_params.get('id_animal')
        if id_animal:
            queryset = queryset.filter(id_animal_id=id_animal)
        return queryset


class ConsultaViewSet(RestrictDeleteProtectionMixin, viewsets.ModelViewSet):
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
        ingresa_cba = self.request.query_params.get('ingresa_cba')
        if ingresa_cba is not None:
            if ingresa_cba.strip().lower() in ['true', '1', 't', 'si']:
                queryset = queryset.filter(ingresa_cba=True)
            elif ingresa_cba.strip().lower() in ['false', '0', 'f', 'no']:
                queryset = queryset.filter(ingresa_cba=False)
        return queryset

    @action(detail=False, methods=['get'], url_path='cba')
    def cba(self, request):
        """
        GET /api/clinica/consultas/cba/
        Retorna únicamente las atenciones clínicas con ingreso efectivo al CBA (ingresa_cba=True).
        """
        consultas_cba = self.get_queryset().filter(ingresa_cba=True)
        page = self.paginate_queryset(consultas_cba)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(consultas_cba, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class HospitalizacionSeresSintientesViewSet(viewsets.ModelViewSet):
    queryset = HospitalizacionSeresSintientes.objects.all()
    serializer_class = HospitalizacionSeresSintientesSerializer
    permission_classes = [IsAuthenticated]


class TratamientosViewSet(RestrictDeleteProtectionMixin, viewsets.ModelViewSet):
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
from .serializers import ExpedienteCompletoSerializer
from inventario.api.kit_views import KitErrorHandlingMixin

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

class ExpedienteCompletoAPIView(KitErrorHandlingMixin, APIView):
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
                    'tratamientos_set__tratamientomedicamentos_set__id_medicamento',
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

            tratamientos_data = []
            for t in consulta.tratamientos_set.all():
                tm = t.tratamientomedicamentos_set.first()
                t_dict = {
                    "id_tratamiento": t.id_tratamiento,
                    "productoBase": t.producto_base or "",
                    "dosisBasica": t.dosis_basica or "",
                    "presentacion": t.presentacion or "",
                    "via": t.via_administracion or "",
                    "frecuenciaDuracion": t.frecuencia_duracion or "",
                    "id_medicamento": tm.id_medicamento_id if tm else None,
                    "medicamento_catalogo": tm.id_medicamento.nombre if (tm and tm.id_medicamento) else None,
                    "suministrado_clinica": bool(tm and tm.fecha_aplicacion)
                }
                tratamientos_data.append(t_dict)

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

    @swagger_auto_schema(
        operation_description="Actualiza la historia clínica y evolución del animal registrando trazabilidad y auditoría de cambios (Jira SCRUM-84 / HE06-HU013).",
        responses={200: "Historia clínica actualizada con éxito", 400: "Datos inválidos o historia anulada", 404: "No encontrado"}
    )
    def patch(self, request, *args, **kwargs):
        from django.db import transaction
        from datetime import datetime

        data = request.data
        id_animal = data.get('id_animal')
        if not id_animal:
            animal_data = data.get('animal') or {}
            id_animal = animal_data.get('id_animal') or animal_data.get('id')

        if not id_animal:
            return Response(
                {"detail": "El id_animal es obligatorio para actualizar la historia clínica."},
                status=status.HTTP_400_BAD_REQUEST
            )

        animal = Animal.objects.filter(pk=id_animal).first()
        if not animal:
            return Response(
                {"detail": f"No se encontró el animal con ID {id_animal}."},
                status=status.HTTP_404_NOT_FOUND
            )

        historia = HistoriaClinica.objects.filter(id_animal=animal).first()
        if not historia:
            return Response(
                {"detail": f"El animal '{animal.nombre}' no posee historia clínica registrada previa."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Criterio Jira HE06-HU013 Escenario 3.0: Edición bloqueada en historia anulada
        if str(historia.estado_general or '').strip().lower() == 'anulado':
            return Response(
                {"detail": "La historia clínica se encuentra anulada y no puede ser modificada."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Criterio Jira HE06-HU013 Escenario 2.0: Validación de campos obligatorios
        diagnostico_principal = data.get('diagnostico_principal') or data.get('diagnosticoPrincipal') or ''
        evolucion = data.get('evolucion') or ''
        pronostico = data.get('pronostico') or 'Favorable'
        plan_tratamiento = data.get('plan_tratamiento') or data.get('planTratamiento') or ''
        motivo_modificacion = data.get('motivo_modificacion') or data.get('motivoModificacion') or ''

        if not str(diagnostico_principal).strip():
            return Response({"detail": "El diagnóstico principal es obligatorio."}, status=status.HTTP_400_BAD_REQUEST)
        if not str(evolucion).strip():
            return Response({"detail": "La evolución médica es obligatoria."}, status=status.HTTP_400_BAD_REQUEST)
        if not str(motivo_modificacion).strip():
            return Response({"detail": "Debe justificar el motivo de la modificación para el historial de auditoría."}, status=status.HTTP_400_BAD_REQUEST)

        from inventario.constants import CONSUMO_ORIGEN_HISTORIA
        from inventario.services.consumo_service import descontar_consumo_kit
        from inventario.api.kit_serializers import (
            validar_payload_consumo_kit,
            verificar_idempotencia_consumo_clinico,
            extraer_alertas_farmacia,
        )

        id_vet_kit = getattr(request.user, 'id_usuario', None)
        consumos_kit, es_offline_kit = validar_payload_consumo_kit(
            raw_meds=data.get('medicamentos_usados'),
            raw_offline=data.get('sincronizacion_offline', False),
            id_veterinario=id_vet_kit,
            permitir_multipart=False,
        )

        with transaction.atomic():
            # Actualizar peso del animal si fue suministrado
            nuevo_peso = data.get('peso')
            peso_anterior = float(animal.peso) if animal.peso else None
            if nuevo_peso is not None and str(nuevo_peso).strip() != '':
                try:
                    nuevo_peso_float = float(nuevo_peso)
                    if nuevo_peso_float != peso_anterior:
                        animal.peso = nuevo_peso_float
                        animal.save(update_fields=['peso'])
                except (ValueError, TypeError):
                    pass

            # Obtener última consulta asociada a la historia clínica única
            consulta = historia.consulta_set.order_by('-fecha_hora').first()
            if not consulta:
                consulta = Consulta.objects.create(
                    id_historia=historia,
                    motivo_consulta=f"Actualización / Control: {motivo_modificacion}",
                    pronostico=pronostico
                )

            diag_obj = consulta.diagnostico_set.last()
            diag_anterior = diag_obj.descripcion if diag_obj else ""
            evolucion_anterior = historia.evolucion_previa or ""
            pronostico_anterior = consulta.pronostico or ""
            plan_anterior = consulta.plan_procedimientos or historia.tratamientos_anteriores or ""

            # Calcular campos cambiados (Criterio Jira HE06-HU013 Escenario 4.0)
            campos_cambiados = []
            if str(diagnostico_principal).strip() != str(diag_anterior).strip():
                campos_cambiados.append({
                    "campo": "diagnostico_principal",
                    "etiqueta": "Diagnóstico Principal",
                    "valor_anterior": diag_anterior or "(vacío)",
                    "valor_nuevo": diagnostico_principal
                })
            if str(evolucion).strip() != str(evolucion_anterior).strip():
                campos_cambiados.append({
                    "campo": "evolucion",
                    "etiqueta": "Evolución Clínica",
                    "valor_anterior": evolucion_anterior or "(vacío)",
                    "valor_nuevo": evolucion
                })
            if str(pronostico).strip() != str(pronostico_anterior).strip():
                campos_cambiados.append({
                    "campo": "pronostico",
                    "etiqueta": "Pronóstico",
                    "valor_anterior": pronostico_anterior or "(vacío)",
                    "valor_nuevo": pronostico
                })
            if str(plan_tratamiento).strip() != str(plan_anterior).strip():
                campos_cambiados.append({
                    "campo": "plan_tratamiento",
                    "etiqueta": "Plan de Tratamiento",
                    "valor_anterior": plan_anterior or "(vacío)",
                    "valor_nuevo": plan_tratamiento
                })
            if nuevo_peso is not None and str(nuevo_peso).strip() != '' and peso_anterior != float(nuevo_peso):
                campos_cambiados.append({
                    "campo": "peso",
                    "etiqueta": "Peso del Paciente",
                    "valor_anterior": f"{peso_anterior} kg" if peso_anterior is not None else "(vacío)",
                    "valor_nuevo": f"{nuevo_peso} kg"
                })

            # Identificar usuario en sesión
            vet_nombre = ""
            vet_rol = "Veterinario Oficial"
            if request.user and request.user.is_authenticated:
                vet_nombre = f"{getattr(request.user, 'nombre', '')} {getattr(request.user, 'apellido', '')}".strip() or getattr(request.user, 'username', '')
                if hasattr(request.user, 'id_rol') and request.user.id_rol:
                    vet_rol = getattr(request.user.id_rol, 'nombre_rol', 'Veterinario')

            nuevo_registro = {
                "id_auditoria": int(datetime.now().timestamp() * 1000),
                "id_historia": historia.id_historia,
                "id_consulta": consulta.id_consulta,
                "usuario": vet_nombre or "Veterinario CBA",
                "rol": vet_rol,
                "fecha_hora": datetime.now().isoformat(),
                "tipo_accion": "Actualización de Evolución y Diagnóstico",
                "motivo_cambio": motivo_modificacion,
                "campos_cambiados": campos_cambiados if campos_cambiados else [{
                    "campo": "evolucion",
                    "etiqueta": "Evolución",
                    "valor_anterior": evolucion_anterior or "(sin cambio)",
                    "valor_nuevo": evolucion
                }]
            }

            # Persistencia de auditoría en JSONField sin requerir nuevas tablas SQL
            eval_sistemas = consulta.evaluacion_sistemas if isinstance(consulta.evaluacion_sistemas, dict) else {}
            historial = eval_sistemas.get('_historial_modificaciones', [])
            if not isinstance(historial, list):
                historial = []
            historial.insert(0, nuevo_registro)
            eval_sistemas['_historial_modificaciones'] = historial
            consulta.evaluacion_sistemas = eval_sistemas

            # Actualizar constantes fisiológicas
            def parse_num(val, cast_fn):
                if val is None or str(val).strip() == '': return None
                try: return cast_fn(val)
                except: return None

            fc = parse_num(data.get('frecuencia_cardiaca') or data.get('frecuenciaCardiaca'), int)
            fr = parse_num(data.get('frecuencia_respiratoria') or data.get('frecuenciaRespiratoria'), int)
            temp = parse_num(data.get('temperatura'), float)

            if fc is not None: consulta.frecuencia_cardiaca = fc
            if fr is not None: consulta.frecuencia_respiratoria = fr
            if temp is not None: consulta.temperatura = temp

            diagnostico_diferencial = data.get('diagnostico_diferencial') or data.get('diagnosticoDiferencial') or ''
            if diagnostico_diferencial:
                consulta.hallazgos_examen = diagnostico_diferencial

            consulta.plan_procedimientos = plan_tratamiento
            consulta.pronostico = pronostico
            consulta.save()

            # Actualizar antecedentes en HistoriaClinica
            historia.evolucion_previa = evolucion
            historia.tratamientos_anteriores = plan_tratamiento
            historia.save(update_fields=['evolucion_previa', 'tratamientos_anteriores'])

            # Actualizar Diagnóstico final
            lista_prob = data.get('lista_problemas') or data.get('listaProblemas') or ''
            if isinstance(lista_prob, list):
                lista_prob = "; ".join(lista_prob)

            if diag_obj:
                diag_obj.descripcion = diagnostico_principal
                diag_obj.lista_problemas = str(lista_prob)
                diag_obj.save(update_fields=['descripcion', 'lista_problemas'])
            else:
                diag_obj = Diagnostico.objects.create(
                    id_consulta=consulta,
                    descripcion=diagnostico_principal,
                    lista_problemas=str(lista_prob),
                    tipo_diagnostico='Final'
                )

            # Vincular catálogo de Patología si fue seleccionada
            id_patologia = data.get('id_patologia') or data.get('idPatologia')
            from patologias.models import Patologia
            pat_obj = None
            if id_patologia:
                pat_obj = Patologia.objects.filter(pk=id_patologia).first()
            elif diagnostico_principal:
                pat_obj = Patologia.objects.filter(nombre__iexact=str(diagnostico_principal).strip()).first()

            if pat_obj:
                from clinica.models import DiagnosticoPatologia
                dp = DiagnosticoPatologia.objects.filter(id_diagnostico=diag_obj).first()
                if dp:
                    dp.id_patologia = pat_obj
                    dp.save(update_fields=['id_patologia'])
                else:
                    DiagnosticoPatologia.objects.create(
                        id_diagnostico=diag_obj,
                        id_patologia=pat_obj,
                        hallazgos_pruebas=diagnostico_diferencial
                    )

            alertas_farmacia = None
            if consumos_kit:
                verificar_idempotencia_consumo_clinico(
                    consumos=consumos_kit,
                    id_veterinario=id_vet_kit,
                    origen_tipo=CONSUMO_ORIGEN_HISTORIA,
                    id_origen_esperado=consulta.id_consulta,
                    post_descuento=False,
                )
                resultado_kit = descontar_consumo_kit(
                    id_veterinario=id_vet_kit,
                    consumos=consumos_kit,
                    origen_tipo=CONSUMO_ORIGEN_HISTORIA,
                    origen_ids={'id_consulta': consulta.id_consulta},
                    es_offline=es_offline_kit,
                )
                verificar_idempotencia_consumo_clinico(
                    consumos=consumos_kit,
                    id_veterinario=id_vet_kit,
                    origen_tipo=CONSUMO_ORIGEN_HISTORIA,
                    id_origen_esperado=consulta.id_consulta,
                    post_descuento=True,
                )
                alertas_farmacia = extraer_alertas_farmacia(resultado_kit)

        respuesta_patch = {
            "status": "success",
            "mensaje": "Historia clínica actualizada con éxito. La evolución y diagnóstico fueron registrados en el expediente.",
            "historia_id": historia.id_historia,
            "consulta_id": consulta.id_consulta,
            "auditoria": nuevo_registro,
            "historial_modificaciones": historial
        }
        if alertas_farmacia:
            respuesta_patch["alertas_farmacia"] = alertas_farmacia
        return Response(respuesta_patch, status=status.HTTP_200_OK)


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
                "consultas_anteriores": [],
                "historial_modificaciones": []
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

        consultas_qs = historia.consulta_set.all().prefetch_related(
            'diagnostico_set',
            'tratamientos_set__tratamientomedicamentos_set__id_medicamento'
        ).order_by('-fecha_hora')
        consultas_anteriores = []
        historial_modificaciones = []

        for index, c in enumerate(consultas_qs):
            diag = c.diagnostico_set.last()
            # Extraer auditoría persistente de la consulta más reciente
            if index == 0 and isinstance(c.evaluacion_sistemas, dict):
                historial_modificaciones = c.evaluacion_sistemas.get('_historial_modificaciones', [])

            tratamientos_list = []
            for t in c.tratamientos_set.all():
                tm = t.tratamientomedicamentos_set.first()
                tratamientos_list.append({
                    "id_tratamiento": t.id_tratamiento,
                    "producto_base": t.producto_base or "",
                    "dosis_basica": t.dosis_basica or "",
                    "presentacion": t.presentacion or "",
                    "via": t.via_administracion or "",
                    "frecuencia": t.frecuencia_duracion or "",
                    "id_medicamento": tm.id_medicamento_id if tm else None,
                    "medicamento_catalogo": tm.id_medicamento.nombre if (tm and tm.id_medicamento) else None,
                    "suministrado_clinica": bool(tm and tm.fecha_aplicacion)
                })
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
                "tratamientos": tratamientos_list
            })

        return Response({
            "tiene_historia": True,
            "id_animal": animal.id_animal,
            "id_historia": historia.id_historia,
            "numero_historia": historia.numero_historia or "",
            "estado_general": historia.estado_general or "activo",
            "animal": animal_data,
            "tenedor": tenedor_data,
            "anamnesicos": anamnesicos_data,
            "consultas_anteriores": consultas_anteriores,
            "historial_modificaciones": historial_modificaciones
        }, status=status.HTTP_200_OK)
