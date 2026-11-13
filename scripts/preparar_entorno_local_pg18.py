"""
Prepara en PostgreSQL local (127.0.0.1:5433) una base completamente aislada
para probar la reparacion de 0002, la migracion 0005, concurrencia, integridad y RLS.

Reglas de seguridad:
- Solo opera contra hosts locales (127.0.0.1, localhost, ::1).
- Nunca modifica Supabase (solo lee django_migrations en BEGIN READ ONLY).
- Nunca copia datos personales (usuarios, peticiones, historiales reales).
- Nunca borra bases existentes ni ejecuta DROP/TRUNCATE sobre bases previas:
  si alguna base objetivo ya existe, aborta inmediatamente.
- Nunca imprime contrasenas ni cadenas de conexion.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

if sys.platform == 'win32':
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as rk:
            for k in (
                'TEST_DB_HOST',
                'TEST_DB_PORT',
                'TEST_DB_USER',
                'TEST_DB_PASSWORD',
                'TEST_DB_TEMPLATE',
                'TEST_DB_NAME',
            ):
                if k not in os.environ:
                    try:
                        val, _ = winreg.QueryValueEx(rk, k)
                        os.environ[k] = str(val)
                    except OSError:
                        pass
    except Exception:
        pass

import dj_database_url
import psycopg2
from dotenv import load_dotenv
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

BASE_DIR = Path(__file__).resolve().parent.parent
SQL_DIR = BASE_DIR / 'scripts' / 'sql'
SCHEMA_SQL = SQL_DIR / 'schema_public_estructura_sin_datos.sql'
PSQL_EXE = Path(r'C:\Program Files\PostgreSQL\18\bin\psql.exe')

ALLOWED_HOSTS = {'127.0.0.1', 'localhost', '::1'}
TARGET_VERIFY_DB = 'vetericano_mig_verify'
TARGET_TEMPLATE_DB = os.getenv('TEST_DB_TEMPLATE', 'template_vetericano_test')
TARGET_TEST_DB = os.getenv('TEST_DB_NAME', 'test_vetericano')


def _validar_entorno_local() -> tuple[str, int, str, str]:
    host = os.getenv('TEST_DB_HOST', '127.0.0.1')
    port = int(os.getenv('TEST_DB_PORT', '5433'))
    user = os.getenv('TEST_DB_USER', 'postgres')
    password = os.getenv('TEST_DB_PASSWORD', '')

    if host.lower() not in ALLOWED_HOSTS:
        raise RuntimeError(f"BLOQUEO DE SEGURIDAD: host local no permitido: {host}")
    if not password:
        raise RuntimeError("Falta TEST_DB_PASSWORD en el entorno.")
    if not SCHEMA_SQL.exists():
        raise FileNotFoundError(f"No existe el esquema sin datos: {SCHEMA_SQL}")
    if not PSQL_EXE.exists():
        raise FileNotFoundError(f"No se encontro psql.exe en: {PSQL_EXE}")
    return host, port, user, password


def _leer_metadata_migraciones_supabase() -> list[tuple[int, str, str, object]]:
    """Lee UNICAMENTE public.django_migrations desde Supabase en modo BEGIN READ ONLY (cero PII)."""
    load_dotenv(BASE_DIR / '.env')
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError("DATABASE_URL no disponible para lectura de metadata django_migrations.")
    parsed = dj_database_url.parse(db_url, conn_max_age=0, ssl_require=True)
    conn = psycopg2.connect(
        dbname=parsed['NAME'],
        user=parsed['USER'],
        password=parsed['PASSWORD'],
        host=parsed['HOST'],
        port=parsed['PORT'],
        sslmode='require',
    )
    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN READ ONLY;")
            try:
                cur.execute(
                    "SELECT id, app, name, applied FROM public.django_migrations ORDER BY id;"
                )
                return list(cur.fetchall())
            finally:
                cur.execute("ROLLBACK;")
    finally:
        conn.close()


def _cargar_schema_en_bd(host: str, port: int, user: str, password: str, dbname: str) -> None:
    env = os.environ.copy()
    env['PGPASSWORD'] = password
    res = subprocess.run(
        [
            str(PSQL_EXE),
            '-v', 'ON_ERROR_STOP=1',
            '-h', host,
            '-p', str(port),
            '-U', user,
            '-d', dbname,
            '-f', str(SCHEMA_SQL),
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    if res.returncode != 0:
        raise RuntimeError(f"Error cargando esquema en {dbname}:\n{res.stderr or res.stdout}")


def _asegurar_roles_prueba_supabase(conn_local) -> None:
    """Crea roles locales NOLOGIN equivalentes a Supabase si no existen."""
    with conn_local.cursor() as cur:
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    CREATE ROLE anon NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    CREATE ROLE authenticated NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
                    CREATE ROLE service_role NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
                END IF;
            END $$;
        """)


