# Generated for Vetericano - Módulo Super Administrador: Gestión de Sedes

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0010_delete_administracionmedicamento_delete_animal_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        CREATE TABLE IF NOT EXISTS public.sedes (
                            id_sede SERIAL PRIMARY KEY,
                            nombre VARCHAR(150) NOT NULL UNIQUE,
                            direccion VARCHAR(255) NOT NULL,
                            ciudad VARCHAR(100) NOT NULL,
                            telefono VARCHAR(30) NOT NULL,
                            codigo VARCHAR(50) NULL UNIQUE,
                            activo BOOLEAN NOT NULL DEFAULT TRUE,
                            fecha_creacion TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE UNIQUE INDEX IF NOT EXISTS uq_sedes_nombre_lower
                        ON public.sedes (LOWER(TRIM(nombre)));

                        CREATE UNIQUE INDEX IF NOT EXISTS uq_sedes_codigo_lower
                        ON public.sedes (LOWER(TRIM(codigo)))
                        WHERE codigo IS NOT NULL;

                        ALTER TABLE public.sedes ENABLE ROW LEVEL SECURITY;

                        ALTER TABLE public.usuarios
                        ADD COLUMN IF NOT EXISTS id_sede INTEGER NULL
                        REFERENCES public.sedes(id_sede) ON DELETE SET NULL;

                        CREATE INDEX IF NOT EXISTS idx_usuarios_id_sede
                        ON public.usuarios (id_sede);

                        INSERT INTO public.rol (id_rol, nombre_rol)
                        SELECT 5, 'super_administrador'
                        WHERE NOT EXISTS (
                            SELECT 1 FROM public.rol
                            WHERE id_rol = 5
                               OR LOWER(TRIM(nombre_rol)) IN ('super_administrador', 'super administrador', 'superadministrador')
                        );

                        UPDATE public.rol
                        SET nombre_rol = 'super_administrador'
                        WHERE id_rol = 5;

                        SELECT setval(
                            pg_get_serial_sequence('public.rol', 'id_rol'),
                            GREATEST((SELECT COALESCE(MAX(id_rol), 1) FROM public.rol), 5)
                        );
                    """,
                    reverse_sql="""
                        DROP INDEX IF EXISTS public.idx_usuarios_id_sede;
                        ALTER TABLE public.usuarios DROP COLUMN IF EXISTS id_sede;
                        DROP INDEX IF EXISTS public.uq_sedes_codigo_lower;
                        DROP INDEX IF EXISTS public.uq_sedes_nombre_lower;
                        DROP TABLE IF EXISTS public.sedes;
                    """,
                ),
            ],
            state_operations=[
                migrations.CreateModel(
                    name='Sede',
                    fields=[
                        ('id_sede', models.AutoField(primary_key=True, serialize=False)),
                        ('nombre', models.CharField(max_length=150, unique=True)),
                        ('direccion', models.CharField(max_length=255)),
                        ('ciudad', models.CharField(max_length=100)),
                        ('telefono', models.CharField(max_length=30)),
                        ('codigo', models.CharField(blank=True, max_length=50, null=True, unique=True)),
                        ('activo', models.BooleanField(default=True)),
                        ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                    ],
                    options={
                        'db_table': 'sedes',
                        'managed': True,
                    },
                ),
                migrations.AddField(
                    model_name='usuarios',
                    name='id_sede',
                    field=models.ForeignKey(
                        blank=True,
                        db_column='id_sede',
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='usuarios',
                        to='users.sede',
                    ),
                ),
            ],
        ),
    ]
