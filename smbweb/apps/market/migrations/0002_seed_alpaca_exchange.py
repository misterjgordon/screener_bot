# Generated manually for default Alpaca exchange row (historical stock bars).

from typing import TYPE_CHECKING

from django.db import migrations

if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def seed_alpaca_exchange(apps: 'Apps', schema_editor: 'BaseDatabaseSchemaEditor') -> None:
    Exchange = apps.get_model('market', 'Exchange')
    Exchange.objects.get_or_create(pk=1, defaults={'name': 'alpaca'})


def unseed_alpaca_exchange(apps: 'Apps', schema_editor: 'BaseDatabaseSchemaEditor') -> None:
    Exchange = apps.get_model('market', 'Exchange')
    Exchange.objects.filter(pk=1, name='alpaca').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_alpaca_exchange, unseed_alpaca_exchange),
    ]
