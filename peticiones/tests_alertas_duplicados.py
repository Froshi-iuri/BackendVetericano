"""Pruebas unitarias y de restricciones SQL aisladas para PeticionPosibleDuplicado (Fase 4A).

Todas las validaciones de base de datos se ejecutan contra una instancia SQLite
en memoria completamente aislada y/o compilación DDL offline; nunca se conectan
a Supabase ni modifican datos reales.
"""

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, models
from django.db.backends.sqlite3.base import DatabaseWrapper as SqliteDatabaseWrapper
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase

from peticiones.models import Peticiones, PeticionPosibleDuplicado
from users.models import Usuarios


class PeticionPosibleDuplicadoModeloTest(SimpleTestCase):
    """Valida metadatos, validaciones de dominio, ausencia de CASCADE y migración 0005."""

    def test_tabla_y_campos_principales_configurados(self):
        meta = PeticionPosibleDuplicado._meta
        self.assertTrue(meta.managed)
        self.assertEqual(meta.db_table, 'peticion_posible_duplicado')
        self.assertEqual(meta.pk.name, 'id_alerta')

        campos_esperados = {
            'id_alerta',
            'peticion_menor',
            'peticion_mayor',
            'peticion_origen',
            'estado_revision',
            'peticion_principal',
            'similitud_texto',
            'coincidencia_texto',
            'distancia_metros',
            'coincidencia_gps',
            'distancia_hamming',
            'similitud_imagen',
            'coincidencia_imagen',
            'score_combinado',
            'criterios_coincidencia',
            'origen_deteccion',
            'revisado_por',
            'observacion_revision',
            'fecha_revision',
            'fecha_creacion',
            'fecha_actualizacion',
        }
        nombres_campos = {f.name for f in meta.fields}
        self.assertEqual(nombres_campos, campos_esperados)

    def test_no_hay_eliminaciones_en_cascada_en_foreign_keys(self):
        fks = {
            f.name: f
            for f in PeticionPosibleDuplicado._meta.fields
            if isinstance(f, models.ForeignKey)
        }
        self.assertEqual(
            set(fks.keys()),
            {
                'peticion_menor',
                'peticion_mayor',
                'peticion_origen',
                'peticion_principal',
                'revisado_por',
            },
        )
        for nombre, campo in fks.items():
            self.assertIsNot(
                campo.remote_field.on_delete,
                models.CASCADE,
                f'El campo {nombre} no debe usar on_delete=CASCADE',
            )

        self.assertIs(fks['peticion_menor'].remote_field.on_delete, models.PROTECT)
        self.assertIs(fks['peticion_mayor'].remote_field.on_delete, models.PROTECT)
        self.assertIs(fks['peticion_principal'].remote_field.on_delete, models.PROTECT)
        self.assertIs(fks['peticion_origen'].remote_field.on_delete, models.SET_NULL)
        self.assertIs(fks['revisado_por'].remote_field.on_delete, models.SET_NULL)

        self.assertIs(fks['peticion_menor'].remote_field.model, Peticiones)
        self.assertIs(fks['peticion_mayor'].remote_field.model, Peticiones)
        self.assertIs(fks['peticion_principal'].remote_field.model, Peticiones)
        self.assertIs(fks['peticion_origen'].remote_field.model, Peticiones)
        self.assertIs(fks['revisado_por'].remote_field.model, Usuarios)

    def test_no_se_creo_tabla_opcional_de_cache_hashes_en_fase_4a(self):
        nombres_modelos = {m.__name__ for m in apps.get_app_config('peticiones').get_models()}
        self.assertIn('PeticionPosibleDuplicado', nombres_modelos)
        self.assertNotIn('PeticionEvidenciaHash', nombres_modelos)

    def test_normalizar_par_ids_ordena_y_rechaza_autoreferencia_e_invalidos(self):
        self.assertEqual(PeticionPosibleDuplicado.normalizar_par_ids(25, 10), (10, 25))
        self.assertEqual(PeticionPosibleDuplicado.normalizar_par_ids('10', 25), (10, 25))

        for par_invalido in [
            (7, 7),
            (None, 7),
            (7, None),
            (True, 7),
            (0, 7),
            (-3, 7),
            ('abc', 7),
        ]:
            with self.subTest(par=par_invalido):
                with self.assertRaises(ValidationError):
                    PeticionPosibleDuplicado.normalizar_par_ids(*par_invalido)

    def test_normalizar_par_peticiones_ordena_instancias(self):
        p_mayor = Peticiones(id_peticion=30)
        p_menor = Peticiones(id_peticion=12)
        menor, mayor = PeticionPosibleDuplicado.normalizar_par_peticiones(p_mayor, p_menor)
        self.assertIs(menor, p_menor)
        self.assertIs(mayor, p_mayor)

        with self.assertRaises(ValidationError):
            PeticionPosibleDuplicado.normalizar_par_peticiones(p_menor, Peticiones(id_peticion=12))
        with self.assertRaises(ValidationError):
            PeticionPosibleDuplicado.normalizar_par_peticiones(p_menor, Peticiones())

    def test_clean_rechaza_autoreferencia_par_desordenado_estado_y_referencias_fuera_del_par(self):
        # Válido
        alerta_valida = PeticionPosibleDuplicado(
            peticion_menor_id=5,
            peticion_mayor_id=10,
            peticion_origen_id=10,
            peticion_principal_id=5,
            estado_revision=PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
        )
        alerta_valida.clean()

        # Autorreferencia
        with self.assertRaises(ValidationError) as ctx_self:
            PeticionPosibleDuplicado(peticion_menor_id=5, peticion_mayor_id=5).clean()
        self.assertIn('peticion_mayor', ctx_self.exception.message_dict)

        # Par invertido
        with self.assertRaises(ValidationError) as ctx_orden:
            PeticionPosibleDuplicado(peticion_menor_id=10, peticion_mayor_id=5).clean()
        self.assertIn('peticion_menor', ctx_orden.exception.message_dict)

        # Estado inválido
        with self.assertRaises(ValidationError) as ctx_estado:
            PeticionPosibleDuplicado(
                peticion_menor_id=5,
                peticion_mayor_id=10,
                estado_revision='OTRO_ESTADO',
            ).clean()
        self.assertIn('estado_revision', ctx_estado.exception.message_dict)

        # Petición principal fuera del par
        with self.assertRaises(ValidationError) as ctx_principal:
            PeticionPosibleDuplicado(
                peticion_menor_id=5,
                peticion_mayor_id=10,
                peticion_principal_id=99,
            ).clean()
        self.assertIn('peticion_principal', ctx_principal.exception.message_dict)

        # Petición origen fuera del par
        with self.assertRaises(ValidationError) as ctx_origen:
            PeticionPosibleDuplicado(
                peticion_menor_id=5,
                peticion_mayor_id=10,
                peticion_origen_id=99,
            ).clean()
        self.assertIn('peticion_origen', ctx_origen.exception.message_dict)

    def test_migracion_0005_integra_solo_nuevo_modelo_y_sin_alterar_tablas_existentes(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        loader.graph.ensure_not_cyclic()

        mig = loader.get_migration('peticiones', '0005_peticionposibleduplicado')
        self.assertEqual(
            set(mig.dependencies),
            {
                ('peticiones', '0004_renombrar_y_agregar_tests_seguimiento_visita'),
                ('users', '0011_sede_usuarios_id_sede'),
            },
        )
        self.assertEqual(len(mig.operations), 1)
        self.assertEqual(mig.operations[0].__class__.__name__, 'CreateModel')
        self.assertEqual(mig.operations[0].name, 'PeticionPosibleDuplicado')

        # Sin cambios pendientes para PeticionPosibleDuplicado en el autodetector
        autodetector = MigrationAutodetector(
            loader.project_state(),
            ProjectState.from_apps(apps),
            NonInteractiveMigrationQuestioner(dry_run=True),
        )
        changes = autodetector.changes(graph=loader.graph)
        operaciones_pendientes = [
            (type(op).__name__, getattr(op, 'name', ''))
            for migs in changes.values()
            for m in migs
            for op in m.operations
        ]
        self.assertNotIn(('CreateModel', 'PeticionPosibleDuplicado'), operaciones_pendientes)

        # Compilación DDL offline en PostgreSQL sin tocar Supabase ni tablas existentes
        estado_previo = loader.project_state(
            ('peticiones', '0004_renombrar_y_agregar_tests_seguimiento_visita')
        )
        nuevo_estado = estado_previo.clone()
        pg_conn = connections['default']
        schema_editor = pg_conn.schema_editor(collect_sql=True, atomic=False)
        with schema_editor:
            mig.apply(nuevo_estado, schema_editor)

        sql_completo = '\n'.join(schema_editor.collected_sql)
        self.assertIn('CREATE TABLE "peticion_posible_duplicado"', sql_completo)
        self.assertIn('CONSTRAINT "chk_pet_dup_par_ordenado"', sql_completo)
        self.assertIn('CONSTRAINT "uq_pet_dup_par"', sql_completo)
        self.assertIn('CONSTRAINT "chk_pet_dup_estado_valido"', sql_completo)
        self.assertIn('CONSTRAINT "chk_pet_dup_principal_par"', sql_completo)
        self.assertIn('CONSTRAINT "chk_pet_dup_origen_par"', sql_completo)
        self.assertNotIn('ON DELETE CASCADE', sql_completo.upper())
        self.assertNotIn('ALTER TABLE "peticiones"', sql_completo)
        self.assertNotIn('ALTER TABLE "usuarios"', sql_completo)
        self.assertTrue(mig.operations[0].reversible)


class PeticionPosibleDuplicadoRestriccionesSQLTest(SimpleTestCase):
    """Ejecuta DDL compilado de Django en una BD SQLite en memoria 100% aislada."""

    def _crear_conexion_aislada(self):
        conn = SqliteDatabaseWrapper(
            {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
                'TIME_ZONE': None,
                'CONN_HEALTH_CHECKS': False,
                'CONN_MAX_AGE': 0,
                'OPTIONS': {},
                'AUTOCOMMIT': True,
            },
            alias='sqlite_aislado_alertas',
        )
        with conn.cursor() as cur:
            cur.execute('PRAGMA foreign_keys = ON')
            cur.execute(
                'CREATE TABLE "peticiones" ("id_peticion" INTEGER PRIMARY KEY AUTOINCREMENT)'
            )
            cur.execute(
                'CREATE TABLE "usuarios" ("id_usuario" INTEGER PRIMARY KEY AUTOINCREMENT)'
            )
            cur.execute('INSERT INTO "peticiones" ("id_peticion") VALUES (5), (10), (99)')
            cur.execute('INSERT INTO "usuarios" ("id_usuario") VALUES (1)')

        schema_editor = conn.schema_editor(atomic=False)
        tabla_sql, params = schema_editor.table_sql(PeticionPosibleDuplicado)
        with conn.cursor() as cur:
            cur.execute(tabla_sql, params)
            for idx in PeticionPosibleDuplicado._meta.indexes:
                cur.execute(str(idx.create_sql(PeticionPosibleDuplicado, schema_editor)))
        return conn

    def _insertar_alerta_sql(
        self,
        conn,
        id_menor,
        id_mayor,
        *,
        id_origen=None,
        estado='PENDIENTE',
        id_principal=None,
        revisado_por_id=None,
    ):
        sql = (
            'INSERT INTO "peticion_posible_duplicado" ('
            '"id_peticion_menor", "id_peticion_mayor", "id_peticion_origen", '
            '"estado_revision", "id_peticion_principal", '
            '"similitud_texto", "coincidencia_texto", "distancia_metros", "coincidencia_gps", '
            '"distancia_hamming", "similitud_imagen", "coincidencia_imagen", '
            '"score_combinado", "criterios_coincidencia", "origen_deteccion", '
            '"revisado_por_id", "observacion_revision", "fecha_revision", '
            '"fecha_creacion", "fecha_actualizacion"'
            ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
        )
        params = (
            id_menor,
            id_mayor,
            id_origen,
            estado,
            id_principal,
            0.78,
            1,
            45.5,
            1,
            4,
            0.9375,
            1,
            0.85,
            '["texto", "gps", "imagen"]',
            PeticionPosibleDuplicado.ORIGEN_ENDPOINT_MANUAL,
            revisado_por_id,
            None,
            None,
            '2026-11-12 22:00:00',
            '2026-11-12 22:00:00',
        )
        with conn.cursor() as cur:
            cur.execute(sql, params)

    def test_restricciones_check_unique_y_proteccion_fk_en_sql_real(self):
        conn = self._crear_conexion_aislada()
        try:
            # 1. Rechaza autorreferencia (CHECK chk_pet_dup_par_ordenado)
            with self.assertRaises(IntegrityError):
                self._insertar_alerta_sql(conn, 5, 5)

            # 2. Rechaza par desordenado mayor antes que menor (CHECK chk_pet_dup_par_ordenado)
            with self.assertRaises(IntegrityError):
                self._insertar_alerta_sql(conn, 10, 5)

            # 3. Rechaza estado de revisión inválido (CHECK chk_pet_dup_estado_valido)
            with self.assertRaises(IntegrityError):
                self._insertar_alerta_sql(conn, 5, 10, estado='ELIMINADO')

            # 4. Rechaza peticion_principal ajena al par (CHECK chk_pet_dup_principal_par)
            with self.assertRaises(IntegrityError):
                self._insertar_alerta_sql(conn, 5, 10, id_principal=99)

            # 5. Rechaza peticion_origen ajena al par (CHECK chk_pet_dup_origen_par)
            with self.assertRaises(IntegrityError):
                self._insertar_alerta_sql(conn, 5, 10, id_origen=99)

            # 6. Inserción válida con par ordenado
            self._insertar_alerta_sql(conn, 5, 10, id_origen=10)

            # 7. Rechaza duplicado para el mismo par ordenado (UNIQUE uq_pet_dup_par)
            with self.assertRaises(IntegrityError):
                self._insertar_alerta_sql(conn, 5, 10, id_origen=5)

            # 8. Permite registrar decisión humana conservando ambas peticiones
            with conn.cursor() as cur:
                cur.execute(
                    'UPDATE "peticion_posible_duplicado" '
                    'SET "estado_revision" = ?, "id_peticion_principal" = ?, '
                    '"revisado_por_id" = ?, "observacion_revision" = ?, "fecha_revision" = ? '
                    'WHERE "id_peticion_menor" = ? AND "id_peticion_mayor" = ?',
                    (
                        PeticionPosibleDuplicado.ESTADO_CONFIRMADO,
                        5,
                        1,
                        'Mismo canino reportado por dos vecinos.',
                        '2026-11-12 22:05:00',
                        5,
                        10,
                    ),
                )

            # 9. Impide eliminar peticiones referenciadas (sin cascada destructiva)
            with self.assertRaises(IntegrityError):
                with conn.cursor() as cur:
                    cur.execute('DELETE FROM "peticiones" WHERE "id_peticion" = 5')

            # 10. Eliminar la alerta tampoco elimina ninguna petición original
            with conn.cursor() as cur:
                cur.execute('DELETE FROM "peticion_posible_duplicado"')
                cur.execute('SELECT "id_peticion" FROM "peticiones" ORDER BY "id_peticion"')
                ids_restantes = [row[0] for row in cur.fetchall()]
            self.assertEqual(ids_restantes, [5, 10, 99])

            # 11. Revertir la tabla de alertas conserva intactas las tablas existentes
            with conn.schema_editor(atomic=False) as schema_drop:
                schema_drop.delete_model(PeticionPosibleDuplicado)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='peticion_posible_duplicado'"
                )
                self.assertEqual(cur.fetchall(), [])
                cur.execute('SELECT "id_peticion" FROM "peticiones" ORDER BY "id_peticion"')
                self.assertEqual([row[0] for row in cur.fetchall()], [5, 10, 99])
        finally:
            conn.close()
