from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('triaje_ia', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='triajepeticion',
            name='despacho_token',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='lease_expira_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='ultimo_intento_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='version_resultado',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='triajepeticion',
            name='estado_envio',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente de envío'),
                    ('ENVIANDO', 'Enviando a n8n (en curso)'),
                    ('ENVIADO', 'Enviado a n8n'),
                    ('FALLO_TEMPORAL', 'Fallo temporal (reintento pendiente)'),
                    ('FALLO_DEFINITIVO', 'Fallo definitivo (reintentos agotados)'),
                    ('OMITIDO', 'Omitido'),
                ],
                default='PENDIENTE',
                max_length=25,
            ),
        ),
        migrations.AddIndex(
            model_name='triajepeticion',
            index=models.Index(fields=['estado_envio', 'lease_expira_en'], name='idx_triaje_envio_lease'),
        ),
        migrations.AddIndex(
            model_name='triajepeticion',
            index=models.Index(fields=['estado_analisis', 'enviado_en'], name='idx_triaje_analisis_env'),
        ),
    ]
