"""
Tests de seguridad y matriz de permisos RBAC para el Kit de Veterinario (Fase D2 - Tarea 10).
Cubre los escenarios TEST-SEC-56 a TEST-SEC-58 (3 tests).

Especificación y Contratos:
- plan/DISENO_TAREA10.md (§4.6: Matriz RBAC, §5.6: Bloque F).
- inventario/api/urls.py (rutas reales bajo /api/inventario/kit/).
- inventario/api/kit_views.py (clases de vista, permission_classes y control en métodos).
- inventario/api/permissions.py (_resolver_usuario_activo_y_rol, EsGestorInventario, EsVeterinario, EsGestorOVeterinario).
- inventario/constants.py (INVENTARIO_GESTOR_ROLES=[1], ROL_VETERINARIO=3).
- users/authentication.py (CustomJWTAuthentication: control de activo=False emite 401).
- vetericano/settings.py y test_settings.py (REST_FRAMEWORK, SIMPLE_JWT).
"""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from inventario.models import (
    InventarioKardex,
    KitVeterinarioLotes,
    SolicitudesKit,
    InventarioConsumosRegistro,
)
from inventario.tests.factories import crear_usuario
from inventario.tests.helpers import obtener_headers_jwt


class KitPermisosSeguridadTestCase(TestCase):
    """
    Suite de seguridad y permisos RBAC para los endpoints del Kit de Veterinario.
    Evalúa la matriz completa de roles institucionales (1 Gestor, 2 Jurídico,
    3 Veterinario, 4 Peticionario), el bloqueo de usuarios inactivos y la validación
    de integridad y expiración de tokens JWT.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()

        # Usuarios de prueba para cada rol institucional (roles expresados como literales)
        # Nota Tarea 11: La gestión de inventario podría migrar al Rol 2 (Jurídico),
        # lo que requerirá actualizar INVENTARIO_GESTOR_ROLES y esta matriz de permisos.
        self.user_gestor = crear_usuario(rol_id=1, email="gestor_sec56@cba.gov.co")        # Rol 1: Administrador / Gestor
        self.user_juridico = crear_usuario(rol_id=2, email="juridico_sec56@cba.gov.co")    # Rol 2: Jurídico
        self.user_vet = crear_usuario(rol_id=3, email="vet_sec56@cba.gov.co")              # Rol 3: Veterinario
        self.user_peticionario = crear_usuario(rol_id=4, email="peticionario_sec56@cba.gov.co")  # Rol 4: Peticionario

        self.headers_gestor = obtener_headers_jwt(self.user_gestor)
        self.headers_juridico = obtener_headers_jwt(self.user_juridico)
        self.headers_vet = obtener_headers_jwt(self.user_vet)
        self.headers_peticionario = obtener_headers_jwt(self.user_peticionario)

        self.roles_map = {
            1: ("Gestor", self.headers_gestor),
            2: ("Juridico", self.headers_juridico),
            3: ("Veterinario", self.headers_vet),
            4: ("Peticionario", self.headers_peticionario),
        }

    def test_sec_56_matriz_rbac_endpoints_kit(self):
        """
        TEST-SEC-56: Matriz RBAC contra todos los endpoints de /api/inventario/kit/.
        - Construida leyendo directamente urls.py, kit_views.py y permissions.py:
          1. POST /api/inventario/kit/asignar/ -> EsGestorInventario (Autorizado: Rol 1)
          2. POST /api/inventario/kit/retiro-directo/ -> EsGestorInventario (Autorizado: Rol 1)
             * NOTA H-C2: kit_views.py:273 tiene un bug conocido (KeyError al acceder a
               resultado['lotes_afectados'] en retiros exitosos). Para evitar detonar el bug
               en esta matriz, para el rol autorizado se envía un cuerpo vacío ({}) que el
               serializer rechaza con HTTP 400 antes de llegar al servicio de dominio.
          3. GET /api/inventario/kit/veterinario/{id}/ -> EsGestorInventario (Autorizado: Rol 1)
          4. POST /api/inventario/kit/solicitudes/{id}/aprobar/ -> EsGestorInventario (Autorizado: Rol 1)
          5. POST /api/inventario/kit/solicitudes/{id}/rechazar/ -> EsGestorInventario (Autorizado: Rol 1)
          6. GET /api/inventario/kit/conflictos/ -> EsGestorInventario (Autorizado: Rol 1)
          7. GET /api/inventario/kit/mi-kit/ -> EsVeterinario (Autorizado: Rol 3)
          8. GET /api/inventario/kit/snapshot-movil/ -> EsVeterinario (Autorizado: Rol 3)
          9. POST /api/inventario/kit/solicitudes/{id}/cancelar/ -> EsVeterinario (Autorizado: Rol 3)
          10. POST /api/inventario/kit/sincronizar-offline/ -> EsVeterinario (Autorizado: Rol 3)
          11. GET /api/inventario/kit/solicitudes/ -> EsGestorOVeterinario (Autorizados: Rol 1 y Rol 3)
          12. POST /api/inventario/kit/solicitudes/ -> EsGestorOVeterinario (Autorizado: Rol 3)
              * NOTA DE VISTA: SolicitudesKitListCreateView.post (kit_views.py:327) restringe la
                creación exclusivamente a veterinarios activos (if not _es_veterinario: 403).
                Por tanto, solo Rol 3 está autorizado en POST; Rol 1 recibe 403.
          13. GET /api/inventario/kit/solicitudes/{id}/ -> EsGestorOVeterinario (Autorizados: Rol 1 y Rol 3)
          14. GET /api/inventario/kit/alertas/ -> EsGestorOVeterinario (Autorizados: Rol 1 y Rol 3)
          15. GET /api/inventario/kit/kardex/ -> EsGestorOVeterinario (Autorizados: Rol 1 y Rol 3)
        - Para cada combinación (rol x endpoint):
          * Roles NO autorizados: deben recibir estrictamente HTTP 403 Forbidden y la BD
            debe quedar 100% inalterada (sin kardex, sin lotes de kit, sin solicitudes, sin consumos).
          * Roles autorizados: el resultado NO debe ser 401 ni 403 (permiso concedido;
            un 400 por payload vacío o 404 por id inexistente es la respuesta esperada sin mutar BD).
        """
        # Lista canónica de tuplas: (ruta, método, clase_permiso, roles_autorizados)
        endpoints = [
            ("/api/inventario/kit/asignar/", "POST", "EsGestorInventario", [1]),
            ("/api/inventario/kit/retiro-directo/", "POST", "EsGestorInventario", [1]),
            (f"/api/inventario/kit/veterinario/{self.user_vet.id_usuario}/", "GET", "EsGestorInventario", [1]),
            ("/api/inventario/kit/solicitudes/99999/aprobar/", "POST", "EsGestorInventario", [1]),
            ("/api/inventario/kit/solicitudes/99999/rechazar/", "POST", "EsGestorInventario", [1]),
            ("/api/inventario/kit/conflictos/", "GET", "EsGestorInventario", [1]),
            ("/api/inventario/kit/mi-kit/", "GET", "EsVeterinario", [3]),
            ("/api/inventario/kit/snapshot-movil/", "GET", "EsVeterinario", [3]),
            ("/api/inventario/kit/solicitudes/99999/cancelar/", "POST", "EsVeterinario", [3]),
            ("/api/inventario/kit/sincronizar-offline/", "POST", "EsVeterinario", [3]),
            ("/api/inventario/kit/solicitudes/", "GET", "EsGestorOVeterinario", [1, 3]),
            ("/api/inventario/kit/solicitudes/", "POST", "EsGestorOVeterinario", [3]),
            ("/api/inventario/kit/solicitudes/99999/", "GET", "EsGestorOVeterinario", [1, 3]),
            ("/api/inventario/kit/alertas/", "GET", "EsGestorOVeterinario", [1, 3]),
            ("/api/inventario/kit/kardex/", "GET", "EsGestorOVeterinario", [1, 3]),
        ]

        total_subtests = 0
        for ruta, metodo, perm_class, roles_autorizados in endpoints:
            for rol_id, (rol_nombre, headers) in self.roles_map.items():
                total_subtests += 1
                with self.subTest(
                    rol=rol_id,
                    rol_nombre=rol_nombre,
                    endpoint=ruta,
                    metodo=metodo,
                    permiso=perm_class,
                ):
                    kardex_antes = InventarioKardex.objects.count()
                    kit_antes = KitVeterinarioLotes.objects.count()
                    sol_antes = SolicitudesKit.objects.count()
                    consumos_antes = InventarioConsumosRegistro.objects.count()

                    if metodo == "GET":
                        resp = self.client.get(ruta, **headers)
                    else:
                        # En POST se envía payload vacío {} para provocar 400 en roles autorizados
                        # sin mutar BD ni disparar el bug H-C2 en /retiro-directo/
                        resp = self.client.post(ruta, data={}, format="json", **headers)

                    if rol_id not in roles_autorizados:
                        self.assertEqual(
                            resp.status_code,
                            status.HTTP_403_FORBIDDEN,
                            f"Rol {rol_nombre} ({rol_id}) debió recibir 403 en {metodo} {ruta}, obtuvo {resp.status_code}",
                        )
                        # Comprobación de integridad de base de datos inalterada
                        self.assertEqual(InventarioKardex.objects.count(), kardex_antes)
                        self.assertEqual(KitVeterinarioLotes.objects.count(), kit_antes)
                        self.assertEqual(SolicitudesKit.objects.count(), sol_antes)
                        self.assertEqual(InventarioConsumosRegistro.objects.count(), consumos_antes)
                    else:
                        self.assertNotIn(
                            resp.status_code,
                            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                            f"Rol autorizado {rol_nombre} ({rol_id}) fue bloqueado en {metodo} {ruta} con {resp.status_code}",
                        )

        self.assertEqual(total_subtests, 60, "La matriz RBAC debe ejecutar exactamente 60 subTests (15 endpoints x 4 roles).")

    def test_sec_57_bloqueo_usuario_inactivo_activo_false(self):
        """
        TEST-SEC-57: Bloqueo estricto de usuario inactivo (activo=False).
        - Usuario con activo=False y token JWT válido emitido antes de su desactivación.
        - Peticiones a varios endpoints protegidos de /api/inventario/kit/:
          * Gestor-only: GET /api/inventario/kit/conflictos/
          * Veterinario-only: GET /api/inventario/kit/mi-kit/
          * Gestor-o-veterinario: GET /api/inventario/kit/alertas/
        - Resultado empírico observado y certificado:
          * Código HTTP: 401 Unauthorized (status.HTTP_401_UNAUTHORIZED) de forma idéntica en todos.
          * Cuerpo de error: {'detail': 'El usuario está desactivado.', 'code': 'authentication_failed'}.
          * Responsable (archivo y línea): users/authentication.py:20-21 (CustomJWTAuthentication.get_user),
            donde la clase de autenticación personalizada intercepta la petición durante la fase
            de autenticación previa a los permisos de DRF al detectar user.activo == False,
            elevando AuthenticationFailed('El usuario está desactivado.').
        - DB permanece 100% inalterada.
        """
        # Crear usuario inicialmente activo para emitir token válido, luego desactivarlo
        usuario_inactivo = crear_usuario(rol_id=1, email="inactivo_sec57@cba.gov.co", activo=True)
        token = AccessToken.for_user(usuario_inactivo)
        headers_inactivo = {"HTTP_AUTHORIZATION": f"Bearer {str(token)}"}

        usuario_inactivo.activo = False
        usuario_inactivo.save()

        # También crear un veterinario inactivo para probar endpoints veterinarios
        vet_inactivo = crear_usuario(rol_id=3, email="vet_inactivo_sec57@cba.gov.co", activo=True)
        token_vet = AccessToken.for_user(vet_inactivo)
        headers_vet_inactivo = {"HTTP_AUTHORIZATION": f"Bearer {str(token_vet)}"}

        vet_inactivo.activo = False
        vet_inactivo.save()

        endpoints_a_probar = [
            ("gestor_only", "/api/inventario/kit/conflictos/", headers_inactivo),
            ("veterinario_only", "/api/inventario/kit/mi-kit/", headers_vet_inactivo),
            ("gestor_o_veterinario", "/api/inventario/kit/alertas/", headers_vet_inactivo),
        ]

        kardex_antes = InventarioKardex.objects.count()
        kit_antes = KitVeterinarioLotes.objects.count()
        sol_antes = SolicitudesKit.objects.count()
        consumos_antes = InventarioConsumosRegistro.objects.count()

        for etiqueta, ruta, headers in endpoints_a_probar:
            with self.subTest(caso=etiqueta, endpoint=ruta):
                resp = self.client.get(ruta, **headers)
                # HTTP 401 emitido por users/authentication.py:20-21 (CustomJWTAuthentication)
                self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assertEqual(resp.data.get("code"), "authentication_failed")
                self.assertIn("desactivado", str(resp.data.get("detail", "")).lower())

        # DB inalterada
        self.assertEqual(InventarioKardex.objects.count(), kardex_antes)
        self.assertEqual(KitVeterinarioLotes.objects.count(), kit_antes)
        self.assertEqual(SolicitudesKit.objects.count(), sol_antes)
        self.assertEqual(InventarioConsumosRegistro.objects.count(), consumos_antes)

    def test_sec_58_token_alterado_y_expirado(self):
        """
        TEST-SEC-58: Token con firma alterada y token expirado -> 401 Unauthorized.
        - Construcción con utilidades nativas de SimpleJWT sin red ni secretos externos.
        - Token con firma alterada: se modifica el segmento de signatura del token válido.
        - Token expirado: se fija el claim 'exp' en el pasado.
        - Probado contra al menos 2 endpoints representativos (uno gestor y uno veterinario):
          * Gestor: GET /api/inventario/kit/conflictos/
          * Veterinario: GET /api/inventario/kit/mi-kit/
        - Aserciones de respuesta:
          * Código HTTP: 401 Unauthorized.
          * Cuerpo de error: code='token_not_valid', detail='Given token not valid for any token type'.
          * Mensaje específico: 'Token is invalid' (firma) o 'Token is expired' (vencido).
        - DB permanece 100% inalterada.
        """
        # 1. Generación de tokens alterado y expirado para Gestor
        tok_gestor = AccessToken.for_user(self.user_gestor)
        partes_gestor = str(tok_gestor).split('.')
        token_firma_alterada_gestor = f"{partes_gestor[0]}.{partes_gestor[1]}.firma_adulterada_invalida"

        tok_gestor_exp = AccessToken.for_user(self.user_gestor)
        tok_gestor_exp['exp'] = 1000000000  # Timestamp en el pasado (año 2001)
        token_expirado_gestor = str(tok_gestor_exp)

        # 2. Generación de tokens alterado y expirado para Veterinario
        tok_vet = AccessToken.for_user(self.user_vet)
        partes_vet = str(tok_vet).split('.')
        token_firma_alterada_vet = f"{partes_vet[0]}.{partes_vet[1]}.firma_adulterada_invalida"

        tok_vet_exp = AccessToken.for_user(self.user_vet)
        tok_vet_exp['exp'] = 1000000000
        token_expirado_vet = str(tok_vet_exp)

        casos_seguridad = [
            ("gestor_firma_alterada", "/api/inventario/kit/conflictos/", token_firma_alterada_gestor, "invalid"),
            ("gestor_expirado", "/api/inventario/kit/conflictos/", token_expirado_gestor, "expired"),
            ("veterinario_firma_alterada", "/api/inventario/kit/mi-kit/", token_firma_alterada_vet, "invalid"),
            ("veterinario_expirado", "/api/inventario/kit/mi-kit/", token_expirado_vet, "expired"),
        ]

        kardex_antes = InventarioKardex.objects.count()
        kit_antes = KitVeterinarioLotes.objects.count()
        sol_antes = SolicitudesKit.objects.count()
        consumos_antes = InventarioConsumosRegistro.objects.count()

        for etiqueta, ruta, token_str, tipo_fallo in casos_seguridad:
            with self.subTest(caso=etiqueta, endpoint=ruta):
                headers = {"HTTP_AUTHORIZATION": f"Bearer {token_str}"}
                resp = self.client.get(ruta, **headers)

                self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assertEqual(resp.data.get("code"), "token_not_valid")
                self.assertIn("token not valid", str(resp.data.get("detail", "")).lower())

                # Verificación incondicional del mensaje descriptivo en el array 'messages' de SimpleJWT
                self.assertIn("messages", resp.data)
                mensajes = resp.data["messages"]
                self.assertIsInstance(mensajes, list)
                self.assertTrue(len(mensajes) > 0, f"Se esperaba al menos un mensaje en 'messages' para {etiqueta}")
                msg_texto = str(mensajes[0].get("message", "")).lower()
                if tipo_fallo == "invalid":
                    self.assertIn("invalid", msg_texto)
                elif tipo_fallo == "expired":
                    self.assertIn("expired", msg_texto)

        # DB inalterada
        self.assertEqual(InventarioKardex.objects.count(), kardex_antes)
        self.assertEqual(KitVeterinarioLotes.objects.count(), kit_antes)
        self.assertEqual(SolicitudesKit.objects.count(), sol_antes)
        self.assertEqual(InventarioConsumosRegistro.objects.count(), consumos_antes)
