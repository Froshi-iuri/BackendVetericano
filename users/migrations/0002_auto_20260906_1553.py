# AnaC
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuarios',
            name='identificacion',
            field=models.CharField(default='000000', max_length=20, unique=True),
            preserve_default=False,
        ),
    ]