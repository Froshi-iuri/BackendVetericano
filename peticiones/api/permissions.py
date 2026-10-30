from rest_framework.permissions import BasePermission


class IsJuridicoOrAdmin(BasePermission):
    """
    Permiso que valida que el usuario autenticado pertenezca al rol
    Administrador (id_rol = 1) o Jurídico (id_rol = 2).
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # id_rol_id: 1 = Administrador, 2 = Jurídico
        rol_id = getattr(request.user, 'id_rol_id', None)
        if rol_id is None and hasattr(request.user, 'id_rol'):
            rol_obj = getattr(request.user, 'id_rol')
            rol_id = getattr(rol_obj, 'id_rol', None) if rol_obj else None

        return rol_id in [1, 2]
