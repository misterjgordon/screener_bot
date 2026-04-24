from django.db import migrations
from django.db import models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0005_symbol_company_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='symbol',
            name='exchange_name',
            field=models.CharField(default='', max_length=20),
            preserve_default=False,
        ),
        migrations.RunSQL(
            sql=(
                "UPDATE market_symbol s "
                "SET exchange_name = COALESCE(e.name, '') "
                "FROM market_exchange e "
                "WHERE s.exchange_id = e.id;"
            ),
            reverse_sql=(
                "UPDATE market_symbol "
                "SET exchange_name = COALESCE(exchange_name, '');"
            ),
        ),
        migrations.RemoveField(
            model_name='symbol',
            name='exchange',
        ),
        migrations.RenameField(
            model_name='symbol',
            old_name='exchange_name',
            new_name='exchange',
        ),
        migrations.DeleteModel(
            name='Exchange',
        ),
    ]
