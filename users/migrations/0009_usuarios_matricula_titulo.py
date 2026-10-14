from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_usuarios_telefono'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuarios',
            name='matricula_profesional',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
