"""
Script para inicializar la base de datos plantilla de pruebas: template_vetericano_test.
Proyecto VETERICANO - CBA Popayan.

Sigue estrictamente el diseno plan/DISENO_TAREA10.md seccion 2.2 y las reglas de seguridad:
- Valida que el host sea estrictamente local (127.0.0.1, localhost, ::1).
- Valida que el nombre de la base a crear/borrar sea template_vetericano_test.
- Genera plan/schema_public_clean.sql a partir de plan/schema_public.sql.
- Conecta a la base administrativa local 'postgres' para terminar conexiones y recrear template_vetericano_test.
- Ejecuta psql con ON_ERROR_STOP=1 y -f para cargar schema_public_clean.sql.
- Conecta a template_vetericano_test para sembrar los catalogos base (roles 1..4, tipo_peticion, estado_peticiones, especies, raza)
  y sincronizar secuencias dinamicamente mediante pg_get_serial_sequence.
- Marca template_vetericano_test como plantilla (is_template = true).
- NUNCA imprime contrasenas ni datos sensibles.
"""

import os
import sys
import subprocess
from pathlib import Path

# TEMPORAL: retirar cuando Antigravity herede las variables
if sys.platform == 'win32':
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as rk:
            for k in ['TEST_DB_HOST', 'TEST_DB_PORT', 'TEST_DB_USER', 'TEST_DB_PASSWORD', 'TEST_DB_TEMPLATE', 'TEST_DB_NAME']:
                if k not in os.environ:
                    try:
                        val, _ = winreg.QueryValueEx(rk, k)
                        os.environ[k] = str(val)
                    except OSError:
                        pass
    except Exception:
        pass

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

BASE_DIR = Path(__file__).resolve().parent.parent  # BackendVetericano
PLAN_DIR = BASE_DIR.parent / 'plan'                 # Workspace/plan

ALLOWED_HOSTS = {'127.0.0.1', 'localhost', '::1'}
ALLOWED_DBS = {'template_vetericano_test'}


def sanitizar_esquema(archivo_origen: Path, archivo_destino: Path):
    """
    Lee schema_public.sql y genera schema_public_clean.sql:
    - Remueve directivas psql \\restrict y \\unrestrict.
    - Sustituye CREATE SCHEMA public; por CREATE SCHEMA IF NOT EXISTS public;.
    - Conserva SET transaction_timeout = 0;.
    """
    if not archivo_origen.exists():
        raise FileNotFoundError(f"No se encontro el archivo de esquema: {archivo_origen}")

    print(f"[INFO] Leyendo esquema original desde: {archivo_origen}")
    with open(archivo_origen, 'r', encoding='utf-8') as f:
        lineas = f.readlines()

    lineas_limpias = []
    lineas_omitidas = 0
    for linea in lineas:
        stripped = linea.strip()
        if stripped.startswith(r'\restrict') or stripped.startswith(r'\unrestrict'):
            lineas_omitidas += 1
            continue
        if stripped == 'CREATE SCHEMA public;':
            lineas_limpias.append('CREATE SCHEMA IF NOT EXISTS public;\n')
            continue
        lineas_limpias.append(linea)

    print(f"[INFO] Omitidas {lineas_omitidas} lineas de directivas restrict/unrestrict.")
    with open(archivo_destino, 'w', encoding='utf-8') as f:
        f.writelines(lineas_limpias)

    print(f"[INFO] Esquema limpio generado exitosamente en: {archivo_destino}")


def verificar_seguridad(host: str, dbname: str):
    """Guarda fail-closed: verifica host local y nombre de base permitido antes de cualquier operacion DDL."""
    if host.lower() not in ALLOWED_HOSTS:
        raise RuntimeError(
            f"BLOQUEO DE SEGURIDAD: Host '{host}' no permitido. Las pruebas solo pueden correr en localhost/127.0.0.1/::1."
        )
    if dbname.lower() not in ALLOWED_DBS:
        raise RuntimeError(
            f"BLOQUEO DE SEGURIDAD: Base de datos '{dbname}' no permitida. Solo se permite template_vetericano_test."
        )


