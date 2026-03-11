"""Position normalization, summarization, and snapshot save/load for SMB screener."""

import json
import re
from pathlib import Path

from trading.models import NormalizedRecord
from trading.models import PositionSummary

_REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_FILE = str(_REPO_ROOT / 'resources' / 'positions' / 'position_snapshot.json')


def normalize_record(rec: dict) -> NormalizedRecord:
    """
    Convert a raw external-positions record into a NormalizedRecord.

    Extracts trader name, long-term flag, symbol info (equity vs option),
    side (long/short/flat), and magnitude.
    """
    account_name = rec['account_name']  # e.g. "VC Jeff Holden" or "VC Justin Spero LT"

    # Strip leading "VC " if present
    if account_name.startswith('VC '):
        core = account_name[3:]
    else:
        core = account_name

    # Long-term flag if name ends with " LT"
    is_long_term = core.endswith(' LT')
    if is_long_term:
        trader_name = core[:-3]  # drop trailing " LT"
    else:
        trader_name = core

    symbol_raw = rec['symbol']       # e.g. "AMD" or "ETHA 2025-11-21 P 23.00"
    side = rec['side'].lower()      # "long" "short" "flat"
    magnitude = rec['magnitude']

    # Try to detect options of the form: "TICKER YYYY-MM-DD C/P STRIKE"
    # Example: "ETHA 2025-11-21 P 23.00"
    opt_match = re.match(r'^([A-Z]+)\s+(\d{4}-\d{2}-\d{2})\s+([CP])\s+([\d.]+)$', symbol_raw)

    if opt_match:
        underlying, expiry, opt_type, strike_str = opt_match.groups()
        instrument_type = 'option'
        strike = float(strike_str)
    else:
        underlying = symbol_raw
        expiry = None
        opt_type = None
        strike = None
        instrument_type = 'equity'

    # Normalize side into long/short/flat
    if side not in ('long', 'short', 'flat'):
        normalized_side = 'unknown'
    else:
        normalized_side = side

    return NormalizedRecord(
        trader=trader_name,
        is_long_term=is_long_term,
        symbol_raw=symbol_raw,
        side=normalized_side,
        magnitude=magnitude,
        last_updated=rec['last_updated'],
        created_at=rec['created_at'],
        instrument_type=instrument_type,
        underlying=underlying,
        expiry=expiry,
        strike=strike,  # "C" or "P" for options
        option_type=opt_type,  # full SMB account name
    )


def summarize_group(records: list[NormalizedRecord]) -> PositionSummary:
    """
    Determine the trader's *net* position for one symbol.

    Args:
        records: The list of normalized records for one (trader, symbol) group.
    """
    has_long = any(r.side == 'long' and r.magnitude > 0 for r in records)
    has_short = any(r.side == 'short' and r.magnitude > 0 for r in records)

    if has_long and has_short:
        net_side = 'conflict'
        conflict = True
    elif has_long:
        net_side = 'long'
        conflict = False
    elif has_short:
        net_side = 'short'
        conflict = False
    else:
        net_side = 'flat'
        conflict = False
    base = records[0]
    return PositionSummary(
        trader=base.trader,
        is_long_term=base.is_long_term,
        symbol=base.symbol_raw,
        instrument_type=base.instrument_type,
        underlying=base.underlying,
        expiry=base.expiry,
        strike=base.strike,
        option_type=base.option_type,
        net_side=net_side,
        conflict=conflict,
        total_magnitude=sum(r.magnitude for r in records),
    )


def make_position_key(row: PositionSummary) -> tuple[str, bool, str, str]:
    """Create a unique key for a position summary row based on trader, LT flag, symbol, and side."""
    return (row.trader, row.is_long_term, row.symbol, row.net_side)


def make_position_key_no_side(row: PositionSummary) -> tuple[str, bool, str]:
    """Create a key for a position without the side (used to detect position closures)."""
    return (row.trader, row.is_long_term, row.symbol)


