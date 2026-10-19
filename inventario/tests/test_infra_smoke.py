"""
Test de humo temporal para verificar la infraestructura de pruebas (Fase A).
Este archivo sera eliminado posteriormente por el usuario.
"""

from django.test import TestCase
from inventario.tests.base import VetericanoTransactionTestCase
from users.models import Rol, Usuarios
from peticiones.models import TipoPeticion, EstadoPeticiones
from especies.models import Especie, Raza
from inventario.tests.factories import (
    crear_usuario,
    crear_medicamento,
    crear_lote_bodega,
    crear_lote_kit,
    crear_animal_con_historia,
    crear_peticion_visita,
)


class InfraSmokeTestCase(TestCase):
    """Pruebas de conteo de catalogos base y ejecucion unitaria de factorias."""

    def test_catalogos_base_conteo(self):
        """(i) rol tiene 4 filas y tipo_peticion 1, estado_peticiones 2, especies 1, raza 1."""
        self.assertEqual(Rol.objects.count(), 4)
        self.assertEqual(TipoPeticion.objects.count(), 1)
        self.assertEqual(EstadoPeticiones.objects.count(), 2)
        self.assertEqual(Especie.objects.count(), 1)
        self.assertEqual(Raza.objects.count(), 1)

    def test_ejecucion_factories(self):
        """(ii) cada factory se ejecuta una vez sin error."""
        usuario = crear_usuario()
        self.assertIsNotNone(usuario.pk)

        medicamento = crear_medicamento()
        self.assertIsNotNone(medicamento.pk)

        lote_bodega = crear_lote_bodega(medicamento=medicamento)
        self.assertIsNotNone(lote_bodega.pk)

        lote_kit = crear_lote_kit(veterinario=usuario, lote_bodega=lote_bodega)
        self.assertIsNotNone(lote_kit.pk)

        animal, historia = crear_animal_con_historia()
        self.assertIsNotNone(animal.pk)
        self.assertIsNotNone(historia.pk)

        peticion = crear_peticion_visita(veterinario=usuario)
        self.assertIsNotNone(peticion.pk)

    def test_timeouts_y_timezone_sesion(self):
        """Verifica que la sesion PostgreSQL aplique lock_timeout 3s, statement_timeout 5s, deadlock_timeout 1s y TimeZone America/Bogota."""
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("SHOW lock_timeout;")
            lock_timeout = cur.fetchone()[0]
            cur.execute("SHOW statement_timeout;")
            statement_timeout = cur.fetchone()[0]
            cur.execute("SHOW deadlock_timeout;")
            deadlock_timeout = cur.fetchone()[0]
            cur.execute("SHOW TimeZone;")
            timezone_val = cur.fetchone()[0]

        self.assertEqual(lock_timeout, '3s')
        self.assertEqual(statement_timeout, '5s')
        self.assertEqual(deadlock_timeout, '1s')
        self.assertEqual(timezone_val, 'America/Bogota')


class InfraSmokeTransactionTestCase(VetericanoTransactionTestCase):
    """Prueba del teardown dinamico para tests transaccionales."""

    def test_teardown_limpia_usuarios_y_preserva_roles(self):
        """(iii) una clase VetericanoTransactionTestCase que cree un usuario, y compruebe tras self._fixture_teardown() que usuarios tiene 0 filas y rol 4."""
        user = crear_usuario()
        self.assertIsNotNone(user.pk)
        self.assertGreater(Usuarios.objects.count(), 0)

        # Ejecucion explicita del teardown dinamico
        self._fixture_teardown()

        self.assertEqual(Usuarios.objects.count(), 0)
        self.assertEqual(Rol.objects.count(), 4)
