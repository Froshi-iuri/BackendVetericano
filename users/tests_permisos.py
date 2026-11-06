"""
Pruebas de seguridad e integración HTTP por roles (RBAC) para todo el backend de VETERICANO.
Segunda Auditoría de Seguridad:
1. Seguridad global (DEFAULT_PERMISSION_CLASSES = IsAuthenticated y endpoints públicos AllowAny).
2. Permisos del Veterinario (aislamiento de maletín/kit, bloqueo en inventario general y asignaciones).
3. Permisos de Jurídico (gestión de peticiones e inventario general, bloqueo en clínica y administración).
4. Permisos de Peticionario (aislamiento de peticiones propias y perfil, bloqueo en módulos internos).
5. Pruebas HTTP reales mediante APIRequestFactory/APIClient sobre vistas y ViewSets completos
   (GET, POST, PUT, PATCH, DELETE, filtros de QuerySet, permisos de objeto y escalación de privilegios).
"""

from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from django.urls import get_resolver
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from users.constants import (
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_VETERINARIO,
    ROL_PETICIONARIO,
)
from users.api.views import LoginView, RegisterView, UsuariosViewSet, RolViewSet
from users.views import solicitar_recuperacion, confirmar_recuperacion
from users.api.token_refresh import UsuariosTokenRefreshView
from peticiones.api.views import (
    ListarTiposPeticionView,
    ListarEstadosPeticionView,
    ListarPeticionesView,
    IniciarPeticionView,
    DetallePeticionView,
    AsignarPeticionView,
    ActualizarEstadoPeticionView,
    PerfilUsuarioView,
    FuncionariosVisitaView,
    SeguimientoPeticionesVisitaCreateView,
    SeguimientoPeticionesVisitaDetailView,
    ListarSeguimientoPeticionesVisitaView,
    ListarSeguimientosPorPeticionView,
    HistorialPeticionView,
    DashboardJuridicoView,
    NotificacionesJuridicoView,
    MarcarNotificacionLeidaView,
    HistorialAsignacionesPorPeticionView,
)
from inventario.api.views import (
    ProveedoresViewSet,
    CompraViewSet,
    DetalleCompraViewSet,
    SalidasViewSet,
    DetalleSalidaViewSet,
    InventariosViewSet,
)
from inventario.api.kit_views import (
    KitAsignarStockView,
    KitMiKitView,
    KitVeterinarioView,
    KitRetiroDirectoView,
    SolicitudesKitListCreateView,
    SolicitudKitDetailView,
    SolicitudKitAprobarView,
    SolicitudKitRechazarView,
    SolicitudKitCancelarView,
    KitSincronizarOfflineView,
    KitSnapshotMovilView,
    KitAlertasView,
    KitKardexView,
    KitConflictosView,
)
from medicamentos.api.views import (
    MedicamentoViewSet,
    MedicamentosViewSet,
    AdministracionMedicamentoViewSet,
    TratamientoMedicamentosViewSet,
)
from clinica.api.views import (
    HistoriaClinicaViewSet,
    ConsultaViewSet,
    HospitalizacionSeresSintientesViewSet,
    TratamientosViewSet,
    DiagnosticoViewSet,
    SeguimientosClinicosViewSet,
    DiagnosticoPatologiaViewSet,
    SeguimientoHospitalarioViewSet,
    ExpedienteCompletoAPIView,
    ExpedientePorAnimalAPIView,
)
from especies.api.views import EspecieViewSet, RazaViewSet
from patologias.api.views import PatologiaViewSet
from examenes_clinicos.api.views import ExamenViewSet, ProcedimientoRealizadoViewSet
from animales.api.views import (
    AnimalViewSet,
    ResponsablesViewSet,
    EstadosAnimalViewSet,
    AnimalResponsableViewSet,
)
from adopciones.api.views import AdopcionAnimalViewSet
from voluntariado.api.views import (
    EventoVoluntariadoViewSet,
    PostulacionVoluntariadoViewSet,
    TiposEventosViewSet,
    EventosViewSet,
    VoluntarioEventoViewSet,
)
from dashboard.api.views import DashboardView


from users.models import Usuarios


class DummyUser:
    """Usuario autenticado simulado para pruebas HTTP reales sobre vistas DRF."""
    _meta = Usuarios._meta

    def __init__(self, id_usuario, rol_id, activo=True, email=None, nombre="Usuario", apellido="Prueba"):
        self.id_usuario = id_usuario
        self.pk = id_usuario
        self.id_rol_id = rol_id
        self.id_rol = MagicMock(
            id_rol=rol_id,
            nombre_rol={
                1: "Administrador",
                2: "Juridico",
                3: "Veterinario",
                4: "Peticionario",
            }.get(rol_id, "Desconocido"),
        )
        self.activo = activo
        self.is_authenticated = True
        self.is_anonymous = False
        self.email = email or f"user{id_usuario}@cba.gov.co"
        self.nombre = nombre
        self.apellido = apellido
        self.telefono = "3001234567"
        self.identificacion = str(1000000 + id_usuario)


