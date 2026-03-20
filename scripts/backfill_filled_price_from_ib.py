from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from ib_async.objects import ExecutionFilter

from trading.market_data import connect
from trading.market_data import disconnect

if TYPE_CHECKING:
    from ib_async import IB

REPO_ROOT = Path(__file__).resolve().parents[1]
EXEC_DIR = REPO_ROOT / 'smb_trader_executions'

TARGET_TRADERS = {'Justin Spero', 'Jeff Holden'}

EVENT_TYPES_EXIT = {'ADD', 'TRIM', 'CLOSE'}
NEW_TYPE = 'NEW'

PACIFIC_TZ = ZoneInfo('America/Los_Angeles')
EASTERN_TZ = ZoneInfo('America/New_York')


def _parse_ts_pacific(ts: str) -> datetime | None:
    ts2 = (ts or '').strip()
    if not ts2:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            dt_p = datetime.strptime(ts2, fmt).replace(tzinfo=PACIFIC_TZ)
            return dt_p.astimezone(EASTERN_TZ).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _parse_int(v: str | None) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_float(v: str | None) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_et_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # ib_async typically returns naive datetimes; assume Eastern naive.
        return dt
    return dt.astimezone(EASTERN_TZ).replace(tzinfo=None)


def _pick_execution_csv_dates(last_n: int) -> list[str]:
    pat = re.compile(r'^executions_(\d{4}-\d{2}-\d{2})\.csv$')
    dates: list[str] = []
    for p in EXEC_DIR.iterdir():
        m = pat.match(p.name)
        if m:
            dates.append(m.group(1))
    dates_sorted = sorted(dates)
    if len(dates_sorted) <= last_n:
        return dates_sorted
    return dates_sorted[-last_n:]


@dataclass(frozen=True)
class BackfillEvent:
    file_date: str
    row_idx: int
    trader: str
    symbol: str
    change_type: str
    ts_et: datetime
    expected_shares: int
    ib_side: str  # 'BOT' or 'SLD'
    order_id: int | None


def _compute_events_for_file(rows: list[dict[str, str]], file_date: str) -> list[BackfillEvent]:
    # Reconstruct position size to compute the expected share count for CLOSE rows.
    # pos_sign: +1 for long, -1 for short; pos_abs: absolute share count.
    by_pair: dict[tuple[str, str], list[int]] = {}
    for idx, r in enumerate(rows):
        trader = (r.get('trader') or '').strip()
        if trader not in TARGET_TRADERS:
            continue
        symbol = (r.get('symbol') or '').strip()
        if not symbol:
            continue
        by_pair.setdefault((trader, symbol), []).append(idx)

    events: list[BackfillEvent] = []

    for (trader, symbol), idxs in by_pair.items():
        idxs_sorted = sorted(
            idxs,
            key=lambda i: _parse_ts_pacific(rows[i].get('timestamp') or '') or datetime.min,
        )

        pos_sign: int | None = None
        pos_abs: int = 0

        for i in idxs_sorted:
            r = rows[i]
            ct = (r.get('change_type') or '').strip().upper()
            ts_et = _parse_ts_pacific(r.get('timestamp') or '')
            if ts_et is None:
                continue

            order_id = _parse_int(r.get('order_id') or '')

            if ct == NEW_TYPE:
                shares = _parse_int(r.get('shares'))
                ns = (r.get('net_side') or '').strip().lower()
                if shares is None or ns not in {'long', 'short'}:
                    continue
                pos_sign = 1 if ns == 'long' else -1
                pos_abs = shares

            elif ct == 'ADD':
                add_shares = _parse_int(r.get('shares'))
                ns = (r.get('net_side') or '').strip().lower()
                if add_shares is None or ns not in {'long', 'short'}:
                    continue
                pos_sign = 1 if ns == 'long' else -1
                pos_abs += add_shares
                expected = add_shares

                ib_side = 'BOT' if pos_sign > 0 else 'SLD'
                events.append(
                    BackfillEvent(
                        file_date=file_date,
                        row_idx=i,
                        trader=trader,
                        symbol=symbol,
                        change_type=ct,
                        ts_et=ts_et,
                        expected_shares=expected,
                        ib_side=ib_side,
                        order_id=order_id,
                    )
                )

            elif ct == 'TRIM':
                dm = _parse_int(r.get('delta_magnitude') or '')
                ns = (r.get('net_side') or '').strip().lower()
                if dm is None or ns not in {'long', 'short'}:
                    continue
                trim_shares = abs(dm)
                pos_sign = 1 if ns == 'long' else -1
                pos_abs = max(0, pos_abs - trim_shares)
                expected = trim_shares

                # TRIM is an exit.
                ib_side = 'SLD' if pos_sign > 0 else 'BOT'
                events.append(
                    BackfillEvent(
                        file_date=file_date,
                        row_idx=i,
                        trader=trader,
                        symbol=symbol,
                        change_type=ct,
                        ts_et=ts_et,
                        expected_shares=expected,
                        ib_side=ib_side,
                        order_id=order_id,
                    )
                )

            elif ct == 'CLOSE':
                if pos_sign is None:
                    continue
                close_shares = pos_abs
                if close_shares <= 0:
                    continue
                expected = close_shares
                ib_side = 'SLD' if pos_sign > 0 else 'BOT'
                events.append(
                    BackfillEvent(
                        file_date=file_date,
                        row_idx=i,
                        trader=trader,
                        symbol=symbol,
                        change_type=ct,
                        ts_et=ts_et,
                        expected_shares=expected,
                        ib_side=ib_side,
                        order_id=order_id,
                    )
                )
                pos_abs = 0

    return events


