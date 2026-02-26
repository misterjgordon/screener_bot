# Creating the Positions Table

## Summary

1. ✅ Removed database integration from `trading/smb_screener.py` - no changes needed there
2. ✅ Created `Position` model in `smbweb/apps/executions/models.py` (table name: `positions`)
3. ✅ Updated `snapshot_db.py` to read from `position_snapshot.json` file
4. ⏳ **Next Step**: Create Django migration for the new `positions` table

## Steps to Create the Migration

### 1. Generate the Migration

Run Django's migration generator:

```bash
python manage.py makemigrations executions
```

This will create a new migration file (e.g., `0002_position.py`) in `smbweb/apps/executions/migrations/`

### 2. Review the Migration

The migration should create a table called `positions` with these fields:
- `id` (primary key)
- `timestamp` (DateTimeField, indexed)
- `trader` (CharField, indexed)
- `symbol` (CharField, indexed)
- `instrument_type` (CharField with choices: equity/option, indexed)
- `underlying` (CharField, indexed)
- `expiry` (DateField, nullable, indexed)
- `strike` (DecimalField, nullable)
- `option_type` (CharField, nullable, choices: C/P)
- `is_long_term` (BooleanField, indexed)
- `net_side` (CharField with choices: long/short/flat)
- `conflict` (BooleanField)
- `total_magnitude` (FloatField)
- `prev_magnitude` (FloatField, nullable)
- `delta_magnitude` (FloatField, nullable)
- `change_type` (CharField, nullable, indexed)

Plus indexes:
- `-timestamp, trader`
- `trader, underlying, -timestamp`
- `instrument_type, -timestamp`
- `timestamp, trader, symbol` (for uniqueness)

And unique constraint:
- `unique_together = [['timestamp', 'trader', 'symbol']]`

### 3. Apply the Migration

```bash
python manage.py migrate executions
```

This will create the `positions` table in your PostgreSQL database.

## Testing the Integration

After migration, you can test the snapshot saving:

```python
from smbweb.apps.executions.snapshot_db import save_snapshot_from_file

# This reads position_snapshot.json and saves only changed positions
result = save_snapshot_from_file(
    snapshot_file="position_snapshot.json",
    save_only_changes=True,
    save_flat_positions=False
)

print(f"Saved: {result['saved']}, Skipped: {result['skipped']}, Errors: {result['errors']}")
```

## Model Details

The `Position` model:
- **Table name**: `positions` (not `position_snapshots`)
- **Event log approach**: Only saves rows with changes (`delta_magnitude != 0` or `change_type != null`)
- **Reads from**: `position_snapshot.json` file (not passed as parameters)
- **All traders**: Saves positions for all traders regardless of `TRADER_ENABLED` status
- **All instruments**: Saves both equity and options positions
