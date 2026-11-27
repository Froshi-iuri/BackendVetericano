from django.conf import settings
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
    """
    Permite consultar o reintentar triajes únicamente a Administrador (1) o Jurídico (2).
    Si `TRIAJE_AISLAR_POR_SEDE=True`, exige sede activa coincidente cuando usuario y
    petición están asociados a una sede.
    """

    message = 'Solo los roles Administrador y Jurídico pueden consultar el triaje inteligente.'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not es_admin_o_juridico(user):
            return False
        if getattr(settings, 'TRIAJE_AISLAR_POR_SEDE', False):
            sede_user = getattr(user, 'id_sede', None)
            if sede_user is not None and getattr(sede_user, 'activo', True) is False:
                self.message = 'Su sede asignada se encuentra inactiva.'
                return False
        return True

    def has_object_permission(self, request, view, obj):
        if not self.has_permission(request, view):
            return False
        if not getattr(settings, 'TRIAJE_AISLAR_POR_SEDE', False):
            return True
        user = getattr(request, 'user', None)
        user_sede_id = getattr(user, 'id_sede_id', None)
        if user_sede_id is None:
            return True
        peticion = getattr(obj, 'peticion', obj)
        responsable = getattr(peticion, 'responsable', None)
        pet_sede_id = getattr(responsable, 'id_sede_id', None) if responsable else None
        if pet_sede_id is not None and pet_sede_id != user_sede_id:
            self.message = 'No tiene permiso para acceder a peticiones de otra sede.'
            return False
        return True
