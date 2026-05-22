from __future__ import annotations

import csv
import re
from bisect import bisect_right
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from strategies.utils import bar_date
from trading.bar_loader import get_bars
from trading.market_data import connect
from trading.market_data import disconnect
from trading.market_timezones import display_zone
from trading.market_timezones import exchange_zone

if TYPE_CHECKING:
    from ib_async import IB

REPO_ROOT = Path(__file__).resolve().parents[1]
EXEC_DIR = REPO_ROOT / 'smb_trader_executions'

EVENT_TYPES_EXIT = {'ADD', 'TRIM', 'CLOSE'}
EVENT_TYPES_NEW = {'NEW'}


def parse_ts_pacific_to_et_naive(ts: str) -> datetime | None:
    ts2 = (ts or '').strip()
    if not ts2:
        return None
    # Normalize common ISO-8601 shape and drop timezone suffixes (we assume the
    # wall time is Pacific for this CSV schema).
    ts2 = ts2.replace('T', ' ')
    timezone_suffixes = [' PST', ' PDT', ' UTC', ' EST', ' EDT', ' CST', ' CDT', ' MST', ' MDT']
    for suffix in timezone_suffixes:
        if ts2.endswith(suffix):
            ts2 = ts2[: -len(suffix)].strip()
            break

    # Handle microseconds.
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            dt_p = datetime.strptime(ts2, fmt).replace(tzinfo=display_zone())
            return dt_p.astimezone(exchange_zone()).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _pick_connection() -> IB:
    # Try a handful of client_ids; some may be in use.
    for client_id in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 17, 18):
        ib = connect(readonly=True, client_id=client_id)
        if ib is not None and ib.isConnected():
            return ib
    raise SystemExit('Could not connect to IB on localhost:7496')


def _compute_close_at_or_before(
    times: list[datetime],
    closes: list[float | None],
    ts_et: datetime,
) -> float | None:
    idx = bisect_right(times, ts_et) - 1
    if idx < 0:
        return None
    # If close is missing for that bar, walk backwards until we find one.
    while idx >= 0:
        v = closes[idx]
        if v is not None:
            return float(v)
        idx -= 1
    return None


def main() -> None:
    pat = re.compile(r'^executions_(\d{4}-\d{2}-\d{2})\.csv$')

    exec_files = sorted(p for p in EXEC_DIR.iterdir() if pat.match(p.name))
    if not exec_files:
        raise SystemExit(f'No execution CSVs found in {EXEC_DIR}')

    ib = _pick_connection()

    # Cache 2-min bars by (symbol, session_day_et)
    bar_cache: dict[tuple[str, date], tuple[list[datetime], list[float | None]]] = {}

    total_rows_updated = 0
    total_files_updated = 0

    try:
        for fp in exec_files:
            updated_rows = 0
            with fp.open('r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    continue
                fieldnames = list(reader.fieldnames)
                rows = [dict(r) for r in reader]

            if not rows:
                continue

            if 'filled_price' not in fieldnames:
                fieldnames.append('filled_price')
                for r in rows:
                    r['filled_price'] = ''

            # Collect missing fills to compute: (symbol, session_day) -> list of (row_idx, ts_et)
            missing: dict[tuple[str, date], list[tuple[int, datetime]]] = {}

            for i, r in enumerate(rows):
                ct = (r.get('change_type') or '').strip().upper()
                if ct in EVENT_TYPES_NEW:
                    r['filled_price'] = ''
                    continue
                if ct not in EVENT_TYPES_EXIT:
                    continue

                filled = (r.get('filled_price') or '').strip()
                if filled != '':
                    continue

                symbol = (r.get('symbol') or '').strip()
                ts_et = parse_ts_pacific_to_et_naive(r.get('timestamp') or '')
                if not symbol or ts_et is None:
                    continue

                key = (symbol, ts_et.date())
                missing.setdefault(key, []).append((i, ts_et))

            if not missing:
                continue

            # Fetch bars for all needed sessions, using cache.
            for (symbol, session_day) in missing.keys():
                cache_key = (symbol, session_day)
                if cache_key in bar_cache:
                    continue

                bars = get_bars(
                    ib,
                    symbol=symbol,
                    duration_str='1 D',
                    bar_size='2 mins',
                    what_to_show='TRADES',
                    use_rth=False,
                    end_date=session_day,
                )
                if not bars:
                    bar_cache[cache_key] = ([], [])
                    continue

                items: list[tuple[datetime, float | None]] = []
                for b in bars:
                    bd = b.date
                    if not isinstance(bd, datetime):
                        continue
                    t = bd
                    if t.tzinfo is not None:
                        t = t.replace(tzinfo=None)
                    if bar_date(t) != session_day:
                        continue
                    items.append((t, None if b.close is None else float(b.close)))

                items_sorted = sorted(items, key=lambda kv: kv[0])
                times = [x[0] for x in items_sorted]
                closes = [x[1] for x in items_sorted]
                bar_cache[cache_key] = (times, closes)

            # Apply computed prices
            for (symbol, session_day), lst in missing.items():
                times, closes = bar_cache.get((symbol, session_day), ([], []))
                if not times:
                    continue

                for row_idx, ts_et in lst:
                    px = _compute_close_at_or_before(times, closes, ts_et)
                    if px is None:
                        continue
                    rows[row_idx]['filled_price'] = f'{px:.2f}'
                    updated_rows += 1

            if updated_rows > 0:
                total_files_updated += 1
                total_rows_updated += updated_rows

                with fp.open('w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

    finally:
        disconnect(ib)

    print(
        'Done.',
        f'files_updated={total_files_updated}',
        f'rows_updated={total_rows_updated}',
        flush=True,
    )


if __name__ == '__main__':
    main()
