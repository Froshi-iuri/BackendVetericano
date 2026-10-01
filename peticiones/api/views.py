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
    ListarPeticionesSerializer, DetallePeticionSerializer
)


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
        queryset = Peticiones.objects.select_related('id_tipo', 'id_estado', 'id_ubicacion', 'asignado_a').all()
        
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

        return Response({
            "mensaje": "Petición iniciada con éxito",
            "id_peticion": peticion.id_peticion,
            "id_tipo": peticion.id_tipo.id_tipo if peticion.id_tipo else None,
            "id_ubicacion": peticion.id_ubicacion.id_ubicacion if peticion.id_ubicacion else None,
            "foto": peticion.foto
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
        return Response(
            {
                "mensaje": "Acta de visita registrada exitosamente.",
                "id_seguimiento": seguimiento.id_seguimiento,
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
    ).prefetch_related('funcionarios')
    lookup_field = 'id_seguimiento'

    def update(self, request, *args, **kwargs):
        kwargs['partial'] = True  # Siempre parcial (PATCH)
        return super().update(request, *args, **kwargs)


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
    queryset = Peticiones.objects.select_related('id_tipo', 'id_estado', 'id_ubicacion', 'responsable', 'asignado_a').all()
    serializer_class = DetallePeticionSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id_peticion'


class ActualizarEstadoPeticionView(APIView):
    """
    PATCH /api/peticiones/<id_peticion>/estado/
    Actualiza el estado de una petición por parte del veterinario o personal a cargo.
    Body:
    {
        "estado": "En tratamiento",
        "observacion": "Motivo opcional del cambio"
    }
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, id_peticion):
        try:
            peticion = Peticiones.objects.get(pk=id_peticion)
        except Peticiones.DoesNotExist:
            return Response({"error": "Petición no encontrada."}, status=status.HTTP_404_NOT_FOUND)

        nuevo_estado_nombre = request.data.get('estado')
        if not nuevo_estado_nombre:
            return Response({"error": "Debe especificar el nuevo estado."}, status=status.HTTP_400_BAD_REQUEST)

        # Buscar estado existente por nombre (case-insensitive) o crear uno dinámicamente
        estado_obj = EstadoPeticiones.objects.filter(nombre__iexact=nuevo_estado_nombre).first()
        if not estado_obj:
            estado_obj = EstadoPeticiones.objects.create(
                nombre=nuevo_estado_nombre,
                activo=True
            )

        peticion.id_estado = estado_obj

        observacion = request.data.get('observacion')
        if observacion:
            # Guardamos trazabilidad en seguimiento si se provee observación
            SeguimientoPeticionesVisita.objects.create(
                id_peticion=peticion,
                id_veterinario=request.user,
                observacion=f"Cambio de estado a '{estado_obj.nombre}': {observacion}"
            )

        peticion.save(update_fields=['id_estado'])

        return Response({
            "mensaje": f"Estado actualizado a '{estado_obj.nombre}' exitosamente.",
            "id_peticion": peticion.id_peticion,
            "estado": estado_obj.nombre
        }, status=status.HTTP_200_OK)