def inject_closed_position_rows(
    current_rows: list[PositionSummary],
    previous_snapshot: list[PositionSummary] | None,
) -> list[PositionSummary]:
    """
    When a trader exits a position, the API omits that symbol (no row returned).
    Add synthetic flat rows for (trader, symbol) that were in the previous
    snapshot (non-flat) but missing from current so we detect CLOSE and place
    the exit order.
    """
    if not previous_snapshot:
        return current_rows
    current_keys = {make_position_key_no_side(row) for row in current_rows}
    synthetic: list[PositionSummary] = []
    for prev in previous_snapshot:
        if prev.net_side == 'flat' or (prev.total_magnitude or 0) <= 0:
            continue
        key = make_position_key_no_side(prev)
        if key in current_keys:
            continue
        synthetic.append(PositionSummary(
            trader=prev.trader,
            is_long_term=prev.is_long_term,
            symbol=prev.symbol,
            instrument_type=prev.instrument_type,
            underlying=prev.underlying or prev.symbol,
            expiry=prev.expiry,
            strike=prev.strike,
            option_type=prev.option_type,
            net_side='flat',
            conflict=False,
            total_magnitude=0,
        ))
    return current_rows + synthetic


def annotate_with_changes(
    current_rows: list[PositionSummary],
    previous_snapshot: list[PositionSummary] | None,
) -> list[PositionSummary]:
    """
    Given the current summary rows and the previously saved snapshot,
    enrich each row with prev_magnitude, delta_magnitude, and change_type.
    """
    if previous_snapshot is None:
        for row in current_rows:
            row.prev_magnitude = 0
            row.delta_magnitude = row.total_magnitude
            row.change_type = 'NEW' if row.total_magnitude != 0 else 'NONE'
        return current_rows
    prev_index: dict[tuple[str, bool, str, str], PositionSummary] = {}
    for prev_row in previous_snapshot:
        key = make_position_key(prev_row)
        prev_index[key] = prev_row
    prev_index_no_side: dict[tuple[str, bool, str], list[PositionSummary]] = {}
    for prev_row in previous_snapshot:
        key_no_side = make_position_key_no_side(prev_row)
        if key_no_side not in prev_index_no_side:
            prev_index_no_side[key_no_side] = []
        prev_index_no_side[key_no_side].append(prev_row)
    for row in current_rows:
        key = make_position_key(row)
        prev_row = prev_index.get(key)
        prev_mag = prev_row.total_magnitude if prev_row else 0
        curr_mag = row.total_magnitude
        prev_side = prev_row.net_side if prev_row else 'flat'
        curr_side = row.net_side
        row.prev_magnitude = prev_mag
        row.delta_magnitude = curr_mag - prev_mag
        if prev_side != 'flat' and curr_side == 'flat':
            change = 'CLOSE'
        elif prev_row is None and curr_mag > 0:
            change = 'NEW'
        elif prev_row is None and curr_mag == 0:
            key_no_side = make_position_key_no_side(row)
            prev_rows_same_symbol = prev_index_no_side.get(key_no_side, [])
            had_non_flat_position = any(
                p.net_side != 'flat' and p.total_magnitude > 0 for p in prev_rows_same_symbol
            )
            change = 'CLOSE' if had_non_flat_position else 'FLAT'
        elif prev_side != curr_side and prev_side != 'flat' and curr_side != 'flat':
            change = 'FLIP'
        elif (row.delta_magnitude or 0) > 0:
            change = 'ADD'
        elif (row.delta_magnitude or 0) < 0:
            change = 'TRIM'
        else:
            change = None
        row.change_type = change
    return current_rows


def save_snapshot(summary_rows: list[PositionSummary], path: str = SNAPSHOT_FILE) -> None:
    """Save the current summarized positions to disk as JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('w', encoding='utf-8') as f:
        json.dump([r.to_dict() for r in summary_rows], f, indent=2)


def load_snapshot(path: str = SNAPSHOT_FILE) -> list[PositionSummary] | None:
    """Load a previously saved snapshot of summarized positions from disk."""
    if not Path(path).exists():
        print(f'Snapshot file not found: {path}')
        return None
    with Path(path).open('r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        print(f'Snapshot file has invalid format (expected list of dicts): {path}')
        return None
    return [PositionSummary.from_dict(row) for row in data]
