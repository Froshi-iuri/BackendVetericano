import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.hashers import check_password, is_password_usable, make_password
from django.test import SimpleTestCase, override_settings
import jwt as pyjwt
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from users.api.views import GoogleLoginView, LoginView, RegisterView
from users.authentication import CustomJWTAuthentication
from users.constants import (
    ROL_ADMINISTRADOR,
    ROL_JURIDICO,
    ROL_PETICIONARIO,
    ROL_SUPER_ADMINISTRADOR,
    ROL_VETERINARIO,
)
from users.views import confirmar_recuperacion, solicitar_recuperacion


def _build_fake_user(
    *,
    id_usuario=10,
    email="vet@vetericano.com",
    password_plain="ClaveSegura123",
    nombre="Ana",
    apellido="Lopez",
    telefono="3001234567",
    foto_perfil=None,
    matricula_profesional="MV-1234",
    id_rol=3,
    nombre_rol="Veterinario",
    activo=True,
):
    rol = SimpleNamespace(id_rol=id_rol, nombre_rol=nombre_rol)
    return SimpleNamespace(
        id_usuario=id_usuario,
        email=email,
        password=make_password(password_plain),
        nombre=nombre,
        apellido=apellido,
        telefono=telefono,
        foto_perfil=foto_perfil,
        matricula_profesional=matricula_profesional,
        id_rol=rol,
        activo=activo,
    )


def _mock_queryset(items):
    queryset = MagicMock()
    queryset.filter.return_value = queryset
    queryset.order_by.return_value = items
    return queryset


class PublicAuthEndpointsTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def test_public_endpoints_disable_authentication_classes(self):
        self.assertEqual(LoginView.authentication_classes, [])
        self.assertEqual(GoogleLoginView.authentication_classes, [])
        self.assertEqual(RegisterView.authentication_classes, [])
        self.assertEqual(solicitar_recuperacion.cls.authentication_classes, [])
        self.assertEqual(confirmar_recuperacion.cls.authentication_classes, [])

    @patch("users.api.serializers.Usuarios.objects")
    def test_login_succeeds_even_with_stale_bearer_header(self, mock_objects):
        user = _build_fake_user()
        mock_objects.select_related.return_value = _mock_queryset([user])

        response = self.client.post(
            "/api/usuarios/login/",
            {"email": user.email, "password": "ClaveSegura123"},
            format="json",
            HTTP_AUTHORIZATION="Bearer token.expirado.invalido",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["mensaje"], "Bienvenido, Ana Lopez")
        self.assertEqual(data["email"], user.email)
        self.assertEqual(data["id_usuario"], user.id_usuario)
        self.assertEqual(data["nombre"], "Ana")
        self.assertEqual(data["apellido"], "Lopez")
        self.assertEqual(data["telefono"], "3001234567")
        self.assertIsNone(data["foto_perfil"])
        self.assertIsNone(data["foto"])
        self.assertEqual(data["matricula_profesional"], "MV-1234")
        self.assertEqual(data["id_rol"], 3)
        self.assertIn("access", data["tokens"])
        self.assertIn("refresh", data["tokens"])

        access_payload = AccessToken(data["tokens"]["access"])
        self.assertEqual(access_payload["user_id"], str(user.id_usuario))
        self.assertEqual(access_payload["id_rol"], 3)
        self.assertEqual(access_payload["nombre_rol"], "Veterinario")
        self.assertEqual(access_payload["foto_perfil"], "")
        self.assertEqual(access_payload["foto"], "")

    @patch("users.api.serializers.Usuarios.objects")
    def test_login_preserves_password_whitespace(self, mock_objects):
        password_con_espacios = "  ClaveConEspacio  "
        user = _build_fake_user(password_plain=password_con_espacios)
        mock_objects.select_related.return_value = _mock_queryset([user])

        ok_response = self.client.post(
            "/api/usuarios/login/",
            {"email": "  VET@VETERIANO.COM  ", "password": password_con_espacios},
            format="json",
        )
        self.assertEqual(ok_response.status_code, status.HTTP_200_OK)
        mock_objects.select_related.return_value.filter.assert_called_with(
            email__iexact="VET@VETERIANO.COM"
        )

        wrong_response = self.client.post(
            "/api/usuarios/login/",
            {"email": "vet@vetericano.com", "password": password_con_espacios.strip()},
            format="json",
        )
        self.assertEqual(wrong_response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("users.api.serializers.Usuarios.objects")
    def test_login_handles_empty_apellido_and_all_roles(self, mock_objects):
        roles = [
            (1, "Administrador"),
            (2, "Juridico"),
            (3, "Veterinario"),
            (4, "Peticionario"),
            (5, "super_administrador"),
        ]
        for id_rol, nombre_rol in roles:
            user = _build_fake_user(
                id_usuario=100 + id_rol,
                apellido=None,
                id_rol=id_rol,
                nombre_rol=nombre_rol,
            )
            mock_objects.select_related.return_value = _mock_queryset([user])

            response = self.client.post(
                "/api/usuarios/login/",
                {"email": user.email, "password": "ClaveSegura123"},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.json()
            self.assertEqual(data["mensaje"], "Bienvenido, Ana")
            self.assertEqual(data["id_rol"], id_rol)

    @patch("users.api.serializers.Usuarios.objects")
    def test_login_rejects_inactive_and_prefers_active_match(self, mock_objects):
        inactive_user = _build_fake_user(id_usuario=1, activo=False)
        mock_objects.select_related.return_value = _mock_queryset([inactive_user])

        inactive_response = self.client.post(
            "/api/usuarios/login/",
            {"email": inactive_user.email, "password": "ClaveSegura123"},
            format="json",
        )
        self.assertEqual(inactive_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            inactive_response.json()["non_field_errors"],
            ["Esta cuenta ha sido desactivada."],
        )

        active_user = _build_fake_user(id_usuario=2, activo=True, id_rol=4, nombre_rol="Peticionario")
        mock_objects.select_related.return_value = _mock_queryset([active_user, inactive_user])

        active_response = self.client.post(
            "/api/usuarios/login/",
            {"email": active_user.email, "password": "ClaveSegura123"},
            format="json",
        )
        self.assertEqual(active_response.status_code, status.HTTP_200_OK)
        self.assertEqual(active_response.json()["id_usuario"], 2)

    @patch("users.api.serializers.Usuarios.objects")
    def test_login_rejects_invalid_credentials(self, mock_objects):
        user = _build_fake_user()
        mock_objects.select_related.return_value = _mock_queryset([user])

        wrong_pwd = self.client.post(
            "/api/usuarios/login/",
            {"email": user.email, "password": "incorrecta"},
            format="json",
        )
        self.assertEqual(wrong_pwd.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong_pwd.json()["non_field_errors"], ["Credenciales incorrectas."])

        mock_objects.select_related.return_value = _mock_queryset([])
        unknown_email = self.client.post(
            "/api/usuarios/login/",
            {"email": "noexiste@vetericano.com", "password": "incorrecta"},
            format="json",
        )
        self.assertEqual(unknown_email.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(unknown_email.json()["non_field_errors"], ["Credenciales incorrectas."])

    def test_register_ignores_stale_bearer_header(self):
        response = self.client.post(
            "/api/usuarios/register/",
            {},
            format="json",
            HTTP_AUTHORIZATION="Bearer token.expirado.invalido",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_recovery_endpoints_ignore_stale_bearer_header(self):
        solicitar_resp = self.client.post(
            "/api/usuarios/recuperar-password/",
            {},
            format="json",
            HTTP_AUTHORIZATION="Bearer token.expirado.invalido",
        )
        self.assertNotEqual(solicitar_resp.status_code, status.HTTP_401_UNAUTHORIZED)

        confirmar_resp = self.client.post(
            "/api/usuarios/confirmar-password/",
            {},
            format="json",
            HTTP_AUTHORIZATION="Bearer token.expirado.invalido",
        )
        self.assertNotEqual(confirmar_resp.status_code, status.HTTP_401_UNAUTHORIZED)


class JwtAuthAndRefreshTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    @patch("users.authentication.Usuarios.objects")
    def test_custom_jwt_authentication_uses_select_related_and_checks_activo(self, mock_objects):
        active_user = _build_fake_user(activo=True)
        mock_objects.select_related.return_value.get.return_value = active_user

        auth = CustomJWTAuthentication()
        user = auth.get_user({"user_id": active_user.id_usuario})
        self.assertIs(user, active_user)
        mock_objects.select_related.assert_called_with("id_rol")
        mock_objects.select_related.return_value.get.assert_called_with(
            id_usuario=active_user.id_usuario
        )

        inactive_user = _build_fake_user(activo=False)
        mock_objects.select_related.return_value.get.return_value = inactive_user
        with self.assertRaises(AuthenticationFailed):
            auth.get_user({"user_id": inactive_user.id_usuario})

    @patch("users.api.token_refresh.Usuarios.objects")
    def test_token_refresh_returns_new_access_token_with_role_claims(self, mock_objects):
        user = _build_fake_user(id_usuario=55, id_rol=4, nombre_rol="Peticionario")
        mock_objects.select_related.return_value.get.return_value = user
        refresh = RefreshToken.for_user(user)

        response = self.client.post(
            "/api/token/refresh/",
            {"refresh": str(refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("access", data)
        access_payload = AccessToken(data["access"])
        self.assertEqual(access_payload["user_id"], "55")
        self.assertEqual(access_payload["id_rol"], 4)
        self.assertEqual(access_payload["nombre_rol"], "Peticionario")


@override_settings(
    GOOGLE_OAUTH_CLIENT_IDS=("test-web-client.apps.googleusercontent.com",),
    GOOGLE_OAUTH_ALLOWED_LINK_ROLES=(ROL_VETERINARIO, ROL_PETICIONARIO),
    GOOGLE_OAUTH_ALLOWED_HOSTED_DOMAINS=(),
)
class GoogleLoginTests(SimpleTestCase):
    CLIENT_ID = "test-web-client.apps.googleusercontent.com"
    KID = "google-test-key-1"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.rogue_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk_dict = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(cls.private_key.public_key()))
        jwk_dict["kid"] = cls.KID
        jwk_dict["use"] = "sig"
        jwk_dict["alg"] = "RS256"
        cls.certs_bytes = json.dumps({"keys": [jwk_dict]}).encode("utf-8")

    def setUp(self):
        self.client = APIClient()

    def _fake_transport(self, *, status_code=200, exc=None):
        certs_bytes = self.certs_bytes

        def _request(url, method="GET", **kwargs):
            if exc is not None:
                raise exc
            return SimpleNamespace(status=status_code, data=certs_bytes)

        return _request

    def _mint_google_token(self, claims_override=None, *, private_key=None, kid=None, remove_claims=()):
        now = int(time.time())
        payload = {
            "iss": "https://accounts.google.com",
            "aud": self.CLIENT_ID,
            "sub": "google-sub-987654321",
            "email": "Ciudadano.Real@gmail.com",
            "email_verified": True,
            "given_name": "Carlos",
            "family_name": "Mendez",
            "picture": "https://lh3.googleusercontent.com/a/avatar-real",
            "iat": now - 60,
            "exp": now + 3600,
        }
        if claims_override:
            payload.update(claims_override)
        for key in remove_claims:
            payload.pop(key, None)
        return pyjwt.encode(
            payload,
            private_key or self.private_key,
            algorithm="RS256",
            headers={"kid": kid if kid is not None else self.KID},
        )

    def test_rejects_android_account_marker_and_malformed_tokens(self):
        for payload in (
            {},
            {"id_token": ""},
            {"id_token": "android_account:alguien@gmail.com"},
            {"id_token": "ANDROID_ACCOUNT:alguien@gmail.com"},
            {"id_token": "no-es-jwt"},
            {"id_token": "uno.dos"},
        ):
            with self.subTest(payload=payload):
                resp = self.client.post("/api/usuarios/google-login/", payload, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(GOOGLE_OAUTH_CLIENT_IDS=())
    def test_rejects_fail_closed_when_google_client_id_unconfigured(self):
        token = self._mint_google_token()
        resp = self.client.post(
            "/api/usuarios/google-login/",
            {"id_token": token},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @patch("users.services.google_oauth_service._obtener_rol_peticionario")
    @patch("users.services.google_oauth_service.transaction.atomic")
    @patch("users.services.google_oauth_service.Usuarios.objects")
    @patch("users.services.google_oauth_service._get_transport_request")
    def test_valid_token_creates_new_peticionario_with_unusable_password_and_android_payload(
        self, mock_transport, mock_objects, mock_atomic, mock_rol_peticionario
    ):
        mock_transport.return_value = self._fake_transport()
        mock_objects.select_related.return_value = _mock_queryset([])
        mock_rol_peticionario.return_value = SimpleNamespace(
            id_rol=ROL_PETICIONARIO,
            nombre_rol="Peticionario",
        )
        mock_atomic.return_value.__enter__ = MagicMock(return_value=None)
        mock_atomic.return_value.__exit__ = MagicMock(return_value=False)

        captured = {}

        def _fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                id_usuario=701,
                email=kwargs["email"],
                password=kwargs["password"],
                nombre=kwargs["nombre"],
                apellido=kwargs["apellido"],
                identificacion=kwargs.get("identificacion"),
                telefono=kwargs.get("telefono"),
                foto_perfil=kwargs.get("foto_perfil"),
                matricula_profesional=None,
                id_rol=kwargs["id_rol"],
                activo=kwargs["activo"],
            )

        mock_objects.create.side_effect = _fake_create
        token = self._mint_google_token()

        resp = self.client.post(
            "/api/usuarios/google-login/",
            {
                "id_token": token,
                "email": "ciudadano.real@gmail.com",
                "nombre": "Ignorado",
                "apellido": "Cliente",
            },
            format="json",
            HTTP_AUTHORIZATION="Bearer token.expirado.invalido",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertTrue(data["es_nuevo_usuario"])
        self.assertEqual(data["email"], "ciudadano.real@gmail.com")
        self.assertEqual(data["id_usuario"], 701)
        self.assertEqual(data["id_rol"], ROL_PETICIONARIO)
        self.assertEqual(data["nombre"], "Carlos")
        self.assertEqual(data["apellido"], "Mendez")
        self.assertEqual(data["foto_perfil"], "https://lh3.googleusercontent.com/a/avatar-real")
        self.assertEqual(data["foto"], data["foto_perfil"])
        self.assertEqual(data["mensaje"], "Bienvenido, Carlos Mendez")
        self.assertIn("access", data["tokens"])
        self.assertIn("refresh", data["tokens"])

        access_payload = AccessToken(data["tokens"]["access"])
        self.assertEqual(access_payload["user_id"], "701")
        self.assertEqual(access_payload["id_rol"], ROL_PETICIONARIO)
        self.assertEqual(access_payload["nombre_rol"], "Peticionario")

        self.assertEqual(captured["id_rol"].id_rol, ROL_PETICIONARIO)
        self.assertIsNone(captured["identificacion"])
        self.assertTrue(captured["activo"])
        self.assertFalse(is_password_usable(captured["password"]))
        self.assertFalse(check_password("", captured["password"]))
        self.assertFalse(check_password("GglVet9000000000000", captured["password"]))

    @patch("users.api.serializers.Usuarios.objects")
    def test_google_registered_user_with_unusable_password_cannot_use_password_login(self, mock_objects):
        google_user = _build_fake_user(
            id_usuario=702,
            email="nuevo.google@gmail.com",
            id_rol=ROL_PETICIONARIO,
            nombre_rol="Peticionario",
        )
        google_user.password = make_password(None)
        mock_objects.select_related.return_value = _mock_queryset([google_user])

        for candidate_pwd in ("", "GglVet90123456789ab", "ClaveCualquiera123"):
            resp = self.client.post(
                "/api/usuarios/login/",
                {"email": google_user.email, "password": candidate_pwd},
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("users.services.google_oauth_service.Usuarios.objects")
    @patch("users.services.google_oauth_service._get_transport_request")
    def test_links_existing_active_peticionario_and_veterinario_preserving_role_and_password(
        self, mock_transport, mock_objects
    ):
        mock_transport.return_value = self._fake_transport()

        for id_rol, nombre_rol, existing_foto in (
            (ROL_PETICIONARIO, "Peticionario", ""),
            (ROL_VETERINARIO, "Veterinario", "https://res.cloudinary.com/vet/foto.jpg"),
        ):
            user = _build_fake_user(
                id_usuario=300 + id_rol,
                email="colega@gmail.com",
                password_plain="MiClaveReal123",
                nombre="Laura",
                apellido="Rios",
                foto_perfil=existing_foto,
                id_rol=id_rol,
                nombre_rol=nombre_rol,
                activo=True,
            )
            user.save = MagicMock()
            original_hash = user.password
            mock_objects.select_related.return_value = _mock_queryset([user])

            token = self._mint_google_token({"email": "colega@gmail.com"})
            resp = self.client.post("/api/usuarios/google-login/", {"id_token": token}, format="json")

            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            data = resp.json()
            self.assertFalse(data["es_nuevo_usuario"])
            self.assertEqual(data["id_rol"], id_rol)
            self.assertEqual(user.password, original_hash)
            if not existing_foto:
                user.save.assert_called_once_with(update_fields=["foto_perfil"])
                self.assertEqual(data["foto_perfil"], "https://lh3.googleusercontent.com/a/avatar-real")
            else:
                user.save.assert_not_called()
                self.assertEqual(data["foto_perfil"], existing_foto)

    @patch("users.services.google_oauth_service.Usuarios.objects")
    @patch("users.services.google_oauth_service._get_transport_request")
    def test_rejects_inactive_user_and_privileged_roles_by_policy(self, mock_transport, mock_objects):
        mock_transport.return_value = self._fake_transport()

        inactive_user = _build_fake_user(
            id_usuario=800,
            email="inactivo@gmail.com",
            id_rol=ROL_PETICIONARIO,
            nombre_rol="Peticionario",
            activo=False,
        )
        inactive_user.save = MagicMock()
        mock_objects.select_related.return_value = _mock_queryset([inactive_user])

        token_inactivo = self._mint_google_token({"email": "inactivo@gmail.com"})
        resp_inactivo = self.client.post(
            "/api/usuarios/google-login/",
            {"id_token": token_inactivo},
            format="json",
        )
        self.assertEqual(resp_inactivo.status_code, status.HTTP_403_FORBIDDEN)
        inactive_user.save.assert_not_called()

        for id_rol, nombre_rol in (
            (ROL_ADMINISTRADOR, "Administrador"),
            (ROL_JURIDICO, "Juridico"),
            (ROL_SUPER_ADMINISTRADOR, "super_administrador"),
        ):
            priv_user = _build_fake_user(
                id_usuario=810 + id_rol,
                email=f"rol{id_rol}@gmail.com",
                id_rol=id_rol,
                nombre_rol=nombre_rol,
                activo=True,
            )
            mock_objects.select_related.return_value = _mock_queryset([priv_user])
            token_priv = self._mint_google_token({"email": priv_user.email})
            resp_priv = self.client.post(
                "/api/usuarios/google-login/",
                {"id_token": token_priv},
                format="json",
            )
            self.assertEqual(resp_priv.status_code, status.HTTP_403_FORBIDDEN)

    @patch("users.services.google_oauth_service.Usuarios.objects")
    @patch("users.services.google_oauth_service._get_transport_request")
    def test_rejects_expired_forged_wrong_audience_wrong_issuer_unverified_and_email_mismatch(
        self, mock_transport, mock_objects
    ):
        mock_transport.return_value = self._fake_transport()
        mock_objects.select_related.return_value = _mock_queryset([])

        now = int(time.time())
        invalid_tokens = [
            ("expirado", self._mint_google_token({"exp": now - 120})),
            ("firma_falsa", self._mint_google_token(private_key=self.rogue_private_key)),
            ("kid_desconocido", self._mint_google_token(kid="otro-kid")),
            ("audiencia_incorrecta", self._mint_google_token({"aud": "otro-cliente.apps.googleusercontent.com"})),
            ("emisor_incorrecto", self._mint_google_token({"iss": "https://evil.example.com"})),
            ("correo_no_verificado_bool", self._mint_google_token({"email_verified": False})),
            ("correo_no_verificado_str", self._mint_google_token({"email_verified": "true"})),
            ("sin_exp", self._mint_google_token(remove_claims=("exp",))),
            ("sin_sub", self._mint_google_token({"sub": ""})),
        ]

        for label, bad_token in invalid_tokens:
            with self.subTest(caso=label):
                resp = self.client.post(
                    "/api/usuarios/google-login/",
                    {"id_token": bad_token},
                    format="json",
                )
                self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

        # Payload tampering check: replace signature segment on valid token
        valid_token = self._mint_google_token()
        header_part, body_part, sig_part = valid_token.split(".")
        tampered_sig = ("A" if sig_part[0] != "A" else "B") + sig_part[1:]
        resp_tampered = self.client.post(
            "/api/usuarios/google-login/",
            {"id_token": f"{header_part}.{body_part}.{tampered_sig}"},
            format="json",
        )
        self.assertEqual(resp_tampered.status_code, status.HTTP_401_UNAUTHORIZED)

        # Mismatched email in request body vs verified Google token email
        resp_mismatch = self.client.post(
            "/api/usuarios/google-login/",
            {"id_token": valid_token, "email": "victima@vetericano.com"},
            format="json",
        )
        self.assertEqual(resp_mismatch.status_code, status.HTTP_400_BAD_REQUEST)
        mock_objects.create.assert_not_called()

    @patch("users.services.google_oauth_service._get_transport_request")
    def test_google_certs_transport_error_returns_503(self, mock_transport):
        mock_transport.return_value = self._fake_transport(status_code=503)
        token = self._mint_google_token()
        resp = self.client.post(
            "/api/usuarios/google-login/",
            {"id_token": token},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

