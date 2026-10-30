"""
Custom Test Runner para el modulo de inventario y enganches clinicos.
Aplica configuracion de sesion por conexion y aislamiento estricto de servicios externos.
"""

import os
import sys
from unittest.mock import MagicMock
import requests
from django.test.runner import DiscoverRunner
from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def configurar_sesion_postgres(sender, connection, **kwargs):
    """
    Aplica timeouts y zona horaria estrictos unicamente sobre la base de datos de test
    (test_vetericano), protegiendo la conexion administrativa 'postgres' que clona la plantilla.
    """
    test_db_name = os.getenv('TEST_DB_NAME', 'test_vetericano')
    if connection.settings_dict.get('NAME') == test_db_name:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '3000ms';")
            cursor.execute("SET statement_timeout = '5000ms';")
            cursor.execute("SET deadlock_timeout = '1000ms';")
            cursor.execute("SET TIME ZONE 'America/Bogota';")


MOCK_CLOUDINARY_URL = "https://res.cloudinary.com/vetericano-test/image/upload/sample.jpg"


def mock_subir_o_asegurar_cloudinary(imagen_str: str) -> str:
    """
    Mock determinista que conserva la semantica real de subir_o_asegurar_cloudinary sin red:
    - None o cadena vacia/solo espacios -> None
    - valor que empieza con http:// o https:// -> devolver valor[:500] tal cual
    - cualquier otro (base64, data URI, ruta) -> MOCK_CLOUDINARY_URL
    """
    if not imagen_str:
        return None

    valor = str(imagen_str).strip()
    if not valor:
        return None

    if valor.startswith('http://') or valor.startswith('https://'):
        return valor[:500]

    return MOCK_CLOUDINARY_URL


def mock_cloudinary_uploader_upload(*args, **kwargs):
    """Bloqueo de seguridad: impide cualquier llamada a cloudinary.uploader.upload."""
    raise RuntimeError(
        "BLOQUEO DE SEGURIDAD: Se detecto intento de llamada a cloudinary.uploader.upload durante las pruebas."
    )


class VetericanoTestRunner(DiscoverRunner):
    """
    Test runner personalizado que aisla llamadas externas (Cloudinary)
    y gestiona el ciclo de vida de pruebas del modulo.
    """
    _original_requests_post = None
    _orig_peticiones_utils_subir = None
    _orig_peticiones_serializers_subir = None
    _orig_users_serializers_subir = None
    _created_cloudinary_mock = False
    _original_cloudinary_upload = None

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)

        # 1. Bloqueo a nivel de transporte HTTP sobre peticiones hacia Cloudinary
        self._original_requests_post = requests.post

        def mock_requests_post(url, *args, **kwargs_inner):
            url_str = str(url).lower()
            if 'cloudinary.com' in url_str:
                raise RuntimeError(
                    f"BLOQUEO DE SEGURIDAD: Se detecto intento de llamada externa a Cloudinary ({url}) durante las pruebas."
                )
            return self._original_requests_post(url, *args, **kwargs_inner)

        requests.post = mock_requests_post

        # 2. Parcheo de subir_o_asegurar_cloudinary donde se define y donde se importa/usa
        # peticiones/utils.py:11 (definicion)
        try:
            import peticiones.utils
            self._orig_peticiones_utils_subir = getattr(peticiones.utils, 'subir_o_asegurar_cloudinary', None)
            peticiones.utils.subir_o_asegurar_cloudinary = mock_subir_o_asegurar_cloudinary
        except ImportError:
            pass

        # peticiones/api/serializers.py:10 (import y uso en lineas 83, 90, 443, 465, 486)
        try:
            import peticiones.api.serializers
            self._orig_peticiones_serializers_subir = getattr(peticiones.api.serializers, 'subir_o_asegurar_cloudinary', None)
            peticiones.api.serializers.subir_o_asegurar_cloudinary = mock_subir_o_asegurar_cloudinary
        except ImportError:
            pass

        # users/api/serializers.py:9 (import y uso en linea 142)
        try:
            import users.api.serializers
            self._orig_users_serializers_subir = getattr(users.api.serializers, 'subir_o_asegurar_cloudinary', None)
            users.api.serializers.subir_o_asegurar_cloudinary = mock_subir_o_asegurar_cloudinary
        except ImportError:
            pass

        # 3. Bloqueo de cloudinary.uploader.upload con RuntimeError
        if 'cloudinary' in sys.modules or 'cloudinary.uploader' in sys.modules:
            try:
                import cloudinary.uploader
                self._original_cloudinary_upload = getattr(cloudinary.uploader, 'upload', None)
                cloudinary.uploader.upload = mock_cloudinary_uploader_upload
            except (ImportError, AttributeError):
                pass
        else:
            mock_cloudinary = MagicMock()
            mock_uploader = MagicMock()
            mock_uploader.upload.side_effect = mock_cloudinary_uploader_upload
            mock_cloudinary.uploader = mock_uploader
            sys.modules['cloudinary'] = mock_cloudinary
            sys.modules['cloudinary.uploader'] = mock_uploader
            self._created_cloudinary_mock = True

    def teardown_test_environment(self, **kwargs):
        # Restaurar requests.post
        if self._original_requests_post is not None:
            requests.post = self._original_requests_post

        # Restaurar funciones de peticiones/serializers
        try:
            import peticiones.utils
            if self._orig_peticiones_utils_subir is not None:
                peticiones.utils.subir_o_asegurar_cloudinary = self._orig_peticiones_utils_subir
        except ImportError:
            pass

        try:
            import peticiones.api.serializers
            if self._orig_peticiones_serializers_subir is not None:
                peticiones.api.serializers.subir_o_asegurar_cloudinary = self._orig_peticiones_serializers_subir
        except ImportError:
            pass

        try:
            import users.api.serializers
            if self._orig_users_serializers_subir is not None:
                users.api.serializers.subir_o_asegurar_cloudinary = self._orig_users_serializers_subir
        except ImportError:
            pass

        # Restaurar cloudinary.uploader.upload
        if self._created_cloudinary_mock:
            sys.modules.pop('cloudinary.uploader', None)
            sys.modules.pop('cloudinary', None)
        elif self._original_cloudinary_upload is not None:
            try:
                import cloudinary.uploader
                cloudinary.uploader.upload = self._original_cloudinary_upload
            except (ImportError, AttributeError):
                pass

        super().teardown_test_environment(**kwargs)
