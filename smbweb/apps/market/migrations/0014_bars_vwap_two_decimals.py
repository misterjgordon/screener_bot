from django.db import migrations
from django.db import models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0013_bars_add_vwap'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bars',
            name='vwap',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=16,
                null=True,
            ),
        ),
    ]
