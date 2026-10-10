import unicodedata
import logging
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from peticiones.models import TipoPeticion, Peticiones, EstadoPeticiones, SeguimientoPeticionesVisita
from users.models import Usuarios
from users.api.serializers import UsuariosSerializer
from django.utils import timezone
from .serializers import (
    TiposPeticionSerializer, IniciarPeticionSerializer,
    SeguimientoPeticionesVisitaSerializer, AsignarPeticionSerializer,
    ListarPeticionesSerializer, DetallePeticionSerializer,
    ListarSeguimientoPeticionesVisitaSerializer,
)
from peticiones.utils import obtener_fotos_acta_con_fallback

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


class SeguimientoPeticionesVisitaCreateView(generics.CreateAPIView):
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
        fotos = obtener_fotos_acta_con_fallback(seguimiento)
        return Response(
            {
                "mensaje": "Acta de visita registrada exitosamente.",
                "id_seguimiento": seguimiento.id_seguimiento,
                "foto": fotos[0] if fotos else None,
                "fotos": fotos,
                "total_fotos": len(fotos),
            },
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
    ).prefetch_related('funcionarios', 'id_peticion__evidenciapeticiones_set')
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
            .prefetch_related('funcionarios', 'id_peticion__evidenciapeticiones_set')
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
    Permite a un administrador asignar la petición a un veterinario.
    """
    queryset = Peticiones.objects.all()
    serializer_class = AsignarPeticionSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id_peticion'

    def update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        peticion = self.get_object()
        
        # Obtenemos los IDs y verificamos
        asignado_a_id = request.data.get('asignado_a')
        if not asignado_a_id:
            return Response({'error': 'Debe proveer el id del usuario asignado (asignado_a).'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Validar que el usuario a asignar sea un veterinario (rol 3)
        veterinario = Usuarios.objects.filter(id_usuario=asignado_a_id).first()
        if not veterinario or veterinario.id_rol_id != 3:
            return Response({'error': 'El usuario asignado no es un veterinario válido.'}, status=status.HTTP_400_BAD_REQUEST)

        # Actualizar datos de asignación
        serializer = self.get_serializer(peticion, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        
        # Modificamos manualmente los campos que no vienen en el request
        serializer.save(
            asignado_por=request.user,
            fecha_asignacion=timezone.now()
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

