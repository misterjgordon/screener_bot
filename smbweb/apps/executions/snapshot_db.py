"""
Database wrapper for saving position change events to PostgreSQL.

This module reads from position_snapshot.json and saves change events to the database.
Each database record represents a CHANGE event - only rows with changes are saved.

Think of it like a cumulative sum:
- Each saved record = one position change event
- total_magnitude = position size AFTER the change
- delta_magnitude = the change amount (+ or -)
- prev_magnitude = position size BEFORE the change

Usage:
    from smbweb.apps.executions.snapshot_db import save_snapshot_from_file
    
    # Reads position_snapshot.json and saves only changed positions
    save_snapshot_from_file(save_only_changes=True)
"""
import os
import json
import django

# Setup Django environment if not already configured
if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smbweb.settings')
    django.setup()

from django.db import transaction
from django.utils import timezone
from smbweb.apps.executions.models import Position
from datetime import datetime
from typing import List, Dict


def save_snapshot_from_file(
    snapshot_file: str = "position_snapshot.json",
    save_only_changes: bool = True,
    save_flat_positions: bool = False,
    timestamp: datetime | None = None
) -> Dict[str, int]:
    """
    Read position snapshot from JSON file and save change events to database.
    
    Only saves rows where there was a change - think of it like a cumulative sum where
    the position size is only updated on changes. Each saved record represents one change event.
    
    Args:
        snapshot_file: Path to position_snapshot.json file (defaults to "position_snapshot.json")
        save_only_changes: If True, only save rows with changes (delta_magnitude != 0 or change_type != null)
                          Returns early with all zeros if no changes detected
                          If False, save all positions (WARNING: high data volume - not recommended!)
        save_flat_positions: If True, include flat positions (net_side='flat' and total_magnitude=0)
                            If False, exclude flat positions to reduce data volume
        timestamp: Optional datetime for change event timestamp (defaults to now)
    
    Returns:
        Dictionary with counts: {'saved': int, 'skipped': int, 'errors': int, 'has_changes': bool}
    
    Event Log Structure:
        - Each saved record = one change event for one (trader, symbol) position
        - total_magnitude = position size AFTER this change
        - delta_magnitude = the change amount (+ or -)
        - prev_magnitude = position size BEFORE this change
        - change_type = type of change (NEW, ADD, TRIM, CLOSE, FLIP)
    
    Data Volume:
        - Running every 10 seconds with ~150 positions = ~1.3M records/day if saving all
        - With save_only_changes=True: ~100-1000 records/day (only actual position changes)
        - If no changes detected, returns early without database operations
    """
    # Read snapshot from JSON file
    if not os.path.exists(snapshot_file):
        return {
            'saved': 0,
            'skipped': 0,
            'errors': 1,
            'has_changes': False,
            'error_message': f"Snapshot file not found: {snapshot_file}"
        }
    
    try:
        with open(snapshot_file, 'r', encoding='utf-8') as f:
            summary_rows = json.load(f)
    except Exception as e:
        return {
            'saved': 0,
            'skipped': 0,
            'errors': 1,
            'has_changes': False,
            'error_message': f"Error reading snapshot file: {e}"
        }
    
    if timestamp is None:
        timestamp = timezone.now()
    
    saved_count = 0
    skipped_count = 0
    error_count = 0
    
    # First pass: check if there are any changes (if save_only_changes is True)
    if save_only_changes:
        has_changes = False
        for row in summary_rows:
            # Skip flat positions if requested
            if not save_flat_positions:
                if row.get('net_side') == 'flat' and row.get('total_magnitude', 0) == 0:
                    continue
            
            # Check for changes
            delta_magnitude = row.get('delta_magnitude', 0)
            change_type = row.get('change_type')
            if delta_magnitude != 0 or change_type:
                has_changes = True
                break
        
        # If no changes detected, return early without database operations
        if not has_changes:
            return {
                'saved': 0,
                'skipped': len(summary_rows),
                'errors': 0,
                'has_changes': False
            }
    
    try:
        with transaction.atomic():
            for row in summary_rows:
                try:
                    # Skip flat positions if requested
                    if not save_flat_positions:
                        if row.get('net_side') == 'flat' and row.get('total_magnitude', 0) == 0:
                            skipped_count += 1
                            continue
                    
                    # Skip unchanged positions if requested
                    if save_only_changes:
                        delta_magnitude = row.get('delta_magnitude', 0)
                        change_type = row.get('change_type')
                        if delta_magnitude == 0 and not change_type:
                            skipped_count += 1
                            continue
                    
                    # Extract option fields
                    expiry = row.get('expiry')
                    strike = row.get('strike')
                    option_type = row.get('option_type')
                    
                    # Convert expiry string to date if needed
                    if expiry and isinstance(expiry, str):
                        try:
                            from datetime import datetime as dt
                            expiry = dt.strptime(expiry, '%Y-%m-%d').date()
                        except (ValueError, TypeError):
                            expiry = None
                    
                    # Create Position record (represents one change event)
                    # This record captures: what changed, when, and the new position size
                    position = Position(
                        timestamp=timestamp,
                        trader=row.get('trader'),
                        symbol=row.get('symbol'),
                        instrument_type=row.get('instrument_type', 'equity'),
                        underlying=row.get('underlying') or row.get('symbol'),
                        expiry=expiry,
                        strike=strike,
                        option_type=option_type,
                        is_long_term=row.get('is_long_term', False),
                        net_side=row.get('net_side', 'flat'),
                        conflict=row.get('conflict', False),
                        total_magnitude=row.get('total_magnitude', 0.0),  # Position size AFTER change
                        prev_magnitude=row.get('prev_magnitude'),  # Position size BEFORE change
                        delta_magnitude=row.get('delta_magnitude'),  # The change amount
                        change_type=row.get('change_type') or None,  # Type of change (NEW, ADD, etc.)
                    )
                    
                    # Use get_or_create to handle duplicates gracefully
                    # Each (timestamp, trader, symbol) combination represents one change event
                    # We preserve all change events - this is an event log, not a state table
                    Position.objects.get_or_create(
                        timestamp=timestamp,
                        trader=position.trader,
                        symbol=position.symbol,
                        defaults={
                            'instrument_type': position.instrument_type,
                            'underlying': position.underlying,
                            'expiry': position.expiry,
                            'strike': position.strike,
                            'option_type': position.option_type,
                            'is_long_term': position.is_long_term,
                            'net_side': position.net_side,
                            'conflict': position.conflict,
                            'total_magnitude': position.total_magnitude,
                            'prev_magnitude': position.prev_magnitude,
                            'delta_magnitude': position.delta_magnitude,
                            'change_type': position.change_type,
                        }
                    )
                    saved_count += 1
                    
                except Exception as e:
                    error_count += 1
                    print(f"Error saving snapshot for {row.get('trader')}/{row.get('symbol')}: {e}")
                    # Continue processing other rows
                    continue
        
        return {
            'saved': saved_count,
            'skipped': skipped_count,
            'errors': error_count,
            'has_changes': True
        }
        
    except Exception as e:
        print(f"Fatal error saving snapshots to database: {e}")
        import traceback
        traceback.print_exc()
        return {
            'saved': saved_count,
            'skipped': skipped_count,
            'errors': error_count + 1,
            'has_changes': True
        }


def get_latest_position_timestamp(trader: str | None = None) -> datetime | None:
    """
    Get the timestamp of the most recent position change in the database.
    
    Args:
        trader: Optional trader name to filter by
    
    Returns:
        Datetime of most recent position change, or None if no positions exist
    """
    queryset = Position.objects.all()
    if trader:
        queryset = queryset.filter(trader=trader)
    
    latest = queryset.order_by('-timestamp').first()
    return latest.timestamp if latest else None