def preparar_base_plantilla():
    host = os.getenv('TEST_DB_HOST', '127.0.0.1')
    port = int(os.getenv('TEST_DB_PORT', '5432'))
    user = os.getenv('TEST_DB_USER', 'postgres')
    password = os.getenv('TEST_DB_PASSWORD', 'postgres')
    template_db = os.getenv('TEST_DB_TEMPLATE', 'template_vetericano_test')

    verificar_seguridad(host, template_db)

    schema_sql = PLAN_DIR / 'schema_public.sql'
    clean_sql = PLAN_DIR / 'schema_public_clean.sql'

    sanitizar_esquema(schema_sql, clean_sql)

    print(f"[INFO] Conectando a motor PostgreSQL local administrativo en {host}:{port}...")
    conn_admin = psycopg2.connect(
        dbname='postgres',
        user=user,
        password=password,
        host=host,
        port=port
    )
    conn_admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

    try:
        with conn_admin.cursor() as cur:
            # 1. Terminar conexiones activas hacia la plantilla
            cur.execute("""
                SELECT pg_terminate_backend(pid) 
                FROM pg_stat_activity 
                WHERE datname = %s AND pid <> pg_backend_pid();
            """, (template_db,))

            # 2. Verificar si la base existe y desmarcar is_template si aplica
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (template_db,))
            if cur.fetchone():
                print(f"[INFO] Desmarcando is_template en {template_db} para permitir reemplazo...")
                cur.execute(f"ALTER DATABASE {template_db} WITH is_template = false;")

            # 3. Eliminar y crear la base limpia (protegido por verificacion previa de nombre y host)
            verificar_seguridad(host, template_db)
            print(f"[INFO] Recreando base de datos {template_db}...")
            cur.execute(f"DROP DATABASE IF EXISTS {template_db} WITH (FORCE);")
            cur.execute(f"CREATE DATABASE {template_db};")
    finally:
        conn_admin.close()

    # 4. Cargar esquema limpio usando psql
    print(f"[INFO] Cargando schema_public_clean.sql en {template_db} mediante psql...")
    env_psql = os.environ.copy()
    env_psql['PGPASSWORD'] = password
    cmd = [
        'psql',
        '-v', 'ON_ERROR_STOP=1',
        '-h', host,
        '-p', str(port),
        '-U', user,
        '-d', template_db,
        '-f', str(clean_sql)
    ]
    res = subprocess.run(cmd, env=env_psql, capture_output=True, text=True)
    if res.returncode != 0:
        err_msg = res.stderr or res.stdout
        raise RuntimeError(f"Fallo al ejecutar psql para cargar el esquema:\n{err_msg}")
    print("[INFO] DDL de schema_public_clean.sql cargado exitosamente.")

    # 5. Conectar a template_db para sembrar catalogos base y sincronizar secuencias
    print(f"[INFO] Sembrando catalogos base y sincronizando secuencias en {template_db}...")
    conn_template = psycopg2.connect(
        dbname=template_db,
        user=user,
        password=password,
        host=host,
        port=port
    )
    try:
        with conn_template.cursor() as cur:
            # 5.1 rol
            roles = [
                (1, 'Administrador'),
                (2, 'Jurídico'),
                (3, 'Veterinario'),
                (4, 'Peticionario'),
            ]
            for r_id, r_name in roles:
                cur.execute(
                    "INSERT INTO public.rol (id_rol, nombre_rol) VALUES (%s, %s) "
                    "ON CONFLICT (id_rol) DO UPDATE SET nombre_rol = EXCLUDED.nombre_rol;",
                    (r_id, r_name)
                )
            cur.execute("SELECT setval(pg_get_serial_sequence('rol', 'id_rol'), 4);")

            # 5.2 tipo_peticion
            cur.execute(
                "INSERT INTO public.tipo_peticion (id_tipo, nombre, activo) VALUES (%s, %s, %s) "
                "ON CONFLICT (id_tipo) DO UPDATE SET nombre = EXCLUDED.nombre, activo = EXCLUDED.activo;",
                (1, 'Atención Animal Campo', True)
            )
            cur.execute("SELECT setval(pg_get_serial_sequence('tipo_peticion', 'id_tipo'), 1);")

            # 5.3 estado_peticiones
            estados = [
                (1, 'En Proceso'),
                (2, 'Atendida'),
            ]
            for e_id, e_name in estados:
                cur.execute(
                    "INSERT INTO public.estado_peticiones (id_estado, nombre, activo) VALUES (%s, %s, %s) "
                    "ON CONFLICT (id_estado) DO UPDATE SET nombre = EXCLUDED.nombre, activo = EXCLUDED.activo;",
                    (e_id, e_name, True)
                )
            cur.execute("SELECT setval(pg_get_serial_sequence('estado_peticiones', 'id_estado'), 2);")

            # 5.4 especies y raza
            cur.execute(
                "INSERT INTO public.especies (id_especie, nombre, activo) VALUES (%s, %s, %s) "
                "ON CONFLICT (id_especie) DO UPDATE SET nombre = EXCLUDED.nombre, activo = EXCLUDED.activo;",
                (1, 'Canino', True)
            )
            cur.execute("SELECT setval(pg_get_serial_sequence('especies', 'id_especie'), 1);")

            cur.execute(
                "INSERT INTO public.raza (id_raza, id_especie, nombre, activo) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id_raza) DO UPDATE SET nombre = EXCLUDED.nombre, activo = EXCLUDED.activo;",
                (1, 1, 'Mestizo Canino', True)
            )
            cur.execute("SELECT setval(pg_get_serial_sequence('raza', 'id_raza'), 1);")

            conn_template.commit()
            print("[INFO] Catalogos base sembrados y secuencias sincronizadas exitosamente.")
    finally:
        conn_template.close()

    # 6. Marcar base de datos como plantilla
    conn_admin = psycopg2.connect(
        dbname='postgres',
        user=user,
        password=password,
        host=host,
        port=port
    )
    conn_admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn_admin.cursor() as cur:
            cur.execute("""
                SELECT pg_terminate_backend(pid) 
                FROM pg_stat_activity 
                WHERE datname = %s AND pid <> pg_backend_pid();
            """, (template_db,))
            print(f"[INFO] Marcando {template_db} con is_template = true...")
            cur.execute(f"ALTER DATABASE {template_db} WITH is_template = true;")
            print(f"[EXITO] Base de datos plantilla {template_db} lista para su clonacion instantanea en pruebas.")
    finally:
        conn_admin.close()


if __name__ == '__main__':
    try:
        preparar_base_plantilla()
    except Exception as exc:
        print(f"[ERROR] Fallo la preparacion de la plantilla: {exc}", file=sys.stderr)
        sys.exit(1)