def main() -> None:
    host, port, user, password = _validar_entorno_local()

    conn_admin = psycopg2.connect(
        dbname='postgres', user=user, password=password, host=host, port=port
    )
    conn_admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn_admin.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database ORDER BY datname;")
            existentes = {row[0] for row in cur.fetchall()}
            conflictos = [
                db for db in (TARGET_VERIFY_DB, TARGET_TEMPLATE_DB, TARGET_TEST_DB)
                if db in existentes
            ]
            if conflictos:
                raise RuntimeError(
                    "CONFLICTO DETECTADO: Las siguientes bases ya existen en PostgreSQL local "
                    f"y no se borraran automaticamente: {conflictos}"
                )

            _asegurar_roles_prueba_supabase(conn_admin)
            print(f"[INFO] Creando base aislada {TARGET_VERIFY_DB}...")
            cur.execute(f'CREATE DATABASE "{TARGET_VERIFY_DB}";')
            print(f"[INFO] Creando base plantilla aislada {TARGET_TEMPLATE_DB}...")
            cur.execute(f'CREATE DATABASE "{TARGET_TEMPLATE_DB}";')
    finally:
        conn_admin.close()

    # Cargar esquema sin datos en ambas bases aisladas
    _cargar_schema_en_bd(host, port, user, password, TARGET_VERIFY_DB)
    _cargar_schema_en_bd(host, port, user, password, TARGET_TEMPLATE_DB)
    print("[INFO] Esquema schema-only (sin datos personales) cargado en ambas bases.")

    # Leer unicamente la tabla tecnica django_migrations en solo lectura desde Supabase
    mig_rows = _leer_metadata_migraciones_supabase()
    print(f"[INFO] Leidas {len(mig_rows)} filas tecnicas de django_migrations (sin PII).")

    # Poblar django_migrations en ambas bases + DEFAULT_ACL simulado + fixtures sinteticos en verify
    for dbname in (TARGET_VERIFY_DB, TARGET_TEMPLATE_DB):
        conn = psycopg2.connect(
            dbname=dbname, user=user, password=password, host=host, port=port
        )
        try:
            with conn.cursor() as cur:
                # Simular DEFAULT_ACL de Supabase public para demostrar efectividad real de RLS/REVOKE
                cur.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;")
                cur.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT ALL ON TABLES TO anon, authenticated, service_role;"
                )
                cur.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;"
                )
                for mig_id, app, name, applied in mig_rows:
                    cur.execute(
                        "INSERT INTO public.django_migrations (id, app, name, applied) VALUES (%s, %s, %s, %s);",
                        (mig_id, app, name, applied),
                    )
                cur.execute(
                    "SELECT setval(pg_get_serial_sequence('public.django_migrations', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM public.django_migrations), 1));"
                )
            conn.commit()
        finally:
            conn.close()

    # En TARGET_VERIFY_DB sembrar unicamente datos sinteticos de prueba (preexistentes simulados)
    conn_v = psycopg2.connect(
        dbname=TARGET_VERIFY_DB, user=user, password=password, host=host, port=port
    )
    try:
        with conn_v.cursor() as cur:
            cur.execute(
                "INSERT INTO public.rol (id_rol, nombre_rol) VALUES "
                "(1, 'Administrador'), (2, 'Jurídico'), (3, 'Veterinario'), (4, 'Peticionario'), (5, 'super_administrador');"
            )
            cur.execute("SELECT setval(pg_get_serial_sequence('public.rol', 'id_rol'), 5);")
            cur.execute(
                "INSERT INTO public.sedes (id_sede, nombre, direccion, ciudad, telefono, codigo, activo) "
                "VALUES (1, 'Sede Prueba Local', 'Calle Falsa 123', 'Popayan', '3000000000', 'LOC1', true);"
            )
            cur.execute("SELECT setval(pg_get_serial_sequence('public.sedes', 'id_sede'), 1);")
            cur.execute(
                "INSERT INTO public.usuarios (id_usuario, email, password, nombre, apellido, id_rol, activo, id_sede) VALUES "
                "(1, 'peticionario.sintetico@local.test', 'pbkdf2_sha256$1$dummy$dummy', 'Ciudadano', 'Sintetico', 4, true, 1),"
                "(2, 'juridico.sintetico@local.test', 'pbkdf2_sha256$1$dummy$dummy', 'Juridico', 'Sintetico', 2, true, 1),"
                "(3, 'vet1.sintetico@local.test', 'pbkdf2_sha256$1$dummy$dummy', 'Veterinario', 'Uno', 3, true, 1),"
                "(4, 'vet2.sintetico@local.test', 'pbkdf2_sha256$1$dummy$dummy', 'Veterinario', 'Dos', 3, true, 1);"
            )
            cur.execute("SELECT setval(pg_get_serial_sequence('public.usuarios', 'id_usuario'), 4);")
            cur.execute("INSERT INTO public.tipo_peticion (id_tipo, nombre, activo) VALUES (1, 'Atención Animal Campo', true);")
            cur.execute("SELECT setval(pg_get_serial_sequence('public.tipo_peticion', 'id_tipo'), 1);")
            cur.execute("INSERT INTO public.estado_peticiones (id_estado, nombre, activo) VALUES (1, 'En Proceso', true), (2, 'Atendida', true);")
            cur.execute("SELECT setval(pg_get_serial_sequence('public.estado_peticiones', 'id_estado'), 2);")
            cur.execute(
                "INSERT INTO public.ubicaciones (id_ubicacion, direccion, latitud, longitud) VALUES "
                "(1, 'Parque Sintetico 1', 2.441900, -76.606300),"
                "(2, 'Parque Sintetico 2', 2.441950, -76.606350);"
            )
            cur.execute("SELECT setval(pg_get_serial_sequence('public.ubicaciones', 'id_ubicacion'), 2);")
            cur.execute(
                "INSERT INTO public.peticiones (id_peticion, id_tipo, id_ubicacion, id_estado, responsable_id, descripcion, prioridad, numero_radicado, asignado_a_id, asignado_por_id) VALUES "
                "(10, 1, 1, 1, 1, 'Canino abandonado cerca al parque sintetico norte', 'ALTA', 'RAD-LOC-0010', 3, 2),"
                "(11, 1, 2, 1, 1, 'Perro abandonado en el parque sintetico norte', 'MEDIA', 'RAD-LOC-0011', NULL, NULL),"
                "(12, 1, 2, 2, 1, 'Felino atendido de control sintetico', 'BAJA', 'RAD-LOC-0012', 4, 2);"
            )
            cur.execute("SELECT setval(pg_get_serial_sequence('public.peticiones', 'id_peticion'), 12);")
            cur.execute(
                "INSERT INTO public.notificaciones_peticiones (id_peticion, id_usuario_destino, tipo, titulo, mensaje, leida) "
                "VALUES (10, 2, 'NUEVA_PETICION', 'Preexistente Sintetica', 'Notificacion preexistente a conservar', false);"
            )
            cur.execute(
                "INSERT INTO public.historial_peticiones (id_peticion, tipo_cambio, id_estado_nuevo, id_usuario_accion, observacion) "
                "VALUES (10, 'CREACION', 1, 1, 'Historial preexistente sintetico');"
            )
        conn_v.commit()
        print("[EXITO] Datos sinteticos preexistentes sembrados en vetericano_mig_verify.")
    finally:
        conn_v.close()


if __name__ == '__main__':
    main()
