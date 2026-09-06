# AnaC
from django.db import migrations

def agregar_columna_identificacion(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS identificacion VARCHAR(20);")

class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_usuarios_id_rol_alter_usuarios_activo_and_more'),
    ]

    operations = [
        migrations.RunPython(agregar_columna_identificacion),
    ]