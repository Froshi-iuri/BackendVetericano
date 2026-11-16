# Generated manually to restrict schema changes strictly to PeticionPosibleDuplicado
# DO NOT execute migrate without explicit authorization.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('peticiones', '0007_peticionimagenembedding_pgvector'),
    ]

    operations = [
        migrations.AddField(
            model_name='peticionposibleduplicado',
            name='metodo_imagen',
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
        migrations.AddField(
            model_name='peticionposibleduplicado',
            name='peticion_duplicada',
            field=models.ForeignKey(
                blank=True,
                db_column='id_peticion_duplicada',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='duplicados_confirmados_como_duplicada',
                to='peticiones.peticiones',
            ),
        ),
        migrations.AddField(
            model_name='peticionposibleduplicado',
            name='porcentaje_proximidad_gps',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='peticionposibleduplicado',
            name='porcentaje_similitud_visual',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='peticionposibleduplicado',
            name='radio_gps_metros',
            field=models.FloatField(blank=True, default=200.0, null=True),
        ),
        migrations.AddField(
            model_name='peticionposibleduplicado',
            name='similitud_visual_coseno',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='peticionposibleduplicado',
            index=models.Index(
                fields=['peticion_duplicada', 'estado_revision'],
                name='idx_pet_dup_duplicada_est',
            ),
        ),
        migrations.AddConstraint(
            model_name='peticionposibleduplicado',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ('peticion_duplicada__isnull', True),
                    ('peticion_duplicada', models.F('peticion_menor')),
                    ('peticion_duplicada', models.F('peticion_mayor')),
                    _connector='OR',
                ),
                name='chk_pet_dup_duplicada_par',
            ),
        ),
        migrations.AddConstraint(
            model_name='peticionposibleduplicado',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ('peticion_principal__isnull', True),
                    ('peticion_duplicada__isnull', True),
                    ('peticion_principal__lt', models.F('peticion_duplicada')),
                    ('peticion_principal__gt', models.F('peticion_duplicada')),
                    _connector='OR',
                ),
                name='chk_pet_dup_principal_distinta_duplicada',
            ),
        ),
    ]
