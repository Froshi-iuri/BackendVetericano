"""
Permisos institucionales basados en Roles para el proyecto VETERICANO.
Centro de Bienestar Animal (CBA) Popayán.

Roles:
1: Administrador - Acceso completo al sistema.
2: Jurídico - Gestión de peticiones (asignación/reasignación), gestión de inventario de medicamentos.
3: Veterinario - Atención clínica, historias clínicas, su propio maletín/kit, peticiones asignadas, catálogos.
4: Peticionario - Aplicación móvil: registro de peticiones, consulta de sus propias peticiones y perfil.
5: super_administrador (Super Administrador) - Gestión de administradores, sedes y reportes globales.
"""

from typing import Any, Tuple
from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.exceptions import PermissionDenied

from users.constants import (
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_VETERINARIO,
    ROL_PETICIONARIO,
    ROL_SUPER_ADMINISTRADOR,
    ROLES_GESTION_PETICIONES,
    ROLES_GESTION_INVENTARIO,
    ROLES_ATENCION_CLINICA,
    ROLES_STAFF,
)

NOMBRES_ROL_SUPER_ADMIN = {
    'super administrador',
    'superadministrador',
    'super_administrador',
    'super-administrador',
    'super admin',
    'superadmin',
}

ROLES_NO_SUPER_ADMIN = (
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_VETERINARIO,
    ROL_PETICIONARIO,
)


