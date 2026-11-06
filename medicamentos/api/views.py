from rest_framework import viewsets, filters
from rest_framework.permissions import BasePermission, SAFE_METHODS
from medicamentos.models import (
    Medicamento,
    Medicamentos,
    AdministracionMedicamento,
    TratamientoMedicamentos,
)
from users.api.permissions import (
    resolver_usuario_activo_y_rol,
    es_admin_o_juridico,
    es_admin_o_veterinario,
    ROLES_GESTION_INVENTARIO,
    ROLES_ATENCION_CLINICA,
    ROLES_STAFF,
)
from .serializers import (
    MedicamentoSerializer,
    MedicamentosSerializer,
    AdministracionMedicamentoSerializer,
    TratamientoMedicamentosSerializer,
)


class PermisoMedicamentosCatalogo(BasePermission):
    """
    Catálogo de medicamentos de farmacia (Medicamento y Medicamentos):
    - Admin y Jurídico: Acceso completo (gestión de inventario de medicamentos).
    - Veterinario: Solo lectura (SAFE_METHODS: GET, HEAD, OPTIONS).
    - Peticionario: Denegado.
    """
    message = "Acceso denegado: solo el Administrador y Jurídico pueden modificar el catálogo de medicamentos."

    def has_permission(self, request, view):
        es_activo, rol_id = resolver_usuario_activo_y_rol(request.user)
        if not es_activo:
            return False

        if es_admin_o_juridico(request.user):
            return True

        if request.method in SAFE_METHODS:
            return rol_id in ROLES_STAFF

        return False


class PermisoTratamientoMedicamento(BasePermission):
    """
    Administración y consumo clínico de medicamentos (tratamientos del paciente):
    - Admin y Veterinario: Acceso completo (prescribir/administrar medicamentos en atención clínica).
    - Jurídico y Peticionario: Denegado.
    """
    message = "Acceso denegado: la administración clínica de medicamentos corresponde al médico veterinario."

    def has_permission(self, request, view):
        es_activo, rol_id = resolver_usuario_activo_y_rol(request.user)
        if not es_activo:
            return False

        return es_admin_o_veterinario(request.user)


class MedicamentoViewSet(viewsets.ModelViewSet):
    queryset = Medicamento.objects.all()
    serializer_class = MedicamentoSerializer
    permission_classes = [PermisoMedicamentosCatalogo]


class MedicamentosViewSet(viewsets.ModelViewSet):
    """
    Catálogo de medicamentos (tabla `medicamentos`).

    Filtros opcionales (si no se envían, la respuesta es la misma de siempre):
      - ?search=texto      -> busca en nombre y principio_activo
      - ?activo=true|false -> filtra por estado
      - ?tipo=valor        -> filtra por tipo (sin distinguir mayúsculas)
      - ?ordering=nombre   -> ordena (prefijo '-' para descendente)
    """
    queryset = Medicamentos.objects.all()
    serializer_class = MedicamentosSerializer
    permission_classes = [PermisoMedicamentosCatalogo]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre', 'principio_activo']
    ordering_fields = ['id_medicamento', 'nombre', 'tipo', 'activo']

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        activo = params.get('activo')
        if activo is not None:
            valor = activo.strip().lower()
            if valor in ('true', '1'):
                queryset = queryset.filter(activo=True)
            elif valor in ('false', '0'):
                queryset = queryset.filter(activo=False)

        tipo = params.get('tipo')
        if tipo:
            queryset = queryset.filter(tipo__iexact=tipo.strip())

        return queryset


class AdministracionMedicamentoViewSet(viewsets.ModelViewSet):
    queryset = AdministracionMedicamento.objects.all()
    serializer_class = AdministracionMedicamentoSerializer
    permission_classes = [PermisoTratamientoMedicamento]


class TratamientoMedicamentosViewSet(viewsets.ModelViewSet):
    queryset = TratamientoMedicamentos.objects.all()
    serializer_class = TratamientoMedicamentosSerializer
    permission_classes = [PermisoTratamientoMedicamento]
