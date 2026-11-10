"""
Pruebas del módulo Super Administrador: Gestión de Sedes (/api/sedes/).
Cubre:
1. GET /api/sedes/ para listar sedes activas e inactivas con total_usuarios.
2. POST /api/sedes/ para crear una sede (asignación automática activo=True y normalización).
3. POST /api/sedes/ intentando crear una sede duplicada (nombre y código insensibles a mayúsculas/espacios).
4. GET /api/sedes/{id}/ para consultar una sede específica.
5. PATCH /api/sedes/{id}/ para inactivar una sede ({"activo": false}).
6. PATCH /api/sedes/{id}/ para reactivar una sede ({"activo": true}).
7. Bloqueo con HTTP 403 para roles sin permiso (Administrador, Jurídico, Veterinario, Peticionario).
8. Bloqueo con HTTP 401 para solicitudes no autenticadas.
9. Integridad de usuarios asociados al inactivar una sede y bloqueo de manipulación de campos internos.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from django.urls import reverse, resolve
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from users.constants import (
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_VETERINARIO,
    ROL_PETICIONARIO,
    ROL_SUPER_ADMINISTRADOR,
)
from users.models import Sede, Usuarios
from users.api.views import SedeViewSet
from users.api.serializers import SedeSerializer, SedeEstadoSerializer


class DummyUserSede:
    """Usuario simulado para pruebas HTTP sobre vistas DRF."""
    _meta = Usuarios._meta

    def __init__(self, id_usuario, rol_id, nombre_rol=None, activo=True):
        self.id_usuario = id_usuario
        self.pk = id_usuario
        self.id_rol_id = rol_id
        default_names = {
            ROL_ADMINISTRADOR: "Administrador",
            ROL_JURIDICO: "Juridico",
            ROL_VETERINARIO: "Veterinario",
            ROL_PETICIONARIO: "Peticionario",
            ROL_SUPER_ADMINISTRADOR: "Super Administrador",
        }
        self.id_rol = MagicMock(
            id_rol=rol_id,
            nombre_rol=nombre_rol or default_names.get(rol_id, "Desconocido"),
        )
        self.activo = activo
        self.is_authenticated = True
        self.is_anonymous = False
        self.email = f"user{id_usuario}@vetericano.gov.co"
        self.nombre = f"Usuario {id_usuario}"
        self.apellido = "Prueba"


class GestionSedesHTTPTestCase(SimpleTestCase):
    """Pruebas HTTP completas sobre SedeViewSet y SedeSerializer."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.super_admin = DummyUserSede(id_usuario=100, rol_id=ROL_SUPER_ADMINISTRADOR)
        self.admin = DummyUserSede(id_usuario=1, rol_id=ROL_ADMINISTRADOR)
        self.juridico = DummyUserSede(id_usuario=2, rol_id=ROL_JURIDICO)
        self.veterinario = DummyUserSede(id_usuario=3, rol_id=ROL_VETERINARIO)
        self.peticionario = DummyUserSede(id_usuario=4, rol_id=ROL_PETICIONARIO)

    def _dispatch(self, view_callable, method, path="/api/sedes/", user=None, data=None, **kwargs):
        req_fn = getattr(self.factory, method.lower())
        request = req_fn(path, data=data, format="json") if data is not None else req_fn(path)
        if user is not None:
            force_authenticate(request, user=user)
        return view_callable(request, **kwargs)

    def test_01_rutas_sedes_registradas_correctamente(self):
        """Verifica que /api/sedes/ y /api/sedes/{id}/ resuelvan a SedeViewSet."""
        self.assertEqual(reverse("sedes-list"), "/api/sedes/")
        self.assertEqual(reverse("sedes-detail", kwargs={"pk": 1}), "/api/sedes/1/")
        match_list = resolve("/api/sedes/")
        self.assertEqual(match_list.func.cls, SedeViewSet)
        match_detail = resolve("/api/sedes/1/")
        self.assertEqual(match_detail.func.cls, SedeViewSet)

    def test_02_usuario_no_autenticado_recibe_401(self):
        """Comprueba que un usuario no autenticado no pueda listar, crear, consultar ni modificar sedes (401)."""
        list_create = SedeViewSet.as_view({"get": "list", "post": "create"})
        detail = SedeViewSet.as_view({"get": "retrieve", "patch": "partial_update"})

        self.assertEqual(self._dispatch(list_create, "GET", "/api/sedes/", user=None).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self._dispatch(list_create, "POST", "/api/sedes/", user=None, data={}).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self._dispatch(detail, "GET", "/api/sedes/1/", user=None, pk=1).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self._dispatch(detail, "PATCH", "/api/sedes/1/", user=None, data={"activo": False}, pk=1).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_03_roles_sin_permisos_reciben_403(self):
        """
        Comprueba que los roles Administrador (1), Jurídico (2), Veterinario (3)
        y Peticionario (4) reciban HTTP 403 Forbidden en todos los endpoints de sedes.
        """
        list_create = SedeViewSet.as_view({"get": "list", "post": "create"})
        detail = SedeViewSet.as_view({"get": "retrieve", "patch": "partial_update"})

        for u in (self.admin, self.juridico, self.veterinario, self.peticionario):
            with self.subTest(rol=u.id_rol.nombre_rol):
                self.assertEqual(
                    self._dispatch(list_create, "GET", "/api/sedes/", user=u).status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                self.assertEqual(
                    self._dispatch(list_create, "POST", "/api/sedes/", user=u, data={"nombre": "X"}).status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                self.assertEqual(
                    self._dispatch(detail, "GET", "/api/sedes/1/", user=u, pk=1).status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                self.assertEqual(
                    self._dispatch(detail, "PATCH", "/api/sedes/1/", user=u, data={"activo": False}, pk=1).status_code,
                    status.HTTP_403_FORBIDDEN,
                )

    def test_04_listar_sedes_activas_e_inactivas_con_total_usuarios(self):
        """Comprueba que GET /api/sedes/ devuelva sedes activas e inactivas con los campos requeridos por Angular."""
        sede1 = Sede(
            id_sede=1,
            nombre="Sede Principal",
            direccion="Calle 10 # 5-20",
            ciudad="Medellín",
            telefono="3000000000",
            codigo="SED001",
            activo=True,
            fecha_creacion=datetime(2026, 11, 10, 10, 0, 0),
        )
        sede1.total_usuarios_count = 5

        sede2 = Sede(
            id_sede=2,
            nombre="Sede Norte",
            direccion="Carrera 15 # 80-10",
            ciudad="Popayán",
            telefono="3001112233",
            codigo="SED002",
            activo=False,
            fecha_creacion=datetime(2026, 11, 10, 11, 0, 0),
        )
        sede2.total_usuarios_count = 2

        with patch.object(SedeViewSet, "get_queryset", return_value=[sede1, sede2]):
            list_view = SedeViewSet.as_view({"get": "list"})
            resp = self._dispatch(list_view, "GET", "/api/sedes/", user=self.super_admin)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(len(resp.data), 2)
            self.assertEqual(resp.data[0]["id_sede"], 1)
            self.assertEqual(resp.data[0]["nombre"], "Sede Principal")
            self.assertEqual(resp.data[0]["activo"], True)
            self.assertEqual(resp.data[0]["codigo"], "SED001")
            self.assertEqual(resp.data[0]["total_usuarios"], 5)
            self.assertEqual(resp.data[1]["id_sede"], 2)
            self.assertEqual(resp.data[1]["activo"], False)
            self.assertEqual(resp.data[1]["total_usuarios"], 2)

    @patch("users.api.serializers.Sede.objects")
    @patch("users.api.serializers.Usuarios.objects")
    def test_05_crear_sede_normaliza_y_fuerza_activo_true(self, mock_user_objs, mock_sede_objs):
        """
        Comprueba que POST /api/sedes/ normalice espacios, ignore activo=False enviado
        por el cliente y asigne activo=True automáticamente.
        """
        mock_qs = MagicMock()
        mock_qs.filter.return_value.exists.return_value = False
        mock_qs.only.return_value = []
        mock_qs.exclude.return_value.only.return_value = []
        mock_sede_objs.all.return_value = mock_qs
        mock_user_objs.filter.return_value.count.return_value = 0

        def fake_create(**kwargs):
            return Sede(
                id_sede=10,
                fecha_creacion=datetime(2026, 11, 10, 12, 0, 0),
                **kwargs,
            )

        mock_sede_objs.create.side_effect = fake_create

        create_view = SedeViewSet.as_view({"post": "create"})
        payload = {
            "nombre": "  Sede   Principal  ",
            "direccion": "  Dirección   de la sede  ",
            "ciudad": "  Medellín  ",
            "telefono": " 3000000000 ",
            "codigo": "  SED001  ",
            "activo": False,          # Intento de manipular estado inicial
            "id_sede": 999,           # Intento de manipular PK
            "total_usuarios": 100,    # Intento de manipular conteo
        }
        resp = self._dispatch(create_view, "POST", "/api/sedes/", user=self.super_admin, data=payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["id_sede"], 10)
        self.assertEqual(resp.data["nombre"], "Sede Principal")
        self.assertEqual(resp.data["direccion"], "Dirección de la sede")
        self.assertEqual(resp.data["ciudad"], "Medellín")
        self.assertEqual(resp.data["telefono"], "3000000000")
        self.assertEqual(resp.data["codigo"], "SED001")
        self.assertIs(resp.data["activo"], True)
        self.assertEqual(resp.data["total_usuarios"], 0)

    @patch("users.api.serializers.Sede.objects")
    def test_06_rechazar_sede_duplicada_por_nombre_o_codigo(self, mock_sede_objs):
        """
        Comprueba que POST /api/sedes/ rechace nombres o códigos duplicados
        sin distinguir mayúsculas/minúsculas ni espacios adicionales.
        """
        existente = Sede(id_sede=1, nombre="Sede Principal", codigo="SED001")
        mock_qs = MagicMock()
        mock_qs.filter.return_value.exists.return_value = False
        mock_qs.only.return_value = [existente]
        mock_qs.exclude.return_value.only.return_value = [existente]
        mock_sede_objs.all.return_value = mock_qs

        create_view = SedeViewSet.as_view({"post": "create"})
        payload = {
            "nombre": "  sede   PRINCIPAL ",
            "direccion": "Calle 1",
            "ciudad": "Medellín",
            "telefono": "3000000000",
            "codigo": "sed001",
        }
        resp = self._dispatch(create_view, "POST", "/api/sedes/", user=self.super_admin, data=payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nombre", resp.data)
        self.assertIn("codigo", resp.data)

    def test_07_inactivar_y_reactivar_sede_conservando_usuarios_y_campos(self):
        """
        Comprueba que PATCH /api/sedes/{id}/ actualice únicamente 'activo'
        (inactivar y reactivar), sin modificar otros campos ni eliminar usuarios asociados.
        """
        sede_obj = Sede(
            id_sede=1,
            nombre="Sede Principal",
            direccion="Dirección Original",
            ciudad="Medellín",
            telefono="3000000000",
            codigo="SED001",
            activo=True,
            fecha_creacion=datetime(2026, 11, 10, 10, 0, 0),
        )
        sede_obj.total_usuarios_count = 3
        sede_obj.save = MagicMock()

        detail_view = SedeViewSet.as_view({"get": "retrieve", "patch": "partial_update"})

        with patch.object(SedeViewSet, "get_object", return_value=sede_obj):
            # 1. Consultar sede (GET /api/sedes/1/)
            resp_get = self._dispatch(detail_view, "GET", "/api/sedes/1/", user=self.super_admin, pk=1)
            self.assertEqual(resp_get.status_code, status.HTTP_200_OK)
            self.assertIs(resp_get.data["activo"], True)
            self.assertEqual(resp_get.data["total_usuarios"], 3)

            # 2. Inactivar sede (PATCH /api/sedes/1/ con {"activo": false}) e intentar alterar nombre
            resp_inactivar = self._dispatch(
                detail_view,
                "PATCH",
                "/api/sedes/1/",
                user=self.super_admin,
                data={"activo": False, "nombre": "Nombre Hackeado"},
                pk=1,
            )
            self.assertEqual(resp_inactivar.status_code, status.HTTP_200_OK)
            self.assertIs(resp_inactivar.data["activo"], False)
            self.assertEqual(resp_inactivar.data["nombre"], "Sede Principal")
            self.assertEqual(resp_inactivar.data["total_usuarios"], 3)
            sede_obj.save.assert_called_with(update_fields=["activo"])

            # 3. Reactivar sede (PATCH /api/sedes/1/ con {"activo": true})
            resp_activar = self._dispatch(
                detail_view,
                "PATCH",
                "/api/sedes/1/",
                user=self.super_admin,
                data={"activo": True},
                pk=1,
            )
            self.assertEqual(resp_activar.status_code, status.HTTP_200_OK)
            self.assertIs(resp_activar.data["activo"], True)
            self.assertEqual(resp_activar.data["total_usuarios"], 3)
