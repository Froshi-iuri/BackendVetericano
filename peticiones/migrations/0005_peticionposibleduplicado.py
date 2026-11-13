# Generated manually in Fase 4A matching Django 6.1 autodetector output for PeticionPosibleDuplicado only.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('peticiones', '0004_renombrar_y_agregar_tests_seguimiento_visita'),
        ('users', '0011_sede_usuarios_id_sede'),
    ]

    operations = [
        migrations.CreateModel(
            name='PeticionPosibleDuplicado',
            fields=[
                ('id_alerta', models.AutoField(primary_key=True, serialize=False)),
                (
                    'estado_revision',
                    models.CharField(
                        choices=[
                            ('PENDIENTE', 'Pendiente de revisión'),
                            ('CONFIRMADO', 'Duplicado confirmado'),
                            ('DESCARTADO', 'Duplicado descartado'),
                        ],
                        default='PENDIENTE',
                        max_length=20,
                    ),
                ),
                ('similitud_texto', models.FloatField(blank=True, null=True)),
                ('coincidencia_texto', models.BooleanField(default=False)),
                ('distancia_metros', models.FloatField(blank=True, null=True)),
                ('coincidencia_gps', models.BooleanField(default=False)),
                ('distancia_hamming', models.SmallIntegerField(blank=True, null=True)),
                ('similitud_imagen', models.FloatField(blank=True, null=True)),
                ('coincidencia_imagen', models.BooleanField(default=False)),
                ('score_combinado', models.FloatField(default=0.0)),
                ('criterios_coincidencia', models.JSONField(blank=True, default=list)),
                (
                    'origen_deteccion',
                    models.CharField(
                        choices=[
                            ('REGISTRO_PETICION', 'Registro de petición'),
                            ('ENDPOINT_MANUAL', 'Consulta manual del detector'),
                            ('REANALISIS', 'Reanálisis posterior'),
                        ],
                        default='ENDPOINT_MANUAL',
                        max_length=40,
                    ),
                ),
                ('observacion_revision', models.TextField(blank=True, null=True)),
                ('fecha_revision', models.DateTimeField(blank=True, null=True)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('fecha_actualizacion', models.DateTimeField(auto_now=True)),
                (
                    'peticion_mayor',
                    models.ForeignKey(
                        db_column='id_peticion_mayor',
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='alertas_duplicado_como_mayor',
                        to='peticiones.peticiones',
                    ),
                ),
                (
                    'peticion_menor',
                    models.ForeignKey(
                        db_column='id_peticion_menor',
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='alertas_duplicado_como_menor',
                        to='peticiones.peticiones',
                    ),
                ),
                (
                    'peticion_origen',
                    models.ForeignKey(
                        blank=True,
                        db_column='id_peticion_origen',
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='alertas_duplicado_originadas',
                        to='peticiones.peticiones',
                    ),
                ),
                (
                    'peticion_principal',
                    models.ForeignKey(
                        blank=True,
                        db_column='id_peticion_principal',
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='duplicados_confirmados_como_principal',
                        to='peticiones.peticiones',
                    ),
                ),
                (
                    'revisado_por',
                    models.ForeignKey(
                        blank=True,
                        db_column='revisado_por_id',
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='revisiones_duplicados_realizadas',
                        to='users.usuarios',
                    ),
                ),
            ],
            options={
                'db_table': 'peticion_posible_duplicado',
                'ordering': ['-fecha_creacion', '-id_alerta'],
                'managed': True,
                'indexes': [
                    models.Index(
                        fields=['estado_revision', '-fecha_creacion'],
                        name='idx_pet_dup_est_fecha',
                    ),
                    models.Index(
                        fields=['peticion_menor', 'estado_revision'],
                        name='idx_pet_dup_menor_est',
                    ),
                    models.Index(
                        fields=['peticion_mayor', 'estado_revision'],
                        name='idx_pet_dup_mayor_est',
                    ),
                    models.Index(
                        fields=['peticion_principal', 'estado_revision'],
                        name='idx_pet_dup_princ_est',
                    ),
                ],
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(('peticion_menor__lt', models.F('peticion_mayor'))),
                        name='chk_pet_dup_par_ordenado',
                    ),
                    models.UniqueConstraint(
                        fields=('peticion_menor', 'peticion_mayor'),
                        name='uq_pet_dup_par',
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ('estado_revision__in', ['PENDIENTE', 'CONFIRMADO', 'DESCARTADO'])
                        ),
                        name='chk_pet_dup_estado_valido',
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ('peticion_principal__isnull', True),
                            ('peticion_principal', models.F('peticion_menor')),
                            ('peticion_principal', models.F('peticion_mayor')),
                            _connector='OR',
                        ),
                        name='chk_pet_dup_principal_par',
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ('peticion_origen__isnull', True),
                            ('peticion_origen', models.F('peticion_menor')),
                            ('peticion_origen', models.F('peticion_mayor')),
                            _connector='OR',
                        ),
                        name='chk_pet_dup_origen_par',
                    ),
                ],
            },
        ),
    ]