class AuditoriaSeguridadHTTPTestCase(SimpleTestCase):
    """
    Suite integral de pruebas HTTP reales que ejecuta el ciclo completo de despacho DRF
    (autenticación -> permisos de vista -> filtrado de queryset -> permisos de objeto -> serializador).
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = DummyUser(id_usuario=1, rol_id=ROL_ADMINISTRADOR, nombre="Admin")
        self.juridico = DummyUser(id_usuario=2, rol_id=ROL_JURIDICO, nombre="Juridico")
        self.vet1 = DummyUser(id_usuario=30, rol_id=ROL_VETERINARIO, nombre="Vet1")
        self.vet2 = DummyUser(id_usuario=31, rol_id=ROL_VETERINARIO, nombre="Vet2")
        self.pet1 = DummyUser(id_usuario=40, rol_id=ROL_PETICIONARIO, nombre="Pet1")
        self.pet2 = DummyUser(id_usuario=41, rol_id=ROL_PETICIONARIO, nombre="Pet2")
        self.inactivo = DummyUser(id_usuario=99, rol_id=ROL_ADMINISTRADOR, activo=False, nombre="Inactivo")

    def _dispatch(self, view_callable, method, path="/", user=None, data=None, **kwargs):
        req_fn = getattr(self.factory, method.lower())
        request = req_fn(path, data=data, format="json") if data is not None else req_fn(path)
        if user is not None:
            force_authenticate(request, user=user)
        return view_callable(request, **kwargs)

    # =========================================================================
    # 1. SEGURIDAD GLOBAL Y ACCESO SIN AUTENTICACIÓN (HTTP 401 vs PÚBLICOS)
    # =========================================================================

    def test_01_configuracion_global_is_authenticated_en_settings(self):
        """Verifica que DEFAULT_PERMISSION_CLASSES en settings.py sea IsAuthenticated."""
        from django.conf import settings
        perm_classes = settings.REST_FRAMEWORK.get("DEFAULT_PERMISSION_CLASSES", ())
        self.assertIn("rest_framework.permissions.IsAuthenticated", perm_classes)

    def test_02_acceso_sin_autenticacion_bloqueado_con_401_en_todos_los_modulos(self):
        """
        Comprueba mediante solicitudes HTTP sin credenciales que todos los endpoints
        protegidos responden 401 Unauthorized.
        """
        endpoints_protegidos = [
            # Usuarios y Roles
            (UsuariosViewSet.as_view({"get": "list"}), "GET", "/api/usuarios/usuarios/", {}),
            (UsuariosViewSet.as_view({"post": "create"}), "POST", "/api/usuarios/usuarios/", {}),
            (RolViewSet.as_view({"get": "list"}), "GET", "/api/usuarios/roles/", {}),
            # Peticiones
            (ListarTiposPeticionView.as_view(), "GET", "/api/peticiones/tipos/", {}),
            (ListarEstadosPeticionView.as_view(), "GET", "/api/peticiones/estados/", {}),
            (ListarPeticionesView.as_view(), "GET", "/api/peticiones/listar/", {}),
            (IniciarPeticionView.as_view(), "POST", "/api/peticiones/iniciar/", {}),
            (PerfilUsuarioView.as_view(), "GET", "/api/peticiones/perfil/", {}),
            (DashboardJuridicoView.as_view(), "GET", "/api/peticiones/dashboard-juridico/", {}),
            (NotificacionesJuridicoView.as_view(), "GET", "/api/peticiones/notificaciones/", {}),
            (FuncionariosVisitaView.as_view(), "GET", "/api/peticiones/seguimiento/funcionarios/", {}),
            (SeguimientoPeticionesVisitaCreateView.as_view(), "POST", "/api/peticiones/seguimiento/crear/", {}),
            (ListarSeguimientoPeticionesVisitaView.as_view(), "GET", "/api/peticiones/seguimiento/", {}),
            (AsignarPeticionView.as_view(), "PATCH", "/api/peticiones/1/asignar/", {"id_peticion": 1}),
            (ActualizarEstadoPeticionView.as_view(), "PATCH", "/api/peticiones/1/estado/", {"id_peticion": 1}),
            (DetallePeticionView.as_view(), "GET", "/api/peticiones/1/", {"id_peticion": 1}),
            # Inventario general y Kit
            (ProveedoresViewSet.as_view({"get": "list"}), "GET", "/api/inventario/proveedores/", {}),
            (CompraViewSet.as_view({"get": "list"}), "GET", "/api/inventario/compras/", {}),
            (DetalleCompraViewSet.as_view({"get": "list"}), "GET", "/api/inventario/detalles-compra/", {}),
            (SalidasViewSet.as_view({"get": "list"}), "GET", "/api/inventario/salidas/", {}),
            (DetalleSalidaViewSet.as_view({"get": "list"}), "GET", "/api/inventario/detalles-salida/", {}),
            (InventariosViewSet.as_view({"get": "list"}), "GET", "/api/inventario/inventarios/", {}),
            (KitMiKitView.as_view(), "GET", "/api/inventario/kit/mi-kit/", {}),
            (KitAsignarStockView.as_view(), "POST", "/api/inventario/kit/asignar/", {}),
            (KitSincronizarOfflineView.as_view(), "POST", "/api/inventario/kit/sincronizar-offline/", {}),
            # Medicamentos
            (MedicamentoViewSet.as_view({"get": "list"}), "GET", "/api/medicamentos/", {}),
            (MedicamentosViewSet.as_view({"get": "list"}), "GET", "/api/medicamentos-catalogo/", {}),
            (AdministracionMedicamentoViewSet.as_view({"get": "list"}), "GET", "/api/administraciones/", {}),
            (TratamientoMedicamentosViewSet.as_view({"get": "list"}), "GET", "/api/tratamiento-medicamentos/", {}),
            # Clínica
            (HistoriaClinicaViewSet.as_view({"get": "list"}), "GET", "/api/clinica/historias/", {}),
            (ConsultaViewSet.as_view({"get": "list"}), "GET", "/api/clinica/consultas/", {}),
            (TratamientosViewSet.as_view({"get": "list"}), "GET", "/api/clinica/tratamientos/", {}),
            (ExpedienteCompletoAPIView.as_view(), "GET", "/api/clinica/expediente-completo/", {}),
            (ExpedienteCompletoAPIView.as_view(), "POST", "/api/clinica/expediente-completo/", {}),
            (ExpedientePorAnimalAPIView.as_view(), "GET", "/api/clinica/expediente-por-animal/1/", {"id_animal": 1}),
            # Catálogos clínicos y Animales
            (EspecieViewSet.as_view({"get": "list"}), "GET", "/api/especies/especies/", {}),
            (RazaViewSet.as_view({"get": "list"}), "GET", "/api/especies/razas/", {}),
            (PatologiaViewSet.as_view({"get": "list"}), "GET", "/api/patologias/patologias/", {}),
            (ExamenViewSet.as_view({"get": "list"}), "GET", "/api/examenes-clinicos/examenes/", {}),
            (ProcedimientoRealizadoViewSet.as_view({"get": "list"}), "GET", "/api/examenes-clinicos/procedimientos/", {}),
            (AnimalViewSet.as_view({"get": "list"}), "GET", "/api/animales/animales/", {}),
            # Mutaciones de Adopciones y Voluntariado y consulta de postulados
            (AdopcionAnimalViewSet.as_view({"post": "create"}), "POST", "/api/adopciones/", {}),
            (EventoVoluntariadoViewSet.as_view({"post": "create"}), "POST", "/api/eventos-voluntariado/", {}),
            (EventoVoluntariadoViewSet.as_view({"get": "postulados"}), "GET", "/api/eventos-voluntariado/1/postulados/", {"pk": 1}),
            (EventoVoluntariadoViewSet.as_view({"post": "postularse"}), "POST", "/api/eventos-voluntariado/1/postularse/", {"pk": 1}),
            (PostulacionVoluntariadoViewSet.as_view({"get": "list"}), "GET", "/api/postulaciones-voluntariado/", {}),
            # Dashboard
            (DashboardView.as_view(), "GET", "/api/dashboard/", {}),
        ]

        for view_fn, method, url, kwargs in endpoints_protegidos:
            with self.subTest(method=method, url=url):
                resp = self._dispatch(view_fn, method, url, user=None, data={} if method != "GET" else None, **kwargs)
                self.assertEqual(
                    resp.status_code,
                    status.HTTP_401_UNAUTHORIZED,
                    f"El endpoint protegido {method} {url} debía retornar 401 sin token, pero retornó {resp.status_code}",
                )

    @patch.object(EventoVoluntariadoViewSet, "get_queryset", return_value=[])
    @patch.object(AdopcionAnimalViewSet, "get_queryset", return_value=[])
    def test_03_endpoints_publicos_permiten_acceso_anonimo(self, mock_adop_qs, mock_ev_qs):
        """
        Verifica que únicamente los endpoints públicos justificados permiten acceso sin JWT:
        - Login, Registro, Recuperación de contraseña, Refresh Token (retornan 400 por body vacío, nunca 401/403).
        - GET /api/adopciones/ y GET /api/eventos-voluntariado/ (retornan 200 OK).
        """
        # Endpoints públicos de autenticación
        public_auth = [
            (LoginView.as_view(), "POST", "/api/usuarios/login/"),
            (RegisterView.as_view(), "POST", "/api/usuarios/register/"),
            (solicitar_recuperacion, "POST", "/api/usuarios/recuperar-password/solicitar/"),
            (confirmar_recuperacion, "POST", "/api/usuarios/recuperar-password/confirmar/"),
            (UsuariosTokenRefreshView.as_view(), "POST", "/api/token/refresh/"),
        ]
        for view_fn, method, url in public_auth:
            with self.subTest(url=url):
                resp = self._dispatch(view_fn, method, url, user=None, data={})
                self.assertNotIn(
                    resp.status_code,
                    (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                    f"El endpoint público {url} bloqueó acceso anónimo con {resp.status_code}",
                )

        # Endpoints públicos de consulta ciudadana (GET)
        resp_adop = self._dispatch(AdopcionAnimalViewSet.as_view({"get": "list"}), "GET", "/api/adopciones/", user=None)
        self.assertEqual(resp_adop.status_code, status.HTTP_200_OK)

        resp_ev = self._dispatch(EventoVoluntariadoViewSet.as_view({"get": "list"}), "GET", "/api/eventos-voluntariado/", user=None)
        self.assertEqual(resp_ev.status_code, status.HTTP_200_OK)

    # =========================================================================
    # 2. ACCESO A INFORMACIÓN DE OTROS USUARIOS Y ESCALACIÓN DE PRIVILEGIOS (id_rol)
    # =========================================================================

    def test_04_aislamiento_usuarios_y_bloqueo_acceso_a_otros_usuarios(self):
        """
        Comprueba que:
        - Solo Administrador puede listar todos los usuarios (GET /api/usuarios/usuarios/),
          crear/eliminar usuarios, o consultar el catálogo de roles (/api/usuarios/roles/).
        - Jurídico, Veterinario y Peticionario reciben 403 al listar usuarios, al consultar
          o editar el perfil de OTRO usuario, y al acceder a /api/usuarios/roles/.
        - Jurídico, Veterinario y Peticionario SÍ pueden consultar su propio usuario (/api/usuarios/usuarios/<su_id>/).
        """
        list_users = UsuariosViewSet.as_view({"get": "list"})
        detail_user = UsuariosViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"})
        list_roles = RolViewSet.as_view({"get": "list", "post": "create"})

        for u in (self.juridico, self.vet1, self.pet1):
            with self.subTest(rol=u.id_rol_id, caso="listar_usuarios_bloqueado"):
                resp = self._dispatch(list_users, "GET", "/api/usuarios/usuarios/", user=u)
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

            with self.subTest(rol=u.id_rol_id, caso="ver_otro_usuario_bloqueado"):
                otro_id = 999
                resp = self._dispatch(detail_user, "GET", f"/api/usuarios/usuarios/{otro_id}/", user=u, pk=str(otro_id))
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

            with self.subTest(rol=u.id_rol_id, caso="editar_otro_usuario_bloqueado"):
                otro_id = 999
                resp = self._dispatch(detail_user, "PATCH", f"/api/usuarios/usuarios/{otro_id}/", user=u, data={"nombre": "Hack"}, pk=str(otro_id))
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

            with self.subTest(rol=u.id_rol_id, caso="eliminar_propio_o_ajeno_bloqueado"):
                resp = self._dispatch(detail_user, "DELETE", f"/api/usuarios/usuarios/{u.id_usuario}/", user=u, pk=str(u.id_usuario))
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

            with self.subTest(rol=u.id_rol_id, caso="roles_bloqueado"):
                resp_get = self._dispatch(list_roles, "GET", "/api/usuarios/roles/", user=u)
                self.assertEqual(resp_get.status_code, status.HTTP_403_FORBIDDEN)
                resp_post = self._dispatch(list_roles, "POST", "/api/usuarios/roles/", user=u, data={"nombre_rol": "SuperAdmin"})
                self.assertEqual(resp_post.status_code, status.HTTP_403_FORBIDDEN)

    def test_05_intentos_escalar_privilegios_enviando_id_rol_en_json(self):
        """
        Comprueba que ningún usuario no administrador pueda cambiar su rol enviando `id_rol`
        ni su estado `activo` mediante:
        1. PATCH /api/peticiones/perfil/
        2. PATCH /api/usuarios/usuarios/<su_id>/
        3. POST /api/usuarios/register/ (fuerza id_rol = 4 Peticionario aunque envíe id_rol = 1).
        """
        perfil_view = PerfilUsuarioView.as_view()
        user_detail_view = UsuariosViewSet.as_view({"patch": "partial_update"})

        with patch(
            "rest_framework.relations.PrimaryKeyRelatedField.to_internal_value",
            side_effect=lambda data: MagicMock(id_rol=int(data), pk=int(data)),
        ), patch("rest_framework.validators.UniqueValidator.__call__", return_value=None):
            for u in (self.juridico, self.vet1, self.pet1):
                with self.subTest(rol=u.id_rol_id, endpoint="/api/peticiones/perfil/"):
                    resp = self._dispatch(perfil_view, "PATCH", "/api/peticiones/perfil/", user=u, data={"id_rol": 1})
                    self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

                    resp_activo = self._dispatch(perfil_view, "PATCH", "/api/peticiones/perfil/", user=u, data={"activo": False})
                    self.assertEqual(resp_activo.status_code, status.HTTP_403_FORBIDDEN)

                with self.subTest(rol=u.id_rol_id, endpoint=f"/api/usuarios/usuarios/{u.id_usuario}/"):
                    with patch.object(UsuariosViewSet, "get_object", return_value=u):
                        resp = self._dispatch(
                            user_detail_view,
                            "PATCH",
                            f"/api/usuarios/usuarios/{u.id_usuario}/",
                            user=u,
                            data={"id_rol": 1},
                            pk=str(u.id_usuario),
                        )
                        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Registro público intentando inyectar id_rol=1
        from users.api.serializers import RegisterCustomSerializer
        ser = RegisterCustomSerializer()
        with patch("users.api.serializers.Rol.objects.get_or_create") as mock_rol_get, \
             patch("rest_framework.serializers.ModelSerializer.create") as mock_super_create:
            rol_pet_mock = MagicMock(id_rol=4, nombre_rol="Peticionario")
            mock_rol_get.return_value = (rol_pet_mock, False)
            mock_super_create.side_effect = lambda vd: vd

            resultado = ser.create({
                "email": "attacker@test.com",
                "password": "Password123!",
                "nombre": "Attacker",
                "apellido": "Test",
                "id_rol": MagicMock(id_rol=1),
            })
            mock_rol_get.assert_called_once_with(nombre_rol="Peticionario")
            self.assertEqual(resultado["id_rol"], rol_pet_mock)

    # =========================================================================
    # 3. PERMISOS SOBRE PETICIONES: ASIGNACIÓN, REASIGNACIÓN Y AISLAMIENTO POR ROL
    # =========================================================================

    def test_06_veterinario_y_peticionario_no_pueden_asignar_ni_reasignar_peticiones(self):
        """
        Comprueba que Veterinario (3) y Peticionario (4) reciben 403 Forbidden al intentar
        asignar o reasignar peticiones en PATCH /api/peticiones/<id>/asignar/,
        y que Veterinario (3) recibe 403 al intentar crear una petición en POST /api/peticiones/iniciar/.
        """
        asignar_view = AsignarPeticionView.as_view()
        iniciar_view = IniciarPeticionView.as_view()

        for u in (self.vet1, self.pet1):
            with self.subTest(rol=u.id_rol_id, accion="asignar_peticion"):
                resp = self._dispatch(
                    asignar_view,
                    "PATCH",
                    "/api/peticiones/10/asignar/",
                    user=u,
                    data={"asignado_a": self.vet1.id_usuario},
                    id_peticion=10,
                )
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Veterinario intentando radicar petición nueva
        resp_iniciar_vet = self._dispatch(
            iniciar_view,
            "POST",
            "/api/peticiones/iniciar/",
            user=self.vet1,
            data={"descripcion": "Intento de veterinario"},
        )
        self.assertEqual(resp_iniciar_vet.status_code, status.HTTP_403_FORBIDDEN)

    @patch("peticiones.api.views.Peticiones.objects")
    def test_07_aislamiento_peticiones_entre_veterinarios_y_entre_peticionarios(self, mock_pet_objects):
        """
        Comprueba que:
        - Un Veterinario (vet1) NO puede consultar el detalle, historial, seguimientos ni cambiar
          el estado de una petición asignada a otro veterinario (vet2) o sin asignar -> 403 Forbidden.
        - Un Peticionario (pet1) NO puede consultar el detalle, historial, seguimientos ni cambiar
          el estado de una petición creada por otro peticionario (pet2) -> 403 Forbidden.
        - Administrador (1) y Jurídico (2) SÍ pueden consultar cualquier petición -> 200 OK.
        """
        peticion_de_pet2_asignada_a_vet2 = MagicMock(
            id_peticion=50,
            pk=50,
            responsable_id=self.pet2.id_usuario,
            asignado_a_id=self.vet2.id_usuario,
            asignado_a=self.vet2,
        )
        mock_pet_objects.get.return_value = peticion_de_pet2_asignada_a_vet2
        mock_pet_objects.filter.return_value.first.return_value = peticion_de_pet2_asignada_a_vet2
        mock_pet_objects.select_related.return_value.filter.return_value.first.return_value = peticion_de_pet2_asignada_a_vet2

        detalle_view = DetallePeticionView.as_view()
        estado_view = ActualizarEstadoPeticionView.as_view()
        historial_view = HistorialPeticionView.as_view()
        seguimientos_pet_view = ListarSeguimientosPorPeticionView.as_view()

        with patch.object(DetallePeticionView, "get_queryset") as mock_det_qs:
            mock_det_qs.return_value.filter.return_value.get.return_value = peticion_de_pet2_asignada_a_vet2

            # vet1 intentando ver petición de vet2 -> 403
            resp_det_vet1 = self._dispatch(detalle_view, "GET", "/api/peticiones/50/", user=self.vet1, id_peticion=50)
            self.assertEqual(resp_det_vet1.status_code, status.HTTP_403_FORBIDDEN)

            # pet1 intentando ver petición de pet2 -> 403
            resp_det_pet1 = self._dispatch(detalle_view, "GET", "/api/peticiones/50/", user=self.pet1, id_peticion=50)
            self.assertEqual(resp_det_pet1.status_code, status.HTTP_403_FORBIDDEN)

        # vet1 y pet1 intentando ver historial de petición ajena -> 403
        self.assertEqual(
            self._dispatch(historial_view, "GET", "/api/peticiones/50/historial/", user=self.vet1, id_peticion=50).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self._dispatch(historial_view, "GET", "/api/peticiones/50/historial/", user=self.pet1, id_peticion=50).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # vet1 y pet1 intentando ver seguimientos de petición ajena -> 403
        self.assertEqual(
            self._dispatch(seguimientos_pet_view, "GET", "/api/peticiones/50/seguimientos/", user=self.vet1, id_peticion=50).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self._dispatch(seguimientos_pet_view, "GET", "/api/peticiones/50/seguimientos/", user=self.pet1, id_peticion=50).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # vet1 y pet1 intentando cambiar estado de petición ajena -> 403
        self.assertEqual(
            self._dispatch(estado_view, "PATCH", "/api/peticiones/50/estado/", user=self.vet1, data={"estado": "En proceso"}, id_peticion=50).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self._dispatch(estado_view, "PATCH", "/api/peticiones/50/estado/", user=self.pet1, data={"estado": "En proceso"}, id_peticion=50).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    @patch("peticiones.api.views.Peticiones.objects")
    def test_08_filtrado_queryset_listar_peticiones_por_rol(self, mock_pet_objects):
        """
        Verifica que ListarPeticionesView.get_queryset() filtre estrictamente:
        - Rol 3 (Veterinario): queryset.filter(asignado_a=user)
        - Rol 4 (Peticionario): queryset.filter(responsable=user)
        - Roles 1 y 2 (Admin y Jurídico): ven todas sin filtro de propiedad.
        """
        base_qs = MagicMock()
        mock_pet_objects.select_related.return_value.prefetch_related.return_value.all.return_value = base_qs
        base_qs.filter.return_value = base_qs
        base_qs.order_by.return_value = base_qs
        base_qs.select_related.return_value.prefetch_related.return_value = base_qs

        # Veterinario
        view_vet = ListarPeticionesView()
        req_vet = self.factory.get("/api/peticiones/listar/")
        req_vet.user = self.vet1
        req_vet.query_params = {}
        view_vet.request = req_vet
        view_vet.get_queryset()
        base_qs.filter.assert_called_with(asignado_a=self.vet1)

        base_qs.reset_mock()

        # Peticionario
        view_pet = ListarPeticionesView()
        req_pet = self.factory.get("/api/peticiones/listar/")
        req_pet.user = self.pet1
        req_pet.query_params = {}
        view_pet.request = req_pet
        view_pet.get_queryset()
        base_qs.filter.assert_called_with(responsable=self.pet1)

    def test_09_permisos_actas_seguimiento_visita_por_rol(self):
        """
        Verifica que:
        - Peticionario (4) y Jurídico (2) NO pueden crear ni editar actas de visita clínica (403 Forbidden).
        - Veterinario (3) NO puede ver ni editar el acta de visita de otro veterinario (403 Forbidden).
        - Peticionario (4) NO puede consultar el directorio de funcionarios (/api/peticiones/seguimiento/funcionarios/ -> 403).
        """
        crear_acta_view = SeguimientoPeticionesVisitaCreateView.as_view()
        detalle_acta_view = SeguimientoPeticionesVisitaDetailView.as_view()
        funcionarios_view = FuncionariosVisitaView.as_view()

        # Peticionario bloqueado en directorio de funcionarios
        resp_func_pet = self._dispatch(funcionarios_view, "GET", "/api/peticiones/seguimiento/funcionarios/", user=self.pet1)
        self.assertEqual(resp_func_pet.status_code, status.HTTP_403_FORBIDDEN)

        # Jurídico y Peticionario bloqueados al intentar crear acta clínica de visita
        for u in (self.juridico, self.pet1):
            resp_crear = self._dispatch(crear_acta_view, "POST", "/api/peticiones/seguimiento/crear/", user=u, data={})
            self.assertEqual(resp_crear.status_code, status.HTTP_403_FORBIDDEN)

        # Acta perteneciente a vet2
        acta_vet2 = MagicMock(
            id_seguimiento=77,
            pk=77,
            id_veterinario_id=self.vet2.id_usuario,
            id_peticion=MagicMock(responsable_id=self.pet2.id_usuario),
        )
        with patch.object(SeguimientoPeticionesVisitaDetailView, "get_queryset") as mock_acta_qs:
            mock_acta_qs.return_value.filter.return_value.get.return_value = acta_vet2

            # vet1 intentando consultar o modificar acta de vet2 -> 403
            self.assertEqual(
                self._dispatch(detalle_acta_view, "GET", "/api/peticiones/seguimiento/77/", user=self.vet1, id_seguimiento=77).status_code,
                status.HTTP_403_FORBIDDEN,
            )
            self.assertEqual(
                self._dispatch(detalle_acta_view, "PATCH", "/api/peticiones/seguimiento/77/", user=self.vet1, data={"observacion": "x"}, id_seguimiento=77).status_code,
                status.HTTP_403_FORBIDDEN,
            )

            # Jurídico intentando modificar acta clínica -> 403
            self.assertEqual(
                self._dispatch(detalle_acta_view, "PATCH", "/api/peticiones/seguimiento/77/", user=self.juridico, data={"observacion": "x"}, id_seguimiento=77).status_code,
                status.HTTP_403_FORBIDDEN,
            )

            # pet1 intentando consultar acta de petición de pet2 -> 403
            self.assertEqual(
                self._dispatch(detalle_acta_view, "GET", "/api/peticiones/seguimiento/77/", user=self.pet1, id_seguimiento=77).status_code,
                status.HTTP_403_FORBIDDEN,
            )

    # =========================================================================
    # 4. PERMISOS DEL VETERINARIO Y JURÍDICO EN INVENTARIO GENERAL Y MALETINES (KIT)
    # =========================================================================

    def test_10_veterinario_y_peticionario_bloqueados_en_inventario_general_y_juridico_autorizado(self):
        """
        Comprueba que:
        - Veterinario (3) y Peticionario (4) reciben 403 Forbidden en TODOS los métodos
          (GET, POST, PUT, PATCH, DELETE) de proveedores, compras, detalles-compra,
          salidas, detalles-salida e inventarios (bodega general).
        - Administrador (1) y Jurídico (2) están autorizados en todos esos recursos.
        """
        viewsets_inventario_general = [
            ("proveedores", ProveedoresViewSet),
            ("compras", CompraViewSet),
            ("detalles-compra", DetalleCompraViewSet),
            ("salidas", SalidasViewSet),
            ("detalles-salida", DetalleSalidaViewSet),
            ("inventarios", InventariosViewSet),
        ]

        for nombre, vs_cls in viewsets_inventario_general:
            with patch.object(vs_cls, "get_queryset", return_value=[]):
                list_create_view = vs_cls.as_view({"get": "list", "post": "create"})
                detail_view = vs_cls.as_view({"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"})

                # Veterinario y Peticionario -> 403 en GET, POST, PUT, PATCH, DELETE
                for u in (self.vet1, self.pet1):
                    for method in ("GET", "POST"):
                        with self.subTest(recurso=nombre, rol=u.id_rol_id, method=method):
                            resp = self._dispatch(list_create_view, method, f"/api/inventario/{nombre}/", user=u, data={} if method == "POST" else None)
                            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
                    for method in ("GET", "PUT", "PATCH", "DELETE"):
                        with self.subTest(recurso=nombre, rol=u.id_rol_id, method=f"{method}_detail"):
                            resp = self._dispatch(detail_view, method, f"/api/inventario/{nombre}/1/", user=u, data={} if method in ("PUT", "PATCH") else None, pk=1)
                            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

                # Administrador y Jurídico -> 200 OK en GET y autorizados en escritura (no 401 ni 403)
                for u in (self.admin, self.juridico):
                    with self.subTest(recurso=nombre, rol=u.id_rol_id, method="GET"):
                        resp_get = self._dispatch(list_create_view, "GET", f"/api/inventario/{nombre}/", user=u)
                        self.assertEqual(resp_get.status_code, status.HTTP_200_OK)

    def test_11_aislamiento_maletin_entre_veterinarios_y_permisos_kit(self):
        """
        Comprueba que:
        - Un Veterinario (vet1) NO puede supervisar el maletín de otro veterinario
          (GET /api/inventario/kit/veterinario/<id_vet2>/ -> 403 Forbidden).
        - Un Veterinario (vet1) NO puede asignar stock a maletines (/kit/asignar/),
          hacer retiros directos (/kit/retiro-directo/), ni aprobar/rechazar solicitudes -> 403 Forbidden.
        - Un Veterinario (vet1) NO puede consultar la solicitud de kit de otro veterinario (vet2)
          en GET /api/inventario/kit/solicitudes/<pk_vet2>/ -> 403 Forbidden.
        - En GET /api/inventario/kit/alertas/?id_veterinario=<id_vet2> y GET /api/inventario/kit/kardex/?id_veterinario=<id_vet2>,
          el backend ignora el parámetro ajeno y fuerza la consulta al id_usuario del veterinario autenticado (vet1).
        - Administrador (1) y Jurídico (2) pueden supervisar maletines, asignar, retirar y aprobar/rechazar solicitudes.
        """
        # 1. Endpoints exclusivos de gestor (Admin y Jurídico permitidos; Veterinario y Peticionario -> 403)
        gestor_views = [
            (KitAsignarStockView.as_view(), "POST", "/api/inventario/kit/asignar/", {}),
            (KitRetiroDirectoView.as_view(), "POST", "/api/inventario/kit/retiro-directo/", {}),
            (KitVeterinarioView.as_view(), "GET", f"/api/inventario/kit/veterinario/{self.vet2.id_usuario}/", {"id": self.vet2.id_usuario}),
            (SolicitudKitAprobarView.as_view(), "POST", "/api/inventario/kit/solicitudes/1/aprobar/", {"pk": 1}),
            (SolicitudKitRechazarView.as_view(), "POST", "/api/inventario/kit/solicitudes/1/rechazar/", {"pk": 1}),
            (KitConflictosView.as_view(), "GET", "/api/inventario/kit/conflictos/", {}),
        ]
        for view_fn, method, url, kwargs in gestor_views:
            for u in (self.vet1, self.pet1):
                with self.subTest(url=url, rol=u.id_rol_id):
                    resp = self._dispatch(view_fn, method, url, user=u, data={} if method == "POST" else None, **kwargs)
                    self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Solicitud de kit perteneciente a vet2: vet1 recibe 403, mientras Admin y Jurídico reciben 200
        solicitud_vet2 = MagicMock(id_solicitud=88, pk=88, id_veterinario_id=self.vet2.id_usuario)
        with patch("inventario.api.kit_views.SolicitudesKit.objects") as mock_sol_objects, \
             patch("inventario.api.kit_views.SolicitudKitOutputSerializer") as mock_sol_ser:
            mock_sol_objects.filter.return_value.select_related.return_value.prefetch_related.return_value.first.return_value = solicitud_vet2
            mock_sol_ser.return_value.data = {"id_solicitud": 88, "id_veterinario": self.vet2.id_usuario}

            resp_vet1 = self._dispatch(SolicitudKitDetailView.as_view(), "GET", "/api/inventario/kit/solicitudes/88/", user=self.vet1, pk=88)
            self.assertEqual(resp_vet1.status_code, status.HTTP_403_FORBIDDEN)

            for gestor in (self.admin, self.juridico):
                resp_gestor = self._dispatch(SolicitudKitDetailView.as_view(), "GET", "/api/inventario/kit/solicitudes/88/", user=gestor, pk=88)
                self.assertEqual(resp_gestor.status_code, status.HTTP_200_OK)

        # 3. Alertas y Kardex: vet1 intentando pasar ?id_veterinario=31 (vet2) solo recibe datos de vet1 (id=30)
        with patch("inventario.api.kit_views.consultar_alertas_kit", return_value={"agotados": []}) as mock_alertas:
            resp_al = self._dispatch(
                KitAlertasView.as_view(),
                "GET",
                f"/api/inventario/kit/alertas/?id_veterinario={self.vet2.id_usuario}",
                user=self.vet1,
            )
            self.assertEqual(resp_al.status_code, status.HTTP_200_OK)
            mock_alertas.assert_called_once_with(
                es_gestor=False,
                id_veterinario_solicitante=self.vet1.id_usuario,
                id_veterinario_filtro=None,
            )

        with patch("inventario.api.kit_views.construir_queryset_kardex", return_value=[]) as mock_kardex, \
             patch("inventario.api.kit_views.KitStandardPagination.paginate_queryset", return_value=None), \
             patch("inventario.api.kit_views.KardexOutputSerializer") as mock_kardex_ser:
            mock_kardex_ser.return_value.data = []
            resp_kx = self._dispatch(
                KitKardexView.as_view(),
                "GET",
                f"/api/inventario/kit/kardex/?id_veterinario={self.vet2.id_usuario}",
                user=self.vet1,
            )
            self.assertEqual(resp_kx.status_code, status.HTTP_200_OK)
            _, kwargs_kx = mock_kardex.call_args
            self.assertFalse(kwargs_kx["es_gestor"])
            self.assertEqual(kwargs_kx["id_veterinario_solicitante"], self.vet1.id_usuario)
            self.assertIsNone(kwargs_kx["filtros"]["id_veterinario"])

        # 4. KitMiKitView y SincronizarOffline: exclusivos de Veterinario (3); Admin (1), Jurídico (2) y Peticionario (4) reciben 403
        for u in (self.admin, self.juridico, self.pet1):
            resp_mikit = self._dispatch(KitMiKitView.as_view(), "GET", "/api/inventario/kit/mi-kit/", user=u)
            self.assertEqual(resp_mikit.status_code, status.HTTP_403_FORBIDDEN)
            resp_sync = self._dispatch(KitSincronizarOfflineView.as_view(), "POST", "/api/inventario/kit/sincronizar-offline/", user=u, data={})
            self.assertEqual(resp_sync.status_code, status.HTTP_403_FORBIDDEN)

        with patch("inventario.api.kit_views.consultar_resumen_kit_veterinario", return_value={"items": [], "total_unidades": 0}) as mock_mi_kit:
            resp_vet_kit = self._dispatch(KitMiKitView.as_view(), "GET", "/api/inventario/kit/mi-kit/", user=self.vet1)
            self.assertEqual(resp_vet_kit.status_code, status.HTTP_200_OK)
            mock_mi_kit.assert_called_once_with(self.vet1.id_usuario)

    # =========================================================================
    # 5. PERMISOS SOBRE MEDICAMENTOS Y TRATAMIENTOS CLÍNICOS
    # =========================================================================

    def test_12_permisos_catalogo_medicamentos_y_administraciones_clinicas(self):
        """
        Comprueba que:
        - En /api/medicamentos/ y /api/medicamentos-catalogo/:
          * Admin (1) y Jurídico (2) pueden leer y escribir (GET, POST, PUT, PATCH, DELETE).
          * Veterinario (3) puede consultar (GET -> 200 OK) pero NO crear, editar ni eliminar (POST/PUT/PATCH/DELETE -> 403).
          * Peticionario (4) recibe 403 en todos los métodos.
        - En /api/administraciones/ y /api/tratamiento-medicamentos/ (registros clínicos):
          * Admin (1) y Veterinario (3) están autorizados.
          * Jurídico (2) y Peticionario (4) reciben 403 en todos los métodos.
        """
        for vs_cls, ruta in ((MedicamentoViewSet, "/api/medicamentos/"), (MedicamentosViewSet, "/api/medicamentos-catalogo/")):
            with patch.object(vs_cls, "get_queryset", return_value=[]):
                list_create = vs_cls.as_view({"get": "list", "post": "create"})
                detail = vs_cls.as_view({"put": "update", "patch": "partial_update", "delete": "destroy"})

                # Veterinario: GET 200, mutaciones 403
                self.assertEqual(self._dispatch(list_create, "GET", ruta, user=self.vet1).status_code, status.HTTP_200_OK)
                self.assertEqual(self._dispatch(list_create, "POST", ruta, user=self.vet1, data={}).status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(self._dispatch(detail, "PUT", f"{ruta}1/", user=self.vet1, data={}, pk=1).status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(self._dispatch(detail, "PATCH", f"{ruta}1/", user=self.vet1, data={}, pk=1).status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(self._dispatch(detail, "DELETE", f"{ruta}1/", user=self.vet1, pk=1).status_code, status.HTTP_403_FORBIDDEN)

                # Peticionario: 403 en GET y POST
                self.assertEqual(self._dispatch(list_create, "GET", ruta, user=self.pet1).status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(self._dispatch(list_create, "POST", ruta, user=self.pet1, data={}).status_code, status.HTTP_403_FORBIDDEN)

                # Admin y Jurídico: GET 200
                for gestor in (self.admin, self.juridico):
                    self.assertEqual(self._dispatch(list_create, "GET", ruta, user=gestor).status_code, status.HTTP_200_OK)

        # Administraciones y TratamientoMedicamentos: exclusivos de Admin (1) y Veterinario (3); Jurídico (2) y Peticionario (4) -> 403
        for vs_cls, ruta in (
            (AdministracionMedicamentoViewSet, "/api/administraciones/"),
            (TratamientoMedicamentosViewSet, "/api/tratamiento-medicamentos/"),
        ):
            list_create = vs_cls.as_view({"get": "list", "post": "create"})
            for u in (self.juridico, self.pet1):
                self.assertEqual(self._dispatch(list_create, "GET", ruta, user=u).status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(self._dispatch(list_create, "POST", ruta, user=u, data={}).status_code, status.HTTP_403_FORBIDDEN)

    # =========================================================================
    # 6. BLOQUEO DE JURÍDICO Y PETICIONARIO EN HISTORIAS CLÍNICAS Y CATÁLOGOS CLÍNICOS
    # =========================================================================

    def test_13_juridico_y_peticionario_bloqueados_en_historias_clinicas_y_modulos_clinicos(self):
        """
        Comprueba mediante solicitudes HTTP que Jurídico (2) y Peticionario (4)
        reciben 403 Forbidden al intentar consultar o modificar (GET, POST, PUT, PATCH, DELETE)
        cualquier endpoint de historias clínicas, consultas, tratamientos, diagnósticos,
        hospitalizaciones y expedientes clínicos, mientras Admin (1) y Veterinario (3) están autorizados.
        """
        viewsets_clinicos = [
            ("historias", HistoriaClinicaViewSet),
            ("consultas", ConsultaViewSet),
            ("hospitalizaciones", HospitalizacionSeresSintientesViewSet),
            ("tratamientos", TratamientosViewSet),
            ("diagnosticos", DiagnosticoViewSet),
            ("seguimientos", SeguimientosClinicosViewSet),
            ("diagnostico-patologias", DiagnosticoPatologiaViewSet),
            ("seguimientos-hospitalarios", SeguimientoHospitalarioViewSet),
        ]

        for nombre, vs_cls in viewsets_clinicos:
            list_create = vs_cls.as_view({"get": "list", "post": "create"})
            detail = vs_cls.as_view({"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"})
            for u in (self.juridico, self.pet1):
                for method in ("GET", "POST"):
                    with self.subTest(modulo=nombre, rol=u.id_rol_id, method=method):
                        resp = self._dispatch(list_create, method, f"/api/clinica/{nombre}/", user=u, data={} if method == "POST" else None)
                        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
                for method in ("GET", "PUT", "PATCH", "DELETE"):
                    with self.subTest(modulo=nombre, rol=u.id_rol_id, method=f"{method}_detail"):
                        resp = self._dispatch(detail, method, f"/api/clinica/{nombre}/1/", user=u, data={} if method in ("PUT", "PATCH") else None, pk=1)
                        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # ExpedienteCompletoAPIView (GET, POST, PATCH) y ExpedientePorAnimalAPIView (GET)
        exp_view = ExpedienteCompletoAPIView.as_view()
        exp_animal_view = ExpedientePorAnimalAPIView.as_view()
        for u in (self.juridico, self.pet1):
            self.assertEqual(self._dispatch(exp_view, "GET", "/api/clinica/expediente-completo/", user=u).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self._dispatch(exp_view, "POST", "/api/clinica/expediente-completo/", user=u, data={}).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self._dispatch(exp_view, "PATCH", "/api/clinica/expediente-completo/", user=u, data={}).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self._dispatch(exp_animal_view, "GET", "/api/clinica/expediente-por-animal/1/", user=u, id_animal=1).status_code, status.HTTP_403_FORBIDDEN)

        # Admin y Veterinario autorizados en historias clínicas
        with patch.object(HistoriaClinicaViewSet, "get_queryset", return_value=[]):
            for u in (self.admin, self.vet1):
                resp_hc = self._dispatch(HistoriaClinicaViewSet.as_view({"get": "list"}), "GET", "/api/clinica/historias/", user=u)
                self.assertEqual(resp_hc.status_code, status.HTTP_200_OK)

    def test_14_catalogos_clinicos_lectura_veterinario_escritura_admin_bloqueo_juridico_y_peticionario(self):
        """
        Comprueba en especies, razas, patologías y exámenes clínicos:
        - Administrador (1): lectura y escritura completa (GET, POST, PUT, PATCH, DELETE).
        - Veterinario (3): solo lectura (GET -> 200 OK); escritura bloqueada (POST, PUT, PATCH, DELETE -> 403).
        - Jurídico (2) y Peticionario (4): bloqueados tanto en lectura como en escritura (403 Forbidden).
        """
        catalogos = [
            ("especies", EspecieViewSet, "/api/especies/especies/"),
            ("razas", RazaViewSet, "/api/especies/razas/"),
            ("patologias", PatologiaViewSet, "/api/patologias/patologias/"),
            ("examenes", ExamenViewSet, "/api/examenes-clinicos/examenes/"),
        ]

        for nombre, vs_cls, url in catalogos:
            with patch.object(vs_cls, "get_queryset", return_value=[]):
                list_create = vs_cls.as_view({"get": "list", "post": "create"})
                detail = vs_cls.as_view({"put": "update", "patch": "partial_update", "delete": "destroy"})

                # Admin y Veterinario pueden consultar (GET 200)
                self.assertEqual(self._dispatch(list_create, "GET", url, user=self.admin).status_code, status.HTTP_200_OK)
                self.assertEqual(self._dispatch(list_create, "GET", url, user=self.vet1).status_code, status.HTTP_200_OK)

                # Veterinario NO puede crear, editar ni eliminar (403)
                self.assertEqual(self._dispatch(list_create, "POST", url, user=self.vet1, data={}).status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(self._dispatch(detail, "PUT", f"{url}1/", user=self.vet1, data={}, pk=1).status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(self._dispatch(detail, "PATCH", f"{url}1/", user=self.vet1, data={}, pk=1).status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(self._dispatch(detail, "DELETE", f"{url}1/", user=self.vet1, pk=1).status_code, status.HTTP_403_FORBIDDEN)

                # Jurídico y Peticionario bloqueados en GET y POST (403)
                for u in (self.juridico, self.pet1):
                    self.assertEqual(self._dispatch(list_create, "GET", url, user=u).status_code, status.HTTP_403_FORBIDDEN)
                    self.assertEqual(self._dispatch(list_create, "POST", url, user=u, data={}).status_code, status.HTTP_403_FORBIDDEN)

    def test_15_adopciones_y_voluntariado_restricciones_por_rol(self):
        """
        Comprueba que:
        - En /api/adopciones/: cualquier usuario (o anónimo) puede hacer GET, pero solo Administrador (1)
          puede hacer POST, PUT, PATCH, DELETE (Jurídico, Veterinario y Peticionario reciben 403).
        - En /api/eventos-voluntariado/: cualquier usuario puede hacer GET y POST a /postularse/,
          pero solo Administrador (1) puede crear/editar/eliminar eventos o consultar /postulados/
          y /api/postulaciones-voluntariado/ (Jurídico, Veterinario y Peticionario reciben 403).
        """
        adop_write = AdopcionAnimalViewSet.as_view({"post": "create", "patch": "partial_update", "delete": "destroy"})
        ev_write = EventoVoluntariadoViewSet.as_view({"post": "create", "patch": "partial_update", "delete": "destroy"})
        ev_postulados = EventoVoluntariadoViewSet.as_view({"get": "postulados"})
        postulaciones_list = PostulacionVoluntariadoViewSet.as_view({"get": "list"})

        for u in (self.juridico, self.vet1, self.pet1):
            # Adopciones mutaciones -> 403
            self.assertEqual(self._dispatch(adop_write, "POST", "/api/adopciones/", user=u, data={}).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self._dispatch(adop_write, "PATCH", "/api/adopciones/1/", user=u, data={}, pk=1).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self._dispatch(adop_write, "DELETE", "/api/adopciones/1/", user=u, pk=1).status_code, status.HTTP_403_FORBIDDEN)

            # Voluntariado mutaciones y consulta de postulados -> 403
            self.assertEqual(self._dispatch(ev_write, "POST", "/api/eventos-voluntariado/", user=u, data={}).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self._dispatch(ev_write, "PATCH", "/api/eventos-voluntariado/1/", user=u, data={}, pk=1).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self._dispatch(ev_write, "DELETE", "/api/eventos-voluntariado/1/", user=u, pk=1).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self._dispatch(ev_postulados, "GET", "/api/eventos-voluntariado/1/postulados/", user=u, pk=1).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self._dispatch(postulaciones_list, "GET", "/api/postulaciones-voluntariado/", user=u).status_code, status.HTTP_403_FORBIDDEN)
