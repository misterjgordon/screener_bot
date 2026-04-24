# Symbol PK becomes ticker only (symbol column). Postgres cannot add a second PK in one
# AlterField step; drop FK, truncate bar rows, drop old PK column, repoint FK.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0002_seed_alpaca_exchange'),
    ]

    state_operations = [
        migrations.AlterUniqueTogether(
            name='symbol',
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name='symbol',
            name='symbol',
            field=models.CharField(max_length=20, primary_key=True, serialize=False),
        ),
        migrations.RemoveField(
            model_name='symbol',
            name='id',
        ),
    ]

    # Drop child FK first (Django may name the constraint with a hash), then PK change.
    sql_forward = """
        ALTER TABLE market_security
            DROP CONSTRAINT IF EXISTS market_security_symbol_id_fkey;
        ALTER TABLE market_security
            DROP CONSTRAINT IF EXISTS market_security_symbol_id_376b9edb_fk_market_symbol_id;
        TRUNCATE TABLE market_security;
        TRUNCATE TABLE market_symbol;
        ALTER TABLE market_symbol
            DROP CONSTRAINT IF EXISTS market_symbol_pkey;
        ALTER TABLE market_symbol
            DROP COLUMN IF EXISTS id;
        ALTER TABLE market_symbol
            ADD PRIMARY KEY (symbol);
        ALTER TABLE market_security
            ADD CONSTRAINT market_security_symbol_id_fkey
            FOREIGN KEY (symbol_id)
            REFERENCES market_symbol (symbol)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED;
    """

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql_forward, reverse_sql=migrations.RunSQL.noop),
            ],
            state_operations=state_operations,
        ),
    ]
