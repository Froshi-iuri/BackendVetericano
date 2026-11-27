from rest_framework.permissions import BasePermission

from triaje_ia.authentication import TriajeServicePrincipal
from users.api.permissions import es_admin_o_juridico


class EsServicioTriaje(BasePermission):
    """Permite acceso únicamente al principal técnico autenticado por token de servicio."""

    message = 'Acceso restringido al servicio interno de triaje.'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        return isinstance(user, TriajeServicePrincipal) and bool(getattr(user, 'es_servicio_triaje', False))


class EsJuridicoOAdminTriaje(BasePermission):
    """Permite consultar o reintentar triajes únicamente a Administrador (1) o Jurídico (2)."""

    message = 'Solo los roles Administrador y Jurídico pueden consultar el triaje inteligente.'

    def has_permission(self, request, view):
        return es_admin_o_juridico(getattr(request, 'user', None))