def resolver_usuario_activo_y_rol(user: Any) -> Tuple[bool, Any]:
    """
    Función utilitaria canónica para resolver de forma segura el estado activo
    y el identificador de rol de request.user sin riesgo de AttributeError ni consultas extra.
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


def es_administrador(user: Any) -> bool:
    es_activo, rol_id = resolver_usuario_activo_y_rol(user)
    return es_activo and rol_id == ROL_ADMINISTRADOR


def es_super_administrador(user: Any) -> bool:
    """
    Verifica que el usuario autenticado esté activo y posea el rol
    Super Administrador (ROL_SUPER_ADMINISTRADOR = 5 o nombre_rol equivalente).
    Bloquea explícitamente a los roles Administrador (1), Jurídico (2),
    Veterinario (3) y Peticionario (4).
    """
    es_activo, rol_id = resolver_usuario_activo_y_rol(user)
    if not es_activo:
        return False

    if rol_id in ROLES_NO_SUPER_ADMIN:
        return False

    if rol_id == ROL_SUPER_ADMINISTRADOR:
        return True

    rol_obj = getattr(user, 'id_rol', None)
    nombre_rol = (getattr(rol_obj, 'nombre_rol', '') or '').strip().lower()
    return nombre_rol in NOMBRES_ROL_SUPER_ADMIN


def es_juridico(user: Any) -> bool:
    es_activo, rol_id = resolver_usuario_activo_y_rol(user)
    return es_activo and rol_id == ROL_JURIDICO


def es_veterinario(user: Any) -> bool:
    es_activo, rol_id = resolver_usuario_activo_y_rol(user)
    return es_activo and rol_id == ROL_VETERINARIO


def es_peticionario(user: Any) -> bool:
    es_activo, rol_id = resolver_usuario_activo_y_rol(user)
    return es_activo and rol_id == ROL_PETICIONARIO


def es_admin_o_juridico(user: Any) -> bool:
    es_activo, rol_id = resolver_usuario_activo_y_rol(user)
    return es_activo and rol_id in ROLES_GESTION_PETICIONES


def es_admin_o_veterinario(user: Any) -> bool:
    es_activo, rol_id = resolver_usuario_activo_y_rol(user)
    return es_activo and rol_id in ROLES_ATENCION_CLINICA


def es_staff(user: Any) -> bool:
    es_activo, rol_id = resolver_usuario_activo_y_rol(user)
    return es_activo and rol_id in ROLES_STAFF


# ==============================================================================
# CLASES DE PERMISO POR ROL
# ==============================================================================

class EsAdministrador(BasePermission):
    """Acceso exclusivo al Administrador del sistema (Rol 1)."""
    message = "Esta operación está reservada exclusivamente para el Administrador del sistema."

    def has_permission(self, request, view):
        return es_administrador(request.user)


class EsSuperAdministrador(BasePermission):
    """
    Acceso exclusivo al Super Administrador del sistema (Rol 5: super_administrador).
    Los usuarios con roles Administrador (1), Jurídico (2), Veterinario (3) y Peticionario (4)
    no pueden acceder a endpoints exclusivos del Super Administrador (administradores, reportes, sedes).
    """
    message = "Esta operación está reservada exclusivamente para el Super Administrador del sistema."

    def has_permission(self, request, view):
        return es_super_administrador(request.user)

    def has_object_permission(self, request, view, obj):
        return es_super_administrador(request.user)


class EsJuridicoOAdmin(BasePermission):
    """Acceso para Administrador (Rol 1) o Jurídico (Rol 2)."""
    message = "Se requieren permisos de Administrador o Jurídico para realizar esta acción."

    def has_permission(self, request, view):
        return es_admin_o_juridico(request.user)


class EsVeterinarioOAdmin(BasePermission):
    """Acceso para Administrador (Rol 1) o Veterinario (Rol 3)."""
    message = "Se requieren permisos de Administrador o Veterinario para realizar esta acción."

    def has_permission(self, request, view):
        return es_admin_o_veterinario(request.user)


class EsStaff(BasePermission):
    """Acceso para usuarios institucionales activos (Admin, Jurídico, Veterinario)."""
    message = "Se requiere pertenecer al personal institucional activo (Staff) para acceder a este recurso."

    def has_permission(self, request, view):
        return es_staff(request.user)


class EsAdministradorOGestionUsuarios(BasePermission):
    """
    Permiso para el módulo de usuarios y roles:
    - Administrador (Rol 1): Acceso completo a usuarios y roles (GET, POST, PUT, PATCH, DELETE).
    - Otros roles activos (Jurídico, Veterinario, Peticionario):
      * Solo pueden consultar (GET) o actualizar (PUT, PATCH) su propio registro en /api/usuarios/usuarios/<su_id>/ (sin alterar id_rol ni activo).
      * No pueden listar todos los usuarios, consultar otros usuarios, crear/eliminar usuarios ni acceder a /api/usuarios/roles/.
    """
    message = "No cuenta con los privilegios requeridos para realizar esta operación. La administración de usuarios y roles está reservada para el Administrador."

    def has_permission(self, request, view):
        es_activo, rol_id = resolver_usuario_activo_y_rol(request.user)
        if not es_activo:
            return False

        if es_administrador(request.user) or es_super_administrador(request.user):
            return True

        # Para roles distintos de Administrador, el endpoint de roles está completamente restringido
        if getattr(view, 'basename', '') == 'roles':
            raise PermissionDenied(self.message)

        # Permitir únicamente retrieve, update y partial_update sobre su propio id_usuario
        action = getattr(view, 'action', None)
        pk = view.kwargs.get('pk')
        es_propio_id = pk is not None and str(getattr(request.user, 'id_usuario', None)) == str(pk)

        if es_propio_id and (action in ['retrieve', 'update', 'partial_update'] or request.method in (*SAFE_METHODS, 'PUT', 'PATCH')):
            return True

        raise PermissionDenied(self.message)

    def has_object_permission(self, request, view, obj):
        if es_administrador(request.user) or es_super_administrador(request.user):
            return True

        es_activo, _ = resolver_usuario_activo_y_rol(request.user)
        if not es_activo:
            return False

        if getattr(view, 'basename', '') == 'roles':
            return False

        if request.method in (*SAFE_METHODS, 'PUT', 'PATCH'):
            return getattr(obj, 'id_usuario', None) == getattr(request.user, 'id_usuario', None)

        return False


class EsAdminOReadOnlyParaStaff(BasePermission):
    """
    Permiso para catálogos clínicos y taxonómicos (especies, razas, patologías, exámenes de catálogo):
    - Admin: Lectura y escritura completa (POST, PUT, PATCH, DELETE, GET).
    - Veterinario: Solo lectura (GET, HEAD, OPTIONS) para su labor clínica.
    - Jurídico y Peticionario: Denegado.
    """
    message = "No cuenta con los privilegios requeridos para acceder o modificar este catálogo clínico."

    def has_permission(self, request, view):
        es_activo, rol_id = resolver_usuario_activo_y_rol(request.user)
        if not es_activo:
            return False

        if es_administrador(request.user):
            return True

        if request.method in SAFE_METHODS:
            return rol_id in ROLES_ATENCION_CLINICA

        return False


class PermisoClinica(BasePermission):
    """
    Permiso para módulos clínicos (historias clínicas, consultas, hospitalización, tratamientos, diagnósticos, etc.):
    - Admin y Veterinario: Acceso completo (lectura y registro de atención clínica).
    - Jurídico: Denegado en modificación clínica. Solo lectura si forma parte de consultas generales autorizadas.
    - Peticionario: Denegado rotundamente.
    """
    message = "Acceso reservado a personal médico veterinario y administración. No cuenta con permisos para modificar la historia clínica."

    def has_permission(self, request, view):
        es_activo, rol_id = resolver_usuario_activo_y_rol(request.user)
        if not es_activo:
            return False

        # Admin y Veterinario tienen acceso para ver y registrar atenciones clínicas
        if rol_id in ROLES_ATENCION_CLINICA:
            return True

        # Jurídico no puede modificar historias clínicas; denegado en mutaciones
        return False


class PermisoAnimales(BasePermission):
    """
    Permiso para gestión de animales y responsables:
    - Admin y Veterinario: lectura y creación/actualización durante atención clínica y actas.
    - Jurídico y Peticionario: denegado (Jurídico usa /api/peticiones/dashboard-juridico/ y /api/adopciones/).
    """
    message = "No cuenta con permisos para gestionar este recurso de animales."

    def has_permission(self, request, view):
        es_activo, rol_id = resolver_usuario_activo_y_rol(request.user)
        if not es_activo:
            return False

        return rol_id in ROLES_ATENCION_CLINICA


class PermisoAdopciones(BasePermission):
    """
    Permiso para el catálogo de animales disponibles para adopción:
    - Lectura (GET): pública o autenticada (usado por web y móvil para ver mascotas adoptables).
    - Escritura (POST, PUT, PATCH, DELETE): reservada exclusivamente al Administrador.
    """
    message = "Solo el Administrador puede gestionar los animales en adopción."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True

        return es_administrador(request.user)


class PermisoVoluntariado(BasePermission):
    """
    Permiso para el módulo de voluntariado y eventos:
    - Lectura de eventos (GET en EventoVoluntariadoViewSet, acción list/retrieve): pública o autenticada.
    - Postularse (POST a /postularse/): cualquier usuario autenticado y activo.
    - Consulta de postulados y gestión de eventos/postulaciones: reservada exclusivamente al Administrador.
    """
    message = "Se requieren permisos de Administrador para gestionar el módulo de voluntariado."

    def has_permission(self, request, view):
        action = getattr(view, 'action', None)
        if action == 'postularse':
            es_activo, _ = resolver_usuario_activo_y_rol(request.user)
            return es_activo

        # Lectura pública solo para listado/detalle de eventos de voluntariado, NO para postulados ni postulaciones
        view_name = getattr(view, '__class__', type(view)).__name__
        if request.method in SAFE_METHODS and view_name == 'EventoVoluntariadoViewSet' and action in ['list', 'retrieve', None]:
            return True

        return es_administrador(request.user)


# Re-export para retrocompatibilidad directa con importaciones anteriores
EsAdministradorOReadOnly = EsAdministradorOGestionUsuarios
IsJuridicoOrAdmin = EsJuridicoOAdmin
