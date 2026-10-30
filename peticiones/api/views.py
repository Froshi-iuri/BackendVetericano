import unicodedata
import logging
from datetime import timedelta
from django.db import transaction
from django.db.models import Count, Q, F, Sum
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from peticiones.models import (
    TipoPeticion, Peticiones, EstadoPeticiones, SeguimientoPeticionesVisita,
    HistorialAsignacionPeticion, NotificacionJuridico
)
from clinica.models import Consulta
from users.models import Usuarios
from users.api.serializers import UsuariosSerializer
from django.utils import timezone
from .permissions import IsJuridicoOrAdmin
from .serializers import (
    TiposPeticionSerializer, IniciarPeticionSerializer,
    SeguimientoPeticionesVisitaSerializer, AsignarPeticionSerializer,
    ListarPeticionesSerializer, DetallePeticionSerializer,
    ListarSeguimientoPeticionesVisitaSerializer,
    HistorialAsignacionPeticionSerializer, NotificacionJuridicoSerializer,
)
from peticiones.utils import obtener_fotos_acta_con_fallback, obtener_multimedia_acta
from inventario.api.kit_views import KitErrorHandlingMixin

logger = logging.getLogger(__name__)

def _normalizar_texto(texto):
    if not texto:
        return ""
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn').lower().strip()



class ListarTiposPeticionView(generics.ListAPIView):
    queryset = TipoPeticion.objects.filter(activo=True)
    serializer_class = TiposPeticionSerializer
    permission_classes = [IsAuthenticated]


class ListarPeticionesView(generics.ListAPIView):
    """
    GET /api/peticiones/listar/
    Lista todas las peticiones para Admin y Juridico.
    Para Veterinarios, solo las que tienen asignadas.
    Para Peticionarios, solo las creadas por ellos.
    """
    serializer_class = ListarPeticionesSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Peticiones.objects.select_related('id_tipo', 'id_estado', 'id_ubicacion', 'asignado_a').prefetch_related('evidenciapeticiones_set').all()
        
        # 1 = Administrador, 2 = Jurídico, 3 = Veterinario, 4 = Peticionario
        if user.id_rol_id == 3:
            # Veterinario: solo ve lo asignado
            queryset = queryset.filter(asignado_a=user)
        elif user.id_rol_id == 4:
            # Peticionario: solo ve lo que creó
            queryset = queryset.filter(responsable=user)
            
        return queryset.order_by('-fecha')

