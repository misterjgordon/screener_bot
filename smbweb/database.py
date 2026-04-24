"""Custom database utilities for smbweb project."""
# For now, it's a placeholder - we can add custom database utilities later
# as needed (similar to other ModelBase, CustomQuerySet, etc.)

# When we create models, we can import custom base classes here if needed
# from django.db import models

# Bulk bar ingest idempotency (future): pandas to_sql(if_exists='append') cannot
# emit INSERT ... ON CONFLICT DO NOTHING. Options after market_bars has a
# UNIQUE (interval, symbol, timestamp) or equivalent composite PK:
#   1) Load new rows into a TEMP/STAGING table via pandas, then run raw SQL
#      INSERT INTO market_bars SELECT ... FROM staging ON CONFLICT DO NOTHING.
#   2) Use psycopg execute_values with ON CONFLICT DO NOTHING.
#   3) Keep append-only plus pre-filter (max_date + drop_duplicates) until volume
#      requires (1) or (2).
