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
        serializer = UsuariosSerializer(request.user, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request):
        serializer = UsuariosSerializer(
            request.user, data=request.data, partial=True, context={'request': request}
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
    Excluye estrictamente a Peticionarios (id_rol=4) tanto en respuesta como en permiso de consulta.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        rol_id = getattr(request.user, 'id_rol_id', None)
        if rol_id == 4:
            return Response({"error": "No cuenta con permisos para consultar el directorio de funcionarios."}, status=status.HTTP_403_FORBIDDEN)
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
        user = request.user
        rol_id = getattr(user, 'id_rol_id', None)
        if rol_id not in (1, 3):
            return Response({"error": "Solo administradores y veterinarios pueden crear actas de visita."}, status=status.HTTP_403_FORBIDDEN)
        id_pet_req = request.data.get('id_peticion')
        if rol_id == 3 and id_pet_req:
            pet_obj = Peticiones.objects.filter(pk=id_pet_req).first()
            if pet_obj and pet_obj.asignado_a_id != user.id_usuario:
                return Response({"error": "No puede crear un acta de visita para una petición asignada a otro veterinario."}, status=status.HTTP_403_FORBIDDEN)
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

    def get_object(self):
        obj = super().get_object()
        user = self.request.user
        rol_id = getattr(user, 'id_rol_id', None)
        if rol_id == 3 and obj.id_veterinario_id != user.id_usuario:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Solo el veterinario responsable puede acceder a esta acta de visita.")
        if rol_id == 4 and obj.id_peticion and obj.id_peticion.responsable_id != user.id_usuario:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("No tiene permiso para acceder a esta acta de visita.")
        return obj

    def update(self, request, *args, **kwargs):
        user = self.request.user
        rol_id = getattr(user, 'id_rol_id', None)
        if rol_id not in (1, 3):
            return Response({"error": "No cuenta con permisos para modificar un acta de visita."}, status=status.HTTP_403_FORBIDDEN)
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
        id_pet = self.kwargs['id_peticion']
        user = self.request.user
        rol_id = getattr(user, 'id_rol_id', None)
        peticion = Peticiones.objects.filter(pk=id_pet).first()
        if peticion:
            if rol_id == 3 and peticion.asignado_a_id != user.id_usuario:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("No tiene permiso para consultar los seguimientos de esta petición.")
            if rol_id == 4 and peticion.responsable_id != user.id_usuario:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("No tiene permiso para consultar los seguimientos de esta petición.")
        queryset = super().get_queryset()
        return queryset.filter(id_peticion_id=id_pet)


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
    Restricción por rol:
    - Admin y Jurídico: acceso completo a cualquier petición.
    - Veterinario: solo su petición asignada.
    - Peticionario: solo su petición radicada.
    """
    queryset = Peticiones.objects.select_related('id_tipo', 'id_estado', 'id_ubicacion', 'responsable', 'asignado_a').prefetch_related('evidenciapeticiones_set').all()
    serializer_class = DetallePeticionSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id_peticion'

    def get_object(self):
        obj = super().get_object()
        user = self.request.user
        rol_id = getattr(user, 'id_rol_id', None)
        if rol_id == 3 and obj.asignado_a_id != user.id_usuario:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("No tiene permiso para consultar esta petición.")
        if rol_id == 4 and obj.responsable_id != user.id_usuario:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("No tiene permiso para consultar esta petición.")
        return obj


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

        user = request.user
        rol_id = getattr(user, 'id_rol_id', None)
        # Validar permisos: Peticionario NO puede cambiar estado
        if rol_id == 4:
            return Response({"error": "No cuenta con permisos para modificar el estado de la petición."}, status=status.HTTP_403_FORBIDDEN)
        # Veterinario solo si está asignado a él
        if rol_id == 3 and peticion.asignado_a_id != user.id_usuario:
            return Response({"error": "Solo puede actualizar el estado de las peticiones asignadas a usted."}, status=status.HTTP_403_FORBIDDEN)

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



# ==============================================================================
# VISTAS Y EXTENSIONES DEL MÓDULO JURÍDICO (ESTADOS, REASIGNACIÓN, HISTORIAL, NOTIFICACIONES Y CORREO)
# ==============================================================================

from peticiones.models import HistorialPeticiones, NotificacionPeticion
from peticiones.utils import (
    ESTADOS_FLUJO_JURIDICO,
    DIAS_PARA_REASIGNAR,
    normalizar_texto_juridico,
    es_prioridad_urgente,
    calcular_tiempos_peticion,
    registrar_historial_peticion,
    crear_notificaciones_nueva_peticion,
    crear_notificacion_asignacion,
    enviar_correo_peticion_urgente_juridico,
)
from .serializers import (
    EstadoPeticionesSerializer,
    HistorialPeticionesSerializer,
    NotificacionPeticionSerializer,
    construir_historial_peticion_lista,
)


def _obtener_estado_por_nombre(nombre_buscado: str):
    """Busca un estado en EstadoPeticiones de forma insensible a mayúsculas y tildes."""
    est = EstadoPeticiones.objects.filter(nombre__iexact=nombre_buscado).first()
    if est:
        return est
    norm = normalizar_texto_juridico(nombre_buscado)
    for item in EstadoPeticiones.objects.all():
        if normalizar_texto_juridico(item.nombre) == norm:
            return item
    return None


class ListarEstadosPeticionView(APIView):
    """
    GET /api/peticiones/estados/
    Retorna los 6 estados requeridos del flujo de peticiones:
    En revisión, Asignada, En proceso, Atendida, En tratamiento y Finalizada.
    Si se envía ?todos=true, retorna todos los estados activos del catálogo.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        mostrar_todos = str(request.query_params.get('todos', '')).lower() in ('true', '1', 'si')
        todos_activos = list(EstadoPeticiones.objects.filter(activo=True).order_by('orden', 'id_estado'))

        if mostrar_todos:
            serializer = EstadoPeticionesSerializer(todos_activos, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        orden_norm = [normalizar_texto_juridico(n) for n in ESTADOS_FLUJO_JURIDICO]
        mapa_por_norm = {}
        for est in todos_activos:
            n_est = normalizar_texto_juridico(est.nombre)
            if n_est in orden_norm and n_est not in mapa_por_norm:
                mapa_por_norm[n_est] = est

        estados_resultado = [mapa_por_norm[n] for n in orden_norm if n in mapa_por_norm]
        serializer = EstadoPeticionesSerializer(estados_resultado, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class HistorialPeticionView(APIView):
    """
    GET /api/peticiones/<id_peticion>/historial/
    Retorna el historial completo de cambios de estado y asignaciones de una petición.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, id_peticion, *args, **kwargs):
        peticion = (
            Peticiones.objects
            .select_related('id_estado', 'responsable', 'asignado_a', 'asignado_por')
            .filter(pk=id_peticion)
            .first()
        )
        if not peticion:
            return Response({"error": "Petición no encontrada."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        if getattr(user, 'id_rol_id', None) == 3 and peticion.asignado_a_id != user.id_usuario:
            return Response({"error": "No tiene permiso para consultar esta petición."}, status=status.HTTP_403_FORBIDDEN)
        if getattr(user, 'id_rol_id', None) == 4 and peticion.responsable_id != user.id_usuario:
            return Response({"error": "No tiene permiso para consultar esta petición."}, status=status.HTTP_403_FORBIDDEN)

        historial = construir_historial_peticion_lista(peticion)
        return Response(historial, status=status.HTTP_200_OK)


class NotificacionesPeticionesListView(generics.ListAPIView):
    """
    GET /api/peticiones/notificaciones/usuario/
    Lista las notificaciones dentro de la aplicación web para el usuario autenticado.
    Soporta filtro opcional ?leida=false o ?solo_no_leidas=true.
    """
    serializer_class = NotificacionPeticionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = (
            NotificacionPeticion.objects
            .select_related('id_peticion', 'id_peticion__id_tipo', 'id_peticion__id_estado')
        )
        if getattr(user, 'id_rol_id', None) in (1, 2):
            qs = qs.filter(Q(id_usuario_destino=user) | Q(id_usuario_destino__isnull=True))
        else:
            qs = qs.filter(id_usuario_destino=user)

        leida_param = self.request.query_params.get('leida')
        solo_no_leidas = self.request.query_params.get('solo_no_leidas')
        if str(solo_no_leidas).lower() in ('true', '1') or str(leida_param).lower() in ('false', '0'):
            qs = qs.filter(leida=False)
        elif str(leida_param).lower() in ('true', '1'):
            qs = qs.filter(leida=True)

        return qs.order_by('-fecha_creacion', '-id_notificacion')


class MarcarNotificacionPeticionLeidaView(APIView):
    """
    PATCH/POST /api/peticiones/notificaciones/<id_notificacion>/leer/
    Marca una notificación específica como leída.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, id_notificacion, *args, **kwargs):
        return self.patch(request, id_notificacion, *args, **kwargs)

    def patch(self, request, id_notificacion, *args, **kwargs):
        user = request.user
        qs = NotificacionPeticion.objects.filter(pk=id_notificacion)
        if getattr(user, 'id_rol_id', None) in (1, 2):
            qs = qs.filter(Q(id_usuario_destino=user) | Q(id_usuario_destino__isnull=True))
        else:
            qs = qs.filter(id_usuario_destino=user)

        notif = qs.first()
        if not notif:
            return Response({"error": "Notificación no encontrada."}, status=status.HTTP_404_NOT_FOUND)

        if not notif.leida:
            notif.leida = True
            notif.fecha_lectura = timezone.now()
            notif.save(update_fields=['leida', 'fecha_lectura'])

        return Response(NotificacionPeticionSerializer(notif).data, status=status.HTTP_200_OK)


class MarcarTodasNotificacionesLeidasView(APIView):
    """
    PATCH/POST /api/peticiones/notificaciones/leer-todas/
    Marca todas las notificaciones pendientes del usuario autenticado como leídas.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        return self.patch(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        user = request.user
        qs = NotificacionPeticion.objects.filter(leida=False)
        if getattr(user, 'id_rol_id', None) in (1, 2):
            qs = qs.filter(Q(id_usuario_destino=user) | Q(id_usuario_destino__isnull=True))
        else:
            qs = qs.filter(id_usuario_destino=user)

        actualizadas = qs.update(leida=True, fecha_lectura=timezone.now())
        return Response({
            "mensaje": "Notificaciones marcadas como leídas.",
            "actualizadas": actualizadas,
        }, status=status.HTTP_200_OK)


# --- Extensión aditiva de ListarPeticionesView y DetallePeticionView ---

_orig_listar_peticiones_get_qs = ListarPeticionesView.get_queryset


def _juridico_listar_peticiones_get_queryset(self):
    qs = _orig_listar_peticiones_get_qs(self).select_related('asignado_por').prefetch_related('historial_cambios')
    params = self.request.query_params

    estado = params.get('estado')
    if estado:
        qs = qs.filter(id_estado__nombre__iexact=estado.strip())

    id_estado = params.get('id_estado')
    if id_estado:
        qs = qs.filter(id_estado_id=id_estado)

    prioridad = params.get('prioridad')
    if prioridad:
        qs = qs.filter(prioridad__iexact=prioridad.strip())

    asignado_a = params.get('asignado_a')
    if asignado_a and getattr(self.request.user, 'id_rol_id', None) in (1, 2):
        qs = qs.filter(asignado_a_id=asignado_a)

    id_tipo = params.get('id_tipo')
    if id_tipo:
        qs = qs.filter(id_tipo_id=id_tipo)

    return qs


ListarPeticionesView.get_queryset = _juridico_listar_peticiones_get_queryset


_orig_detalle_peticion_get_qs = DetallePeticionView.get_queryset


def _juridico_detalle_peticion_get_queryset(self):
    return (
        _orig_detalle_peticion_get_qs(self)
        .select_related('asignado_por')
        .prefetch_related(
            'historial_cambios',
            'historial_cambios__id_estado_anterior',
            'historial_cambios__id_estado_nuevo',
            'historial_cambios__id_veterinario_anterior',
            'historial_cambios__id_veterinario_nuevo',
            'historial_cambios__id_usuario_accion',
            'historial_cambios__id_usuario_accion__id_rol',
        )
    )


DetallePeticionView.get_queryset = _juridico_detalle_peticion_get_queryset


# --- Extensión aditiva de IniciarPeticionView (estado 'En revisión', asignación opcional, correo urgente, notificación web e historial) ---

def _juridico_iniciar_peticion_create(self, request, *args, **kwargs):
    rol_id = getattr(request.user, 'id_rol_id', None)
    if rol_id == 3:
        return Response(
            {"error": "Los veterinarios no están autorizados para radicar nuevas peticiones."},
            status=status.HTTP_403_FORBIDDEN
        )

    estado_inicial = (
        _obtener_estado_por_nombre('En revisión')
        or EstadoPeticiones.objects.filter(nombre__iexact='Pendiente').first()
        or EstadoPeticiones.objects.filter(nombre__iexact='Borrador').first()
        or EstadoPeticiones.objects.filter(activo=True).order_by('orden', 'id_estado').first()
        or EstadoPeticiones.objects.first()
    )
    if not estado_inicial:
        return Response(
            {"error": "No existe ningún estado configurado en la base de datos (estado_peticiones)."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    veterinario_asignado = None
    asignado_a_id = request.data.get('asignado_a')
    if asignado_a_id and getattr(request.user, 'id_rol_id', None) in (1, 2):
        veterinario_asignado = Usuarios.objects.filter(id_usuario=asignado_a_id, id_rol_id=3, activo=True).first()
        if not veterinario_asignado:
            return Response(
                {"error": "El usuario asignado (asignado_a) no es un veterinario activo válido."},
                status=status.HTTP_400_BAD_REQUEST
            )

    serializer = self.get_serializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    save_kwargs = {
        'responsable': request.user,
        'id_estado': estado_inicial,
    }
    if veterinario_asignado:
        estado_asignada = _obtener_estado_por_nombre('Asignada') or estado_inicial
        save_kwargs['id_estado'] = estado_asignada
        save_kwargs['asignado_a'] = veterinario_asignado
        save_kwargs['asignado_por'] = request.user
        save_kwargs['fecha_asignacion'] = timezone.now()

    peticion = serializer.save(**save_kwargs)

    # 1. Registrar creación en el historial
    registrar_historial_peticion(
        peticion=peticion,
        tipo_cambio='CREACION',
        estado_anterior=None,
        estado_nuevo=estado_inicial,
        veterinario_anterior=None,
        veterinario_nuevo=None,
        usuario_accion=request.user,
        observacion="Petición registrada en el sistema.",
    )

    # 2. Si se asignó en el mismo acto de creación, registrar también la asignación
    if veterinario_asignado:
        registrar_historial_peticion(
            peticion=peticion,
            tipo_cambio='ASIGNACION',
            estado_anterior=estado_inicial,
            estado_nuevo=peticion.id_estado,
            veterinario_anterior=None,
            veterinario_nuevo=veterinario_asignado,
            usuario_accion=request.user,
            observacion=f"Asignada al veterinario {veterinario_asignado.nombre} {veterinario_asignado.apellido}".strip(),
        )
        crear_notificacion_asignacion(peticion, veterinario_asignado, es_reasignacion=False)

    # 3. Crear notificaciones dentro de la aplicación web para Jurídico / Admin
    crear_notificaciones_nueva_peticion(peticion)

    # 4. Si es una petición urgente, enviar correo automático a Jurídico
    info_correo = enviar_correo_peticion_urgente_juridico(peticion)

    evidencias_urls = list(peticion.evidenciapeticiones_set.values_list('ruta_archivo', flat=True))
    return Response({
        "mensaje": "Petición iniciada con éxito",
        "id_peticion": peticion.id_peticion,
        "numero_radicado": peticion.numero_radicado,
        "id_tipo": peticion.id_tipo.id_tipo if peticion.id_tipo else None,
        "id_ubicacion": peticion.id_ubicacion.id_ubicacion if peticion.id_ubicacion else None,
        "id_estado": peticion.id_estado_id,
        "estado": peticion.id_estado.nombre if peticion.id_estado else None,
        "prioridad": peticion.prioridad,
        "asignado_a": peticion.asignado_a_id,
        "correo_urgente_enviado": bool(info_correo.get("enviado")),
        "foto": peticion.foto,
        "fotos": evidencias_urls or ([peticion.foto] if peticion.foto else []),
        "total_fotos": len(evidencias_urls) if evidencias_urls else (1 if peticion.foto else 0),
    }, status=status.HTTP_201_CREATED)


IniciarPeticionView.create = _juridico_iniciar_peticion_create


# --- Extensión aditiva de AsignarPeticionView (compatibilidad con dev + regla de 5 días para reasignar + historial) ---

_orig_asignar_peticion_update = AsignarPeticionView.update


def _juridico_asignar_peticion_update(self, request, *args, **kwargs):
    peticion = self.get_object()
    es_modelo_real = isinstance(peticion, Peticiones)

    veterinario_anterior = getattr(peticion, 'asignado_a', None)
    estado_anterior = getattr(peticion, 'id_estado', None)
    es_reasignacion = veterinario_anterior is not None

    # Si es una instancia real en BD y ya tenía veterinario asignado, validar regla de 5 días sin avanzar
    if es_modelo_real and es_reasignacion:
        asignado_a_id = request.data.get('asignado_a')
        if asignado_a_id:
            tiempos = calcular_tiempos_peticion(peticion)
            if not tiempos['puede_reasignar']:
                estado_norm = normalizar_texto_juridico(getattr(estado_anterior, 'nombre', '') if estado_anterior else '')
                if estado_norm in ('finalizada', 'resuelta', 'cancelada', 'rechazada', 'alta medica', 'fallecido'):
                    return Response(
                        {
                            'error': f"No se puede reasignar una petición que ya se encuentra en estado '{estado_anterior.nombre}'.",
                            'puede_reasignar': False,
                            'dias_sin_avanzar': tiempos['dias_sin_avanzar'],
                        },
                        status=status.HTTP_400_BAD_REQUEST
                    )
                return Response(
                    {
                        'error': (
                            f"Solo se puede reasignar una petición cuando lleve al menos {DIAS_PARA_REASIGNAR} días sin avanzar. "
                            f"Actualmente lleva {tiempos['dias_sin_avanzar']} día(s) sin avance."
                        ),
                        'puede_reasignar': False,
                        'dias_sin_avanzar': tiempos['dias_sin_avanzar'],
                        'dias_requeridos': DIAS_PARA_REASIGNAR,
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

    response = _orig_asignar_peticion_update(self, request, *args, **kwargs)

    if response.status_code == status.HTTP_200_OK and es_modelo_real:
        peticion_actualizada = (
            Peticiones.objects
            .select_related('id_estado', 'asignado_a', 'asignado_por')
            .filter(pk=peticion.pk)
            .first()
        ) or peticion
        veterinario_nuevo = getattr(peticion_actualizada, 'asignado_a', None)
        tipo_cambio = 'REASIGNACION' if es_reasignacion else 'ASIGNACION'
        observacion_input = request.data.get('observacion') or request.data.get('motivo')
        if not observacion_input and veterinario_nuevo:
            nombre_nuevo = f"{veterinario_nuevo.nombre or ''} {veterinario_nuevo.apellido or ''}".strip()
            if es_reasignacion and veterinario_anterior:
                nombre_ant = f"{veterinario_anterior.nombre or ''} {veterinario_anterior.apellido or ''}".strip()
                observacion_input = f"Reasignada de {nombre_ant} a {nombre_nuevo} tras {DIAS_PARA_REASIGNAR}+ días sin avance."
            else:
                observacion_input = f"Asignada al veterinario {nombre_nuevo}."

        registrar_historial_peticion(
            peticion=peticion_actualizada,
            tipo_cambio=tipo_cambio,
            estado_anterior=estado_anterior,
            estado_nuevo=getattr(peticion_actualizada, 'id_estado', None),
            veterinario_anterior=veterinario_anterior,
            veterinario_nuevo=veterinario_nuevo,
            usuario_accion=request.user,
            observacion=observacion_input,
        )
        if veterinario_nuevo:
            crear_notificacion_asignacion(peticion_actualizada, veterinario_nuevo, es_reasignacion=es_reasignacion)

        if isinstance(response.data, dict):
            response.data['tipo_operacion'] = tipo_cambio
            response.data['estado'] = (
                peticion_actualizada.id_estado.nombre
                if getattr(peticion_actualizada, 'id_estado', None) else None
            )
            if veterinario_nuevo:
                response.data['veterinario_responsable'] = (
                    f"{veterinario_nuevo.nombre or ''} {veterinario_nuevo.apellido or ''}".strip()
                )

    return response


AsignarPeticionView.update = _juridico_asignar_peticion_update


# --- Extensión aditiva de ActualizarEstadoPeticionView (registro automático en historial_peticiones) ---

_orig_actualizar_estado_patch = ActualizarEstadoPeticionView.patch


def _juridico_actualizar_estado_patch(self, request, id_peticion):
    peticion_prev = (
        Peticiones.objects
        .select_related('id_estado', 'asignado_a')
        .filter(pk=id_peticion)
        .first()
    )
    estado_anterior = peticion_prev.id_estado if peticion_prev else None
    vet_anterior = peticion_prev.asignado_a if peticion_prev else None

    response = _orig_actualizar_estado_patch(self, request, id_peticion)

    if response.status_code == status.HTTP_200_OK:
        peticion_actual = (
            Peticiones.objects
            .select_related('id_estado', 'asignado_a')
            .filter(pk=id_peticion)
            .first()
        )
        if peticion_actual:
            obs = request.data.get('observacion') or f"Cambio de estado a '{peticion_actual.id_estado.nombre}'."
            registrar_historial_peticion(
                peticion=peticion_actual,
                tipo_cambio='CAMBIO_ESTADO',
                estado_anterior=estado_anterior,
                estado_nuevo=peticion_actual.id_estado,
                veterinario_anterior=vet_anterior,
                veterinario_nuevo=peticion_actual.asignado_a,
                usuario_accion=request.user,
                observacion=obs,
            )

    return response


ActualizarEstadoPeticionView.patch = _juridico_actualizar_estado_patch
