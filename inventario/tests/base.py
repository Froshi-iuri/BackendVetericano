"""
Clases base de prueba para el modulo de inventario y enganches clinicos.
Implementa aislamiento por rollback o teardown dinamico para concurrencia.
"""

from django.test import TestCase, TransactionTestCase
from django.db import connection


class VetericanoTestCase(TestCase):
    """
    Clase base para pruebas unitarias y de integracion estandar.
    Opera bajo transacciones aisladas revertidas al finalizar cada prueba.
    """
    pass


class VetericanoTransactionTestCase(TransactionTestCase):
    """
    Clase base para tests de concurrencia e integracion transaccional real.
    Sobrescribe _fixture_teardown para evitar el flush estandar de Django
    que choca con las FK RESTRICT de tablas unmanaged y con los triggers del kardex.
    """
    def _fixture_teardown(self):
        with connection.cursor() as cursor:
            # 1. Terminar sesiones residuales de hilos secundarios en la base de datos de test
            cursor.execute("""
                SELECT pg_terminate_backend(pid) 
                FROM pg_stat_activity 
                WHERE datname = current_database() 
                  AND pid <> pg_backend_pid();
            """)

            # 2. Desactivar triggers de integridad e inmutabilidad en la sesion de limpieza
            cursor.execute("SET session_replication_role = 'replica';")
            try:
                # 3. Truncar dinamicamente TODAS las tablas de public excepto django_* y auth_*
                cursor.execute(r"""
                    DO $$
                    DECLARE
                        r RECORD;
                        tablas TEXT := '';
                    BEGIN
                        FOR r IN (
                            SELECT tablename 
                            FROM pg_tables 
                            WHERE schemaname = 'public' 
                              AND tablename !~ '^(django|auth)_'
                        ) LOOP
                            tablas := tablas || quote_ident(r.tablename) || ', ';
                        END LOOP;
                        IF tablas <> '' THEN
                            tablas := substring(tablas from 1 for length(tablas) - 2);
                            EXECUTE 'TRUNCATE TABLE ' || tablas || ' RESTART IDENTITY CASCADE;';
                        END IF;
                    END $$;
                """)
            finally:
                cursor.execute("SET session_replication_role = 'origin';")

        # 4. Re-sembrar catalogos esenciales de forma idempotente con pg_get_serial_sequence
        from inventario.tests.factories import sembrar_catalogos_base
        sembrar_catalogos_base()
