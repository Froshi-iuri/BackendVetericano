"""
Clases de Permiso por Rol para el Módulo de Inventario.
Proyecto VETERICANO - Centro de Bienestar Animal (CBA) Popayán.

Define las políticas de control de acceso basadas en roles institucionales (R10):
- EsGestorInventario: Administrador/Gestor autorizado para dotación, recepción y gestión de bodega.
- EsVeterinario: Profesional médico veterinario titular de maletín/kit de campo.
- EsGestorOVeterinario: Acceso compartido entre gestores y veterinarios (sin delegación de propiedad de objeto).
- EsGestorOLecturaAutenticado: Lectura abierta a todo usuario autenticado y activo; mutaciones reservadas a gestor.
"""

from typing import Any, Tuple
from rest_framework.permissions import BasePermission, SAFE_METHODS

from inventario.constants import (
    INVENTARIO_GESTOR_ROLES,
    ROL_VETERINARIO,
    INVENTARIO_LECTURA_LEGACY_ROLES,
    INVENTARIO_AJUSTE_STOCK_LEGACY_ROLES,
)


def _resolver_usuario_activo_y_rol(user: Any) -> Tuple[bool, Any]:
    """
    Función auxiliar privada para resolver de forma segura el estado activo
    y el identificador de rol de request.user sin riesgo de AttributeError.

    Retorna:
    - (True, rol_id) si el usuario existe, está autenticado y activo (user.activo == True).
    - (False, None) si el usuario es anónimo, nulo, inactivo o carece de atributos esperados.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False, None

    activo = bool(getattr(user, 'activo', False))
    if not activo:
        return False, None

    # En users.models.Usuarios el campo es ForeignKey id_rol,
    # por lo que id_rol_id contiene directamente el entero sin resolver relaciones.
    rol_id = getattr(user, 'id_rol_id', None)
    if rol_id is None:
        rol_obj = getattr(user, 'id_rol', None)
        if rol_obj is not None:
            rol_id = getattr(rol_obj, 'id_rol', None)

    return True, rol_id


def _es_gestor(user: Any) -> bool:
    """Única fuente de verdad: determina si el usuario está autenticado, activo y posee rol de gestor."""
    es_activo, rol_id = _resolver_usuario_activo_y_rol(user)
    return es_activo and (rol_id in INVENTARIO_GESTOR_ROLES)


def _es_veterinario(user: Any) -> bool:
    """Única fuente de verdad: determina si el usuario está autenticado, activo y posee rol de veterinario."""
    es_activo, rol_id = _resolver_usuario_activo_y_rol(user)
    return es_activo and (rol_id == ROL_VETERINARIO)


class EsGestorInventario(BasePermission):
    """
    Permiso que autoriza exclusivamente a usuarios autenticados, activos
    y cuyo rol figure en INVENTARIO_GESTOR_ROLES.
    """
    message = "Se requieren permisos de gestor de inventario para realizar esta acción."

    def has_permission(self, request, view) -> bool:
        return _es_gestor(request.user)


class EsVeterinario(BasePermission):
    """
    Permiso que autoriza exclusivamente a usuarios autenticados, activos
    y cuyo rol sea estrictamente ROL_VETERINARIO.
    """
    message = "Se requieren permisos de veterinario activo para realizar esta acción."

    def has_permission(self, request, view) -> bool:
        return _es_veterinario(request.user)


class EsGestorOVeterinario(BasePermission):
    """
    Permiso que autoriza a usuarios autenticados y activos que sean gestores
    de inventario o veterinarios.

    Nota: No decide propiedad sobre recursos específicos (la propiedad por veterinario
    o la autorización sobre solicitudes individuales la resuelven los servicios de dominio).
    """
    message = "Se requiere rol de gestor de inventario o veterinario activo para realizar esta acción."

    def has_permission(self, request, view) -> bool:
        return _es_gestor(request.user) or _es_veterinario(request.user)


class EsGestorOLecturaAutenticado(BasePermission):
    """
    Permiso que autoriza operaciones de solo lectura (GET, HEAD, OPTIONS) a cualquier
    usuario autenticado y activo, reservando las operaciones de escritura o mutación
    (POST, PUT, PATCH, DELETE) exclusivamente a gestores de inventario.
    """
    message = "Se requieren permisos de gestor de inventario para modificar estos datos."

    def has_permission(self, request, view) -> bool:
        es_activo, _ = _resolver_usuario_activo_y_rol(request.user)
        if not es_activo:
            return False

        if request.method in SAFE_METHODS:
            return True

        return _es_gestor(request.user)

    def has_object_permission(self, request, view, obj) -> bool:
        es_activo, _ = _resolver_usuario_activo_y_rol(request.user)
        if not es_activo:
            return False

        if request.method in SAFE_METHODS:
            return True

        return _es_gestor(request.user)


class EsVeterinarioDuenioDelKit(BasePermission):
    """
    Permiso que autoriza exclusivamente a un veterinario activo a acceder
    a los recursos de su propio kit o de sus propias solicitudes.
    """
    message = "Acceso denegado: solo el veterinario titular puede acceder a los recursos de su kit."

    def has_permission(self, request, view) -> bool:
        return _es_veterinario(request.user)

    def has_object_permission(self, request, view, obj) -> bool:
        if not _es_veterinario(request.user):
            return False
        vet_id = getattr(obj, 'id_veterinario_id', None)
        if vet_id is None:
            vet_obj = getattr(obj, 'id_veterinario', None)
            if hasattr(vet_obj, 'id_usuario'):
                vet_id = vet_obj.id_usuario
            elif isinstance(vet_obj, int):
                vet_id = vet_obj
        if vet_id is not None:
            return vet_id == request.user.id_usuario
        return False


class EsGestorOLecturaStaffLegacy(BasePermission):
    """
    Permiso para viewsets legacy de inventario (proveedores, compras, salidas, etc.):
    autoriza operaciones de solo lectura (SAFE_METHODS: GET, HEAD, OPTIONS) a usuarios
    activos de staff (roles en INVENTARIO_LECTURA_LEGACY_ROLES: Admin, Juridico, Veterinario),
    y reserva las mutaciones (POST, PUT, PATCH, DELETE) exclusivamente a gestores de inventario.
    Existe para mantener compatibilidad con la web previa a la migracion al kit.
    Politica fail-closed: si el usuario no es activo o su rol es desconocido/None, retorna False.
    """
    message = "Se requieren permisos de gestor de inventario o rol de staff autorizado para esta accion."

    def has_permission(self, request, view) -> bool:
        es_activo, rol_id = _resolver_usuario_activo_y_rol(request.user)
        if not es_activo or rol_id is None:
            return False

        if request.method in SAFE_METHODS:
            return rol_id in INVENTARIO_LECTURA_LEGACY_ROLES

        return _es_gestor(request.user)

    def has_object_permission(self, request, view, obj) -> bool:
        es_activo, rol_id = _resolver_usuario_activo_y_rol(request.user)
        if not es_activo or rol_id is None:
            return False

        if request.method in SAFE_METHODS:
            return rol_id in INVENTARIO_LECTURA_LEGACY_ROLES

        return _es_gestor(request.user)


class PermisoInventariosLegacy(BasePermission):
    """
    Permiso para el viewset legacy de inventarios (InventariosViewSet):
    - Gestor: permitido en todos los metodos (POST, PUT, PATCH, DELETE, GET, etc.).
    - Metodos SAFE (GET, HEAD, OPTIONS): permitido si el rol esta en INVENTARIO_LECTURA_LEGACY_ROLES.
    - Metodo PATCH: permitido si el rol esta en INVENTARIO_AJUSTE_STOCK_LEGACY_ROLES (Veterinario, Juridico),
      delegando las restricciones de contenido especificas (solo reducir stock/estado) al serializer.
    - Cualquier otra combinacion (POST, PUT, DELETE de no gestor; Peticionario en cualquier metodo): denegado (False).
    Existe para mantener compatibilidad con la web previa a la migracion al kit (acta web y consulta de lotes).
    Politica fail-closed: usuarios inactivos o roles nulos/desconocidos reciben False.
    """
    message = "Acceso restringido: no posee permisos para realizar esta operacion sobre el inventario."

    def has_permission(self, request, view) -> bool:
        es_activo, rol_id = _resolver_usuario_activo_y_rol(request.user)
        if not es_activo or rol_id is None:
            return False

        if _es_gestor(request.user):
            return True

        if request.method in SAFE_METHODS:
            return rol_id in INVENTARIO_LECTURA_LEGACY_ROLES

        if request.method == 'PATCH':
            return rol_id in INVENTARIO_AJUSTE_STOCK_LEGACY_ROLES

        return False

    def has_object_permission(self, request, view, obj) -> bool:
        es_activo, rol_id = _resolver_usuario_activo_y_rol(request.user)
        if not es_activo or rol_id is None:
            return False

        if _es_gestor(request.user):
            return True

        if request.method in SAFE_METHODS:
            return rol_id in INVENTARIO_LECTURA_LEGACY_ROLES

        if request.method == 'PATCH':
            return rol_id in INVENTARIO_AJUSTE_STOCK_LEGACY_ROLES

        return False