def _connect_for_backfill() -> IB:
    client_ids = [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 17, 18, 19, 20]
    for cid in client_ids:
        ib = connect(readonly=True, client_id=cid)
        if ib is not None and ib.isConnected():
            return ib
    raise SystemExit('IB connect failed (is TWS/Gateway running on localhost:7496?)')


def backfill_file(ib: IB, fp: Path) -> int:
    with fp.open('r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]
        fieldnames = list(reader.fieldnames or [])

    # Ensure filled_price column exists.
    if 'filled_price' not in fieldnames:
        fieldnames.append('filled_price')
    for r in rows:
        r.setdefault('filled_price', '')

    file_date = fp.stem.replace('executions_', '')
    events = _compute_events_for_file(rows, file_date)
    if not events:
        print(f'  {fp.name}: no events, skipping')
        return 0

    # Group to minimize reqExecutions calls.
    events_by_group: dict[tuple[str, str], list[BackfillEvent]] = {}
    for e in events:
        events_by_group.setdefault((e.symbol, e.ib_side), []).append(e)

    # ExecutionFilter.time is inclusive and returns all executions after/at
    # the given time. Use tight buffers to avoid huge result sets.
    group_buffer_sec = 10
    event_window_sec = 15

    updated = 0

    for (symbol, ib_side), group_events in sorted(events_by_group.items()):
        group_events_sorted = sorted(group_events, key=lambda e: e.ts_et)
        min_ts = group_events_sorted[0].ts_et - timedelta(seconds=group_buffer_sec)
        max_ts = group_events_sorted[-1].ts_et + timedelta(seconds=group_buffer_sec)

        # Use the earliest event timestamp for this group to minimize the
        # execution range returned by IB (ExecutionFilter.time is "after/at").
        start_str = min_ts.strftime('%Y%m%d %H:%M:%S')
        filt = ExecutionFilter(symbol=symbol, secType='STK', side=ib_side, time=start_str)

        print(
            f'  reqExecutions: symbol={symbol} side={ib_side} start={start_str} '
            f'events={len(group_events_sorted)}',
            flush=True,
        )
        fills = ib.reqExecutions(filt)
        print(
            f'  reqExecutions returned: symbol={symbol} side={ib_side} fills={len(fills)}',
            flush=True,
        )

        norm_fills = []
        for fl in fills:
            ft = _to_et_naive(fl.time)
            if min_ts <= ft <= max_ts:
                norm_fills.append(fl)

        for ev in group_events_sorted:
            ev_start = ev.ts_et - timedelta(seconds=event_window_sec)
            ev_end = ev.ts_et + timedelta(seconds=event_window_sec)

            candidates = [fl for fl in norm_fills if ev_start <= _to_et_naive(fl.time) <= ev_end]

            if ev.order_id is not None:
                candidates = [
                    fl
                    for fl in candidates
                    if int(getattr(fl.execution, 'orderId', 0) or 0) == ev.order_id
                ]

            expected = ev.expected_shares

            def shares_close(fl: object, expected_shares: int = expected) -> bool:
                sh = float(fl.execution.shares)  # type: ignore[attr-defined]
                return abs(sh - expected_shares) <= max(1.0, expected_shares * 0.01)

            exact = [fl for fl in candidates if shares_close(fl)]

            vwap: float | None = None
            if exact:
                total_sh = sum(float(fl.execution.shares) for fl in exact)
                if total_sh > 0:
                    total_px = sum(
                        float(fl.execution.price) * float(fl.execution.shares) for fl in exact
                    )
                    vwap = total_px / total_sh
            else:
                total_sh2 = sum(float(fl.execution.shares) for fl in candidates)
                if total_sh2 > 0 and abs(total_sh2 - expected) <= max(1.0, expected * 0.05):
                    total_px2 = sum(
                        float(fl.execution.price) * float(fl.execution.shares) for fl in candidates
                    )
                    vwap = total_px2 / total_sh2

            if vwap is not None:
                rows[ev.row_idx]['filled_price'] = f'{vwap:.2f}'
                updated += 1

    # Write file back with stable fieldnames.
    with fp.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description='Backfill filled_price from IB executions.')
    parser.add_argument(
        '--last-n',
        type=int,
        default=10,
        help='Number of most-recent executions_YYYY-MM-DD.csv files to backfill.',
    )
    args = parser.parse_args()

    dates = _pick_execution_csv_dates(args.last_n)
    print('Backfilling filled_price for:', dates)

    ib = _connect_for_backfill()
    try:
        total_updated = 0
        for d in dates:
            fp = EXEC_DIR / f'executions_{d}.csv'
            total_updated += backfill_file(ib, fp)
        print('Total updated filled_price cells:', total_updated)
    finally:
        disconnect(ib)


if __name__ == '__main__':
    main()
