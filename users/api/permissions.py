from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.exceptions import PermissionDenied


class EsAdministradorOReadOnly(BasePermission):
    """
    Permiso que permite acceso de lectura (GET, HEAD, OPTIONS) a cualquier usuario,
    pero restringe la creación y modificación (POST, PUT, PATCH, DELETE) exclusivamente
    al usuario con rol de Administrador.
    """
    message = "No cuenta con los privilegios requeridos para realizar esta operación. La gestión y modificación de usuarios y roles está reservada exclusivamente para el Administrador del sistema."

    def has_permission(self, request, view):
        # Permitir listar o consultar detalles de roles a cualquier usuario (lectura pública)
        if request.method in SAFE_METHODS:
            return True

        # Si no está autenticado, denegar con el mensaje profesional
        if not request.user or not request.user.is_authenticated:
            raise PermissionDenied(self.message)

        # Validar el rol del usuario autenticado
        rol = getattr(request.user, 'id_rol', None)
        nombre_rol = getattr(rol, 'nombre_rol', '').strip().lower() if rol else ''
        id_rol = getattr(rol, 'id_rol', None) if rol else None

        # Solo el Administrador tiene autorización
        if nombre_rol not in ['administrador', 'admin'] and id_rol != 1:
            raise PermissionDenied(self.message)

        return True
