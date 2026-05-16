from django.db import migrations
from django.db import models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0012_bars_precision_update'),
    ]

    operations = [
        migrations.AddField(
            model_name='bars',
            name='vwap',
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                max_digits=16,
                null=True,
            ),
        ),
    ]
