# Position Snapshot Database Integration

## Overview

This implementation adds database storage for position snapshots, capturing **all traders** (equity + options) regardless of whether they're active in `TRADER_ENABLED`. This is separate from the CSV execution tracking, which only saves enabled traders.

## Architecture Decision: Separate Module

**Why a wrapper module instead of inline code?**

1. **Separation of Concerns**: The main `trading/smb_screener.py` focuses on trading logic; database operations are isolated
2. **Maintainability**: Database schema changes don't require touching the main script
3. **Testability**: The wrapper can be tested independently
4. **Code Size**: The main script is already ~2000 lines; adding 100+ lines of DB code would make it harder to navigate

## Data Volume Analysis

### Current Setup
- **Frequency**: Every 10 seconds
- **Positions per snapshot**: ~150 (equity + options across all traders)
- **Trading day**: ~6.5 hours = 2,340 cycles/day

### Data Volume Scenarios

#### Scenario 1: Save All Snapshots (NOT RECOMMENDED)
- **Records/day**: 2,340 cycles × 150 positions = **351,000 records/day**
- **Storage/year**: ~95 GB/year
- **Database growth**: Unsustainable for long-term storage

#### Scenario 2: Save Only Changes (RECOMMENDED)
- **Records/day**: ~100-1,000 (only positions with `delta_magnitude != 0` or `change_type != null`)
- **Storage/year**: ~27-270 MB/year
- **Database growth**: Manageable, preserves meaningful data

#### Scenario 3: Save Only Changes + Exclude Flat Positions
- **Records/day**: ~50-500 (excludes zero positions)
- **Storage/year**: ~14-135 MB/year
- **Database growth**: Most efficient, still captures all meaningful changes

## Configuration

In `trading/smb_screener.py`:

```python
# Enable/disable database saving
SAVE_SNAPSHOTS_TO_DB = True

# Save only positions with changes (recommended)
SNAPSHOT_SAVE_ONLY_CHANGES = True

# Exclude flat positions (recommended)
SNAPSHOT_SAVE_FLAT_POSITIONS = False
```

## Database Model

The `PositionSnapshot` model stores:
- **All traders**: Justin Spero, Jeff Holden, Steve Spencer, Kenneth Sharkness
- **All instrument types**: Equity and Options
- **Full position details**: magnitude, side, change type, etc.
- **Timestamp**: When the snapshot was taken

## Usage

The database saving happens automatically in `run_single_cycle()` after the JSON snapshot is saved. It's wrapped in a try/except so database failures don't crash the main script.

## Migration Required

After adding the model, run:
```bash
python manage.py makemigrations executions
python manage.py migrate
```

## Performance Considerations

1. **Bulk Operations**: Uses `transaction.atomic()` for efficient batch inserts
2. **Indexes**: Properly indexed for common queries (trader, timestamp, symbol)
3. **Duplicate Handling**: Uses `get_or_create()` to handle timestamp collisions gracefully
4. **Error Handling**: Database errors are logged but don't stop the main script

## Future Enhancements

- Add Django admin interface for viewing snapshots
- Create API endpoints for querying historical positions
- Add data retention policies (e.g., archive snapshots older than 1 year)
- Add aggregation views for faster queries
