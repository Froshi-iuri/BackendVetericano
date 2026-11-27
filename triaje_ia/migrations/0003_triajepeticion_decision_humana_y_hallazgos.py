from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('triaje_ia', '0002_triajepeticion_hardening'),
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='triajepeticion',
            name='signos_reportados',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='signos_fotografia',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='contradiccion_foto_descripcion',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='advertencias_contradiccion',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='decision_prioridad',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente de revisión'),
                    ('ACEPTADA', 'Aceptada'),
                    ('RECHAZADA', 'Rechazada'),
                    ('NO_APLICA', 'No aplica'),
                ],
                default='PENDIENTE',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='decision_prioridad_por',
            field=models.ForeignKey(
                blank=True,
                db_column='id_decision_prioridad_por',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='triajes_decision_prioridad',
                to='users.usuarios',
            ),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='decision_prioridad_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='decision_prioridad_observacion',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='prioridad_aplicada',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='decision_veterinario',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente de revisión'),
                    ('ACEPTADA', 'Aceptada'),
                    ('RECHAZADA', 'Rechazada'),
                    ('NO_APLICA', 'No aplica'),
                ],
                default='PENDIENTE',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='decision_veterinario_por',
            field=models.ForeignKey(
                blank=True,
                db_column='id_decision_veterinario_por',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='triajes_decision_veterinario',
                to='users.usuarios',
            ),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='decision_veterinario_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='decision_veterinario_observacion',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='veterinario_asignado_decision_id',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='triajepeticion',
            name='historial_decisiones',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
