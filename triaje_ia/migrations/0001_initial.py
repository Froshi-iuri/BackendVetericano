from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('peticiones', '0008_peticionposibleduplicado_resolucion_dinov2'),
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='TriajePeticion',
            fields=[
                ('id_triaje', models.BigAutoField(primary_key=True, serialize=False)),
                ('clave_idempotencia', models.CharField(max_length=120, unique=True)),
                (
                    'estado_envio',
                    models.CharField(
                        choices=[
                            ('PENDIENTE', 'Pendiente de envío'),
                            ('ENVIADO', 'Enviado a n8n'),
                            ('FALLO_TEMPORAL', 'Fallo temporal (reintento pendiente)'),
                            ('FALLO_DEFINITIVO', 'Fallo definitivo (reintentos agotados)'),
                            ('OMITIDO', 'Omitido'),
                        ],
                        default='PENDIENTE',
                        max_length=25,
                    ),
                ),
                (
                    'estado_analisis',
                    models.CharField(
                        choices=[
                            ('PENDIENTE', 'Pendiente'),
                            ('EN_PROCESO', 'En proceso'),
                            ('COMPLETADO', 'Completado'),
                            ('PARCIAL', 'Completado parcialmente (degradado)'),
                            ('FALLIDO', 'Fallido'),
                        ],
                        default='PENDIENTE',
                        max_length=25,
                    ),
                ),
                ('intentos_envio', models.PositiveSmallIntegerField(default=0)),
                ('max_intentos', models.PositiveSmallIntegerField(default=3)),
                ('proximo_reintento_en', models.DateTimeField(blank=True, null=True)),
                ('ultimo_codigo_http', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('ultimo_error_envio', models.TextField(blank=True, default='')),
                ('enviado_en', models.DateTimeField(blank=True, null=True)),
                ('completado_en', models.DateTimeField(blank=True, null=True)),
                ('n8n_execution_id', models.CharField(blank=True, default='', max_length=120)),
                ('prioridad_original', models.CharField(blank=True, max_length=20, null=True)),
                (
                    'prioridad_sugerida',
                    models.CharField(
                        blank=True,
                        choices=[
                            ('URGENTE', 'Urgente'),
                            ('ALTA', 'Alta'),
                            ('MEDIA', 'Media'),
                            ('BAJA', 'Baja'),
                        ],
                        max_length=20,
                        null=True,
                    ),
                ),
                ('nivel_confianza', models.FloatField(blank=True, null=True)),
                ('justificacion', models.TextField(blank=True, default='')),
                ('resumen_texto', models.TextField(blank=True, default='')),
                ('resumen_vision', models.TextField(blank=True, default='')),
                ('signos_detectados', models.JSONField(blank=True, default=list)),
                ('posibles_duplicados', models.JSONField(blank=True, default=list)),
                ('total_duplicados_detectados', models.PositiveIntegerField(default=0)),
                ('justificacion_recomendacion', models.TextField(blank=True, default='')),
                ('carga_veterinarios_snapshot', models.JSONField(blank=True, default=list)),
                ('modelo_texto_usado', models.CharField(blank=True, default='', max_length=100)),
                ('modelo_vision_usado', models.CharField(blank=True, default='', max_length=100)),
                ('resultado_raw', models.JSONField(blank=True, default=dict)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('fecha_actualizacion', models.DateTimeField(auto_now=True)),
                (
                    'peticion',
                    models.OneToOneField(
                        db_column='id_peticion',
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='triaje_ia',
                        to='peticiones.peticiones',
                    ),
                ),
                (
                    'veterinario_recomendado',
                    models.ForeignKey(
                        blank=True,
                        db_column='id_veterinario_recomendado',
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='triajes_recomendados',
                        to='users.usuarios',
                    ),
                ),
            ],
            options={
                'db_table': 'triaje_peticion',
                'ordering': ['-fecha_creacion', '-id_triaje'],
                'managed': True,
            },
        ),
        migrations.AddIndex(
            model_name='triajepeticion',
            index=models.Index(fields=['estado_envio', 'proximo_reintento_en'], name='idx_triaje_envio_reint'),
        ),
        migrations.AddIndex(
            model_name='triajepeticion',
            index=models.Index(fields=['estado_analisis', '-fecha_creacion'], name='idx_triaje_analisis_fecha'),
        ),
    ]
