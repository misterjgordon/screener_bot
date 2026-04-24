from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0010_alter_bars_symbol'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'market_security'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'market_bars'
                ) THEN
                    ALTER TABLE public.market_security RENAME TO market_bars;
                END IF;
            END $$;
            """,
            reverse_sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'market_bars'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'market_security'
                ) THEN
                    ALTER TABLE public.market_bars RENAME TO market_security;
                END IF;
            END $$;
            """,
        ),
    ]