class IniciarPeticionView(generics.CreateAPIView):
    serializer_class = IniciarPeticionSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def create(self, request, *args, **kwargs):
        # Búsqueda robusta del estado inicial ('Pendiente', 'Borrador', o primer estado disponible)
        estado_inicial = (
            EstadoPeticiones.objects.filter(nombre__iexact='Pendiente').first()
            or EstadoPeticiones.objects.filter(nombre__iexact='Borrador').first()
            or EstadoPeticiones.objects.filter(activo=True).order_by('orden', 'id_estado').first()
            or EstadoPeticiones.objects.first()
        )
        if not estado_inicial:
            return Response(
                {"error": "No existe ningún estado configurado en la base de datos (estado_peticiones)."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        peticion = serializer.save(
            responsable=request.user,
            id_estado=estado_inicial
        )

        evidencias_urls = list(peticion.evidenciapeticiones_set.values_list('ruta_archivo', flat=True))
        return Response({
            "mensaje": "Petición iniciada con éxito",
            "id_peticion": peticion.id_peticion,
            "numero_radicado": peticion.numero_radicado,
            "id_tipo": peticion.id_tipo.id_tipo if peticion.id_tipo else None,
            "id_ubicacion": peticion.id_ubicacion.id_ubicacion if peticion.id_ubicacion else None,
            "foto": peticion.foto,
            "fotos": evidencias_urls or ([peticion.foto] if peticion.foto else []),
            "total_fotos": len(evidencias_urls) if evidencias_urls else (1 if peticion.foto else 0),
        }, status=status.HTTP_201_CREATED)


class PerfilUsuarioView(generics.RetrieveUpdateAPIView):
    serializer_class = UsuariosSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UsuariosSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request):
        serializer = UsuariosSerializer(
            request.user, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ============================================================
# VIEWS DEL ACTA DE VISITA
# ============================================================

class FuncionariosVisitaView(APIView):
    """
    GET /api/peticiones/seguimiento/funcionarios/
    Lista los usuarios habilitados para participar en una visita:
    Veterinarios (id_rol=3), Jurídicos (id_rol=2) y Administradores (id_rol=1).
    Excluye estrictamente a Peticionarios (id_rol=4).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        ROLES_PERMITIDOS = [1, 2, 3]  # Administrador, Juridico, Veterinario
        funcionarios = Usuarios.objects.filter(
            id_rol__id_rol__in=ROLES_PERMITIDOS,
            activo=True
        ).select_related('id_rol').order_by('id_rol__id_rol', 'nombre')

        data = [
            {
                "id_usuario": u.id_usuario,
                "nombre": u.nombre,
                "apellido": u.apellido,
                "rol": u.id_rol.nombre_rol,
                "id_rol": u.id_rol.id_rol,
                "email": u.email,
                "identificacion": u.identificacion,
            }
            for u in funcionarios
        ]
        return Response(data, status=status.HTTP_200_OK)


class SeguimientoPeticionesVisitaCreateView(KitErrorHandlingMixin, generics.CreateAPIView):
    """
    POST /api/peticiones/seguimiento/crear/
    Crea un acta de visita completa.
    El veterinario autenticado queda como id_veterinario automáticamente.

    Body de ejemplo (Modo B - varios animales):
    {
        "id_peticion": 1,
        "numero_radicado": "RAD-VIS-2024-010",
        "fecha_atencion": "2024-10-01",
        "propietario_nombre": "Carlos Gómez",
        "propietario_cedula": "1020304050",
        "propietario_telefono": "3104567890",
        "propietario_barrio": "El Ejido",
        "propietario_direccion": "Calle 5 # 10-20",
        "quien_reporta": "Propietario",
        "lugar_atencion": {
            "direccion": "Calle 5 # 10-20, Popayán",
            "latitud": 2.4448,
            "longitud": -76.6147
        },
        "animales": [
            {"nombre": "Toby", "sexo": "Macho", "color": "Café", "peso": 12.5},
            {"nombre": "Michi", "sexo": "Hembra", "peso": 3.2}
        ],
        "anamnesis_descripcion_queja": "Animal con signos de desnutrición",
        "tratamiento_realizado": "Suero oral y vitaminas",
        "pruebas_complementarias": "Distemper, Parvovirus",
        "resultado_pruebas": "Distemper: Negativo, Parvovirus: Negativo",
        "compromisos": "Mejorar alimentación y presentar carnés en 30 días",
        "fundamento_legal": "Ley 1774 de 2016",
        "plazo_dias_cumplimiento": 30,
        "funcionarios": [
            {"id_usuario": 3, "es_principal": true},
            {"nombre": "Juan Pasante", "cargo": "Pasante Veterinaria", "es_externo": true,
             "institucion": "Universidad del Cauca", "documento_identidad": "1090123456"}
        ],
        "notificado_nombre": "Carlos Gómez",
        "notificaciones_identificacion": "1020304050",
        "fecha_notificacion": "2024-10-01",
        "notificador_nombre": "Dr. Smith Ordóñez",
        "notificador_identificacion": "12345678",
        "notificador_cargo": "Médico Veterinario Inspector"
    }
    """
    serializer_class = SeguimientoPeticionesVisitaSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        seguimiento = serializer.save(id_veterinario=request.user)
        mm_data = obtener_multimedia_acta(seguimiento)
        respuesta = {
            "mensaje": "Acta de visita registrada exitosamente.",
            "id_seguimiento": seguimiento.id_seguimiento,
            "foto": mm_data['foto'],
            "fotos": mm_data['fotos'],
            "total_fotos": mm_data['total_fotos'],
            "videos": mm_data['videos'],
            "total_videos": mm_data['total_videos'],
            "multimedia": mm_data['multimedia'],
        }
        alertas_farmacia = getattr(seguimiento, '_alertas_farmacia', None)
        if alertas_farmacia:
            respuesta["alertas_farmacia"] = alertas_farmacia
        return Response(
            respuesta,
            status=status.HTTP_201_CREATED,
        )


class SeguimientoPeticionesVisitaDetailView(generics.RetrieveUpdateAPIView):
    """
    GET  /api/peticiones/seguimiento/<id>/
    PATCH /api/peticiones/seguimiento/<id>/actualizar/
    Consulta o actualiza un acta de visita existente.
    """
    serializer_class = SeguimientoPeticionesVisitaSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    queryset = SeguimientoPeticionesVisita.objects.select_related(
        'id_peticion', 'id_veterinario', 'id_animal', 'id_ubicacion_visita'
    ).prefetch_related('funcionarios', 'id_peticion__evidenciapeticiones_set', 'evidencias')
    lookup_field = 'id_seguimiento'

    def update(self, request, *args, **kwargs):
        kwargs['partial'] = True  # Siempre parcial (PATCH)
        return super().update(request, *args, **kwargs)


class ListarSeguimientoPeticionesVisitaView(generics.ListAPIView):
    """
    GET /api/peticiones/seguimiento/
    Lista paginada de actas de visita de campo.

    Filtros opcionales (query params):
      ?id_peticion=5            -> solo actas de esa petición
      ?id_veterinario=3         -> solo actas creadas por ese veterinario
      ?numero_radicado=RAD-...  -> búsqueda parcial por radicado del acta
      ?fecha_desde=2024-01-01   -> fecha de creación >= (YYYY-MM-DD)
      ?fecha_hasta=2024-12-31   -> fecha de creación <= (YYYY-MM-DD)

    Permisos por rol (igual criterio que ListarPeticionesView):
      1 Administrador / 2 Jurídico: ven todo.
      3 Veterinario: solo sus propias actas.
      4 Peticionario: solo actas de peticiones que creó (responsable).
    """
    serializer_class = ListarSeguimientoPeticionesVisitaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = (
            SeguimientoPeticionesVisita.objects
            .select_related(
                'id_peticion', 'id_peticion__id_estado',
                'id_veterinario', 'id_animal', 'id_ubicacion_visita',
            )
            .prefetch_related('funcionarios', 'id_peticion__evidenciapeticiones_set', 'evidencias')
        )

        # 1 = Administrador, 2 = Jurídico, 3 = Veterinario, 4 = Peticionario
        if user.id_rol_id == 3:
            queryset = queryset.filter(id_veterinario=user)
        elif user.id_rol_id == 4:
            queryset = queryset.filter(id_peticion__responsable=user)

        params = self.request.query_params
        id_seguimiento = params.get('id_seguimiento') or params.get('id')
        if id_seguimiento:
            queryset = queryset.filter(id_seguimiento=id_seguimiento)

        id_peticion = params.get('id_peticion')
        if id_peticion:
            queryset = queryset.filter(id_peticion_id=id_peticion)

        # Solo admin/jurídico pueden filtrar por otro veterinario;
        # un veterinario siempre ve lo suyo y un peticionario lo de sus peticiones.
        id_veterinario = params.get('id_veterinario')
        if id_veterinario and user.id_rol_id in (1, 2):
            queryset = queryset.filter(id_veterinario_id=id_veterinario)

        numero_radicado = params.get('numero_radicado')
        if numero_radicado:
            queryset = queryset.filter(numero_radicado__icontains=numero_radicado)

        fecha_desde = params.get('fecha_desde')
        if fecha_desde:
            queryset = queryset.filter(fecha__date__gte=fecha_desde)

        fecha_hasta = params.get('fecha_hasta')
        if fecha_hasta:
            queryset = queryset.filter(fecha__date__lte=fecha_hasta)

        return queryset.order_by('-fecha', '-id_seguimiento')


class ListarSeguimientosPorPeticionView(ListarSeguimientoPeticionesVisitaView):
    """
    GET /api/peticiones/<id_peticion>/seguimientos/
    Historial de visitas de UNA petición (mismo serializer y filtros de fecha,
    más control de acceso por rol sobre esa petición).
    """
    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(id_peticion_id=self.kwargs['id_peticion'])


class AsignarPeticionView(generics.UpdateAPIView):
    """
    PATCH /api/peticiones/<id_peticion>/asignar/
    Permite a un funcionario con rol Jurídico o Administrador asignar o reasignar
    la petición a un veterinario.
    - Transición automática: actualiza el estado de la petición a 'Asignada'.
    - Trazabilidad (SCRUM-210): si ya existía un veterinario asignado, registra
      la reasignación en HistorialAsignacionPeticion con el motivo enviado.
    """
    queryset = Peticiones.objects.all()
    serializer_class = AsignarPeticionSerializer
    permission_classes = [IsJuridicoOrAdmin]
    lookup_field = 'id_peticion'

    def update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        peticion = self.get_object()

        # Obtenemos el ID del veterinario a asignar
        asignado_a_id = request.data.get('asignado_a')
        if not asignado_a_id:
            return Response({'error': 'Debe proveer el id del usuario asignado (asignado_a).'}, status=status.HTTP_400_BAD_REQUEST)

        # Validar que el usuario a asignar sea un veterinario (rol 3)
        veterinario = Usuarios.objects.filter(id_usuario=asignado_a_id).first()
        if not veterinario or getattr(veterinario, 'id_rol_id', None) != 3:
            return Response({'error': 'El usuario asignado no es un veterinario válido.'}, status=status.HTTP_400_BAD_REQUEST)

        veterinario_anterior = peticion.asignado_a
        motivo = (request.data.get('motivo') or '').strip()

        with transaction.atomic():
            # Buscar el estado correspondiente a 'Asignada'
            estado_asignada = EstadoPeticiones.objects.filter(nombre__iexact='Asignada').first()
            if not estado_asignada:
                estado_asignada = EstadoPeticiones.objects.filter(nombre__icontains='asignad').first()

            # Actualizar datos de asignación
            serializer = self.get_serializer(peticion, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)

            save_kwargs = {
                'asignado_por': request.user if request.user.is_authenticated else None,
                'fecha_asignacion': timezone.now()
            }
            if estado_asignada:
                save_kwargs['id_estado'] = estado_asignada

            peticion_actualizada = serializer.save(**save_kwargs)

            # Trazabilidad SCRUM-210: Si ya tenía veterinario asignado previamente o se envió motivo
            if veterinario_anterior or motivo:
                HistorialAsignacionPeticion.objects.create(
                    id_peticion=peticion_actualizada,
                    veterinario_anterior=veterinario_anterior,
                    veterinario_nuevo=veterinario,
                    motivo=motivo or ('Reasignación de funcionario veterinario' if veterinario_anterior else 'Asignación de funcionario veterinario'),
                    asignado_por=request.user if request.user.is_authenticated else None
                )

        return Response(serializer.data, status=status.HTTP_200_OK)


class DetallePeticionView(generics.RetrieveAPIView):
    """
    GET /api/peticiones/<id_peticion>/
    Retorna el detalle completo de la petición para visualización móvil y web.
    """
    queryset = Peticiones.objects.select_related('id_tipo', 'id_estado', 'id_ubicacion', 'responsable', 'asignado_a').prefetch_related('evidenciapeticiones_set').all()
    serializer_class = DetallePeticionSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id_peticion'


class ActualizarEstadoPeticionView(APIView):
    """
    PATCH/PUT /api/peticiones/<id_peticion>/estado/
    Actualiza el estado de una petición por parte del veterinario o personal a cargo.
    Body:
    {
        "estado": "En tratamiento",
        "observacion": "Motivo opcional del cambio"
    }
    Opcionalmente acepta:
    {
        "id_estado": 9,
        "observacion": "..."
    }
    """
    permission_classes = [IsAuthenticated]

    def put(self, request, id_peticion):
        return self.patch(request, id_peticion)

    def patch(self, request, id_peticion):
        try:
            peticion = Peticiones.objects.get(pk=id_peticion)
        except Peticiones.DoesNotExist:
            return Response({"error": "Petición no encontrada."}, status=status.HTTP_404_NOT_FOUND)

        nuevo_estado_nombre = request.data.get('estado') or request.data.get('nuevo_estado')
        nuevo_estado_id = request.data.get('id_estado')

        if not nuevo_estado_nombre and not nuevo_estado_id:
            return Response({"error": "Debe especificar el nuevo estado ('estado' o 'id_estado')."}, status=status.HTTP_400_BAD_REQUEST)

        estado_obj = None

        # 1. Búsqueda por ID numérico si se envió
        if nuevo_estado_id:
            try:
                estado_obj = EstadoPeticiones.objects.filter(id_estado=int(nuevo_estado_id)).first()
            except (ValueError, TypeError):
                pass

        # 2. Búsqueda por nombre si aún no se tiene
        if not estado_obj and nuevo_estado_nombre:
            nombre_str = str(nuevo_estado_nombre).strip()
            # Búsqueda exacta (case-insensitive)
            estado_obj = EstadoPeticiones.objects.filter(nombre__iexact=nombre_str).first()

            # 3. Búsqueda insensible a tildes / acentos
            if not estado_obj:
                norm_busqueda = _normalizar_texto(nombre_str)
                for est in EstadoPeticiones.objects.all():
                    if _normalizar_texto(est.nombre) == norm_busqueda:
                        estado_obj = est
                        break

            # 4. Búsqueda por palabra clave clínica común
            if not estado_obj:
                norm_busqueda = _normalizar_texto(nombre_str)
                mapeo_claves = {
                    "evaluac": "En evaluación",
                    "pendient": "En evaluación",
                    "tratamient": "En tratamiento",
                    "observac": "En observación",
                    "alta": "Alta médica",
                    "fallecid": "Fallecido",
                    "transferid": "Transferido a otro centro",
                    "proces": "En Proceso",
                    "atendid": "Atendida",
                    "resuelt": "Resuelta",
                }
                for clave, nombre_estandar in mapeo_claves.items():
                    if clave in norm_busqueda:
                        estado_obj = EstadoPeticiones.objects.filter(nombre__iexact=nombre_estandar).first()
                        if estado_obj:
                            break

            # 5. Si es un estado nuevo que no existe en el catálogo, crearlo
            if not estado_obj:
                try:
                    estado_obj = EstadoPeticiones.objects.create(
                        nombre=nombre_str,
                        activo=True
                    )
                except Exception as e:
                    logger.warning(f"No se pudo crear estado dinámico {nombre_str}: {e}")
                    estado_obj = peticion.id_estado or EstadoPeticiones.objects.filter(activo=True).first()

        if not estado_obj:
            return Response({"error": "No se pudo identificar un estado válido."}, status=status.HTTP_400_BAD_REQUEST)

        # Asignar estado a la petición
        peticion.id_estado = estado_obj
        campos_a_actualizar = ['id_estado']

        # Si el usuario que atiende es veterinario (rol 3) y la petición no tenía asignado, auto-asignar
        if getattr(request.user, 'id_rol_id', None) == 3 and not peticion.asignado_a:
            peticion.asignado_a = request.user
            if not peticion.fecha_asignacion:
                peticion.fecha_asignacion = timezone.now()
            campos_a_actualizar.extend(['asignado_a', 'fecha_asignacion'])

        # Guardar la petición
        peticion.save(update_fields=campos_a_actualizar)

        # Guardar trazabilidad opcional en seguimiento si se provee observación
        observacion = request.data.get('observacion')
        if observacion:
            try:
                vet_usuario = request.user if hasattr(request.user, 'id_usuario') else None
                if vet_usuario:
                    SeguimientoPeticionesVisita.objects.create(
                        id_peticion=peticion,
                        id_veterinario=vet_usuario,
                        observacion=f"Cambio de estado a '{estado_obj.nombre}': {observacion}"
                    )
            except Exception as e:
                logger.warning(f"No se pudo registrar seguimiento para cambio de estado: {e}")

        return Response({
            "mensaje": f"Estado actualizado a '{estado_obj.nombre}' exitosamente.",
            "id_peticion": peticion.id_peticion,
            "id_estado": estado_obj.id_estado,
            "estado": estado_obj.nombre
        }, status=status.HTTP_200_OK)




# ============================================================================
# MÓDULO JURÍDICO: DASHBOARD, NOTIFICACIONES Y REASIGNACIONES
# ============================================================================

class DashboardJuridicoView(APIView):
    """
    GET /api/peticiones/dashboard-juridico/
    Consolida las métricas del módulo jurídico en una sola llamada:
    - Peticiones y distribución por estado, casos urgentes y casos atrasados (>= 5 días)
    - Casos por veterinario (totales, pendientes, en proceso, finalizadas, % resolución)
    - Seres sintientes ingresados al CBA (observación, tratamiento, adopción y últimos 6 ingresos)
    """
    permission_classes = [IsJuridicoOrAdmin]

    def get(self, request, *args, **kwargs):
        # ----------------------------------------------------
        # 1. MÉTRICAS DE PETICIONES
        # ----------------------------------------------------
        total_peticiones = Peticiones.objects.count()

        # Urgentes: prioridad alta o urgente
        filtro_urgentes = (
            Q(prioridad__iexact='alta') |
            Q(prioridad__iexact='urgente') |
            Q(prioridad__icontains='urg') |
            Q(prioridad__icontains='alt')
        )
        total_urgentes = Peticiones.objects.filter(filtro_urgentes).count()

        # Atrasadas: >= 5 días sin atención y no finalizadas
        hace_5_dias = timezone.now() - timedelta(days=5)
        filtro_no_finalizadas = (
            ~Q(id_estado__nombre__icontains='atendid') &
            ~Q(id_estado__nombre__icontains='finaliz') &
            ~Q(id_estado__nombre__icontains='alta') &
            ~Q(id_estado__nombre__icontains='cerrad') &
            ~Q(id_estado__nombre__icontains='resuelt')
        )
        filtro_atrasadas = (
            (Q(fecha_asignacion__lte=hace_5_dias) | (Q(fecha_asignacion__isnull=True) & Q(fecha__lte=hace_5_dias))) &
            filtro_no_finalizadas
        )
        total_atrasadas = Peticiones.objects.filter(filtro_atrasadas).count()

        # Distribución por estados
        distribucion_estados = {}
        estados_qs = Peticiones.objects.values('id_estado__nombre').annotate(total=Count('id_peticion'))
        for item in estados_qs:
            nombre = item['id_estado__nombre'] or 'Sin Estado'
            distribucion_estados[nombre] = item['total']

        # Conteo agrupado estandarizado para frontend
        pendientes_count = 0
        en_proceso_count = 0
        finalizadas_count = 0
        for est_nom, cant in distribucion_estados.items():
            est_low = est_nom.lower()
            if any(term in est_low for term in ['atendid', 'finaliz', 'cerrad', 'alta', 'resuelt']):
                finalizadas_count += cant
            elif any(term in est_low for term in ['proceso', 'atenci', 'curso', 'tratam']):
                en_proceso_count += cant
            else:
                pendientes_count += cant

        peticiones_data = {
            "total": total_peticiones,
            "urgentes": total_urgentes,
            "atrasadas_mas_5dias": total_atrasadas,
            "distribucion_estados": distribucion_estados,
            "distribucionEstados": distribucion_estados,
            "pendientes": pendientes_count,
            "enProceso": en_proceso_count,
            "en_proceso": en_proceso_count,
            "finalizadas": finalizadas_count,
        }

        # ----------------------------------------------------
        # 2. MÉTRICAS DE VETERINARIOS (id_rol = 3)
        # ----------------------------------------------------
        vets_qs = Usuarios.objects.filter(
            id_rol_id=3,
            activo=True
        ).annotate(
            total_asig=Count('peticiones_asignadas'),
            pendientes_asig=Count('peticiones_asignadas', filter=(
                Q(peticiones_asignadas__id_estado__nombre__icontains='pendiente') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='evalua') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='asignad') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='revis')
            )),
            en_proceso_asig=Count('peticiones_asignadas', filter=(
                Q(peticiones_asignadas__id_estado__nombre__icontains='proceso') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='atenci') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='curso') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='tratam')
            )),
            finalizadas_asig=Count('peticiones_asignadas', filter=(
                Q(peticiones_asignadas__id_estado__nombre__icontains='atendid') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='finaliz') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='alta') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='cerrad') |
                Q(peticiones_asignadas__id_estado__nombre__icontains='resuelt')
            ))
        ).order_by('-total_asig', 'nombre')

        veterinarios_data = []
        for v in vets_qs:
            tot = v.total_asig
            fin = v.finalizadas_asig
            pct = round((fin / tot) * 100, 1) if tot > 0 else 0
            if isinstance(pct, float) and pct.is_integer():
                pct = int(pct)
            veterinarios_data.append({
                "id_veterinario": v.id_usuario,
                "nombre_completo": f"{v.nombre} {v.apellido}".strip(),
                "total_asignados": tot,
                "pendientes": v.pendientes_asig,
                "en_proceso": v.en_proceso_asig,
                "finalizadas": fin,
                "porcentaje_completado": pct,
            })

        # ----------------------------------------------------
        # 3. MÉTRICAS CBA (clinica.Consulta con ingresa_cba=True)
        # ----------------------------------------------------
        consultas_cba = Consulta.objects.filter(ingresa_cba=True)
        total_cba = consultas_cba.count()

        filtro_obs = (
            Q(pronostico__icontains='observaci') |
            Q(resultado_estado__icontains='observaci') |
            Q(pronostico__icontains='evalua') |
            Q(resultado_estado__icontains='evalua') |
            Q(pronostico__icontains='reservad') |
            Q(resultado_estado__icontains='reservad')
        )
        filtro_trat = (
            Q(pronostico__icontains='tratam') |
            Q(resultado_estado__icontains='tratam') |
            Q(pronostico__icontains='hospit') |
            Q(resultado_estado__icontains='hospit') |
            Q(pronostico__icontains='grave') |
            Q(resultado_estado__icontains='grave')
        )
        filtro_adop = (
            Q(pronostico__icontains='favor') |
            Q(resultado_estado__icontains='favor') |
            Q(pronostico__icontains='recuperad') |
            Q(resultado_estado__icontains='recuperad') |
            Q(pronostico__icontains='adop') |
            Q(resultado_estado__icontains='adop')
        )

        en_observacion = consultas_cba.filter(filtro_obs).count()
        en_tratamiento = consultas_cba.filter(filtro_trat).count()
        aptos_adopcion = consultas_cba.filter(filtro_adop).count()

        if total_cba > 0 and (en_observacion + en_tratamiento + aptos_adopcion) == 0:
            en_observacion = total_cba

        # Fallback con catálogo de Animales si no hay consultas registradas en CBA
        if total_cba == 0:
            try:
                from animales.models import Animal
                aptos_adopcion = Animal.objects.filter(disponible_adopcion=True).count()
                en_observacion = Animal.objects.filter(activo=True, disponible_adopcion=False).count()
                total_cba = aptos_adopcion + en_observacion
            except Exception:
                pass

        ultimos_qs = consultas_cba.order_by('-fecha_hora')[:6]
        ultimos_ingresos = []
        for c in ultimos_qs:
            fecha_iso = c.fecha_hora.isoformat() if c.fecha_hora else ""
            canil = c.canil_asignado or "Canil General"
            func_recibe = c.funcionario_recibe_cba or "Personal de Turno CBA"
            motivo_ingreso = c.motivo_ingreso_cba or c.motivo_consulta or "Ingreso CBA"
            estado_diag = c.pronostico or c.resultado_estado or "En Observación"

            ultimos_ingresos.append({
                "id_consulta": c.id_consulta,
                "fecha_ingreso": fecha_iso,
                "canil": canil,
                "funcionario_recibe": func_recibe,
                "funcionario": func_recibe,
                "motivo": motivo_ingreso,
                "estado": estado_diag,
            })

        cba_data = {
            "total_ingresados_cba": total_cba,
            "totalIngresadosCBA": total_cba,
            "en_observacion": en_observacion,
            "enObservacion": en_observacion,
            "en_tratamiento": en_tratamiento,
            "enTratamiento": en_tratamiento,
            "aptos_adopcion": aptos_adopcion,
            "aptosAdopcion": aptos_adopcion,
            "ultimos_ingresos": ultimos_ingresos,
            "ultimosIngresos": ultimos_ingresos,
        }

        return Response({
            "peticiones": peticiones_data,
            "veterinarios": veterinarios_data,
            "casosVeterinarios": veterinarios_data,
            "cba": cba_data,
        }, status=status.HTTP_200_OK)


class NotificacionesJuridicoView(APIView):
    """
    GET /api/peticiones/notificaciones/
    Lista notificaciones del rol jurídico. Por defecto retorna solo no leídas,
    o todas si se incluye ?todas=true.
    """
    permission_classes = [IsJuridicoOrAdmin]

    def get(self, request, *args, **kwargs):
        incluir_todas = request.query_params.get('todas', '').strip().lower() in ['true', '1', 'si']
        queryset = NotificacionJuridico.objects.select_related('id_peticion').all()
        if not incluir_todas:
            queryset = queryset.filter(leida=False)
        queryset = queryset.order_by('-fecha_creacion')
        serializer = NotificacionJuridicoSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        """Permite crear una notificación jurídica manual o programática."""
        serializer = NotificacionJuridicoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MarcarNotificacionLeidaView(APIView):
    """
    PATCH /api/peticiones/notificaciones/<int:id>/marcar-leida/
    Marca una notificación específica como leída.
    Si se invoca sin id o con id=0 o ?todas=true, marca todas las notificaciones como leídas.
    """
    permission_classes = [IsJuridicoOrAdmin]

    def patch(self, request, id=None, *args, **kwargs):
        marcar_todas = (
            id is None or
            id == 0 or
            request.data.get('marcar_todas') is True or
            request.query_params.get('todas', '').lower() in ['true', '1', 'si']
        )
        if marcar_todas:
            actualizadas = NotificacionJuridico.objects.filter(leida=False).update(leida=True)
            return Response({
                "mensaje": "Todas las notificaciones han sido marcadas como leídas.",
                "actualizadas": actualizadas
            }, status=status.HTTP_200_OK)

        notificacion = NotificacionJuridico.objects.filter(id_notificacion=id).first()
        if not notificacion:
            return Response({
                "error": f"Notificación con id {id} no encontrada."
            }, status=status.HTTP_404_NOT_FOUND)

        notificacion.leida = True
        notificacion.save(update_fields=['leida'])
        serializer = NotificacionJuridicoSerializer(notificacion)
        return Response({
            "mensaje": "Notificación marcada como leída exitosamente.",
            "notificacion": serializer.data
        }, status=status.HTTP_200_OK)


class HistorialAsignacionesPorPeticionView(generics.ListAPIView):
    """
    GET /api/peticiones/<id_peticion>/historial-asignaciones/
    Lista el historial cronológico de reasignaciones de una petición.
    """
    serializer_class = HistorialAsignacionPeticionSerializer
    permission_classes = [IsJuridicoOrAdmin]

    def get_queryset(self):
        id_peticion = self.kwargs.get('id_peticion')
        return HistorialAsignacionPeticion.objects.filter(
            id_peticion_id=id_peticion
        ).select_related('veterinario_anterior', 'veterinario_nuevo', 'asignado_por').order_by('-fecha')
