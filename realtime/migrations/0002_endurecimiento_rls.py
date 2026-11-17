"""
Endurecimiento de las tablas nuevas desde su creación (patrón de scripts/sql/0003):
ENABLE ROW LEVEL SECURITY (sin FORCE: Django se conecta como owner `postgres` y no le aplica)
+ REVOKE ALL a PUBLIC/anon/authenticated en tabla y secuencia. No toca datos ni otras tablas.
Si los roles de Supabase no existen (PostgreSQL local), solo se revoca a PUBLIC.
"""
from django.db import migrations

TABLAS = ('notificaciones_sistema', 'dispositivos_fcm')

ENDURECER = """
DO $$
DECLARE
  t text;
  seq text;
  r text;
BEGIN
  FOREACH t IN ARRAY ARRAY['notificaciones_sistema','dispositivos_fcm'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC', t);
    seq := pg_get_serial_sequence(format('public.%I', t), CASE t WHEN 'notificaciones_sistema' THEN 'id_notificacion' ELSE 'id_dispositivo' END);
    IF seq IS NOT NULL THEN
      EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM PUBLIC', seq);
    END IF;
    FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM %I', t, r);
        IF seq IS NOT NULL THEN
          EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM %I', seq, r);
        END IF;
      END IF;
    END LOOP;
  END LOOP;
END $$;
"""

REVERTIR = """
ALTER TABLE public.notificaciones_sistema DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.dispositivos_fcm DISABLE ROW LEVEL SECURITY;
"""


def _solo_postgres(sql):
    def aplicar(apps, schema_editor):
        if schema_editor.connection.vendor == 'postgresql':
            # params=None: el SQL usa format('%I', ...) de PostgreSQL; con params=() psycopg interpretaría los
            # '%' como marcadores y fallaría (detectado al verificar la migración en PostgreSQL 17 real).
            schema_editor.execute(sql, params=None)
    return aplicar


class Migration(migrations.Migration):

    dependencies = [
        ('realtime', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(_solo_postgres(ENDURECER), _solo_postgres(REVERTIR)),
    ]
