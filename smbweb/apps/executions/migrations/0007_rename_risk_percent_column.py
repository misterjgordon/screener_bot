# Psycopg 3 rejects SQL that contains % outside placeholders; quoted "risk_%" yields
# the invalid token %". Store as risk_percent (CSV header can stay risk_%).

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):

    dependencies = [
        ('executions', '0006_add_execution_filled_price'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql='ALTER TABLE executions RENAME COLUMN "risk_%" TO risk_percent;',
                    reverse_sql='ALTER TABLE executions RENAME COLUMN risk_percent TO "risk_%"',
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='execution',
                    name='risk_percent',
                    field=models.FloatField(blank=True, null=True),
                ),
            ],
        ),
    ]
