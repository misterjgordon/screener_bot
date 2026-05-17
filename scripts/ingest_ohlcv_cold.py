#!/usr/bin/env python3
"""Backfill cold OHLCV Parquet from Massive REST (Django-free).

Requires ``OHLCV_COLD_ROOT``, ``MASSIVE_API_KEY``. Ticker list: default shortlist
(``trading/data/symbols/shortlist_stocks.csv``) or ``OHLCV_SYMBOL_LIST_PATH``.

Refuses large (calendar_days × symbol_count) jobs unless ``--allow-high-volume-ingest``
so a wide date range over the full list cannot run by accident.

When ``--start`` / ``--end`` are omitted, the ingest window defaults to the same UTC
calendar bounds as ``OHLCV_DEFAULT_INGEST_*`` in
``trading.storage.ohlcv.ohlcv_ingest_limits`` (kept in sync with cold verify tests).

Massive REST rate limits are **plan-specific**; the API returns HTTP 429 when exceeded.
If you see throttling, lower ``--jobs`` or add backoff (not implemented here).

Example (1y single-symbol run exceeds the default symbol-day budget)::

    export OHLCV_COLD_ROOT=/path/to/ohlcv
    export MASSIVE_API_KEY=...
    uv run --frozen python scripts/ingest_ohlcv_cold.py --allow-high-volume-ingest --jobs 1

Example with explicit dates::

    uv run --frozen python scripts/ingest_ohlcv_cold.py --start 2024-01-02 --end 2024-01-02 --max-symbols 5
"""

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import UTC
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path

import requests

from trading.integrations.massive_bars import fetch_stock_minute_bars_dataframe
from trading.storage.ohlcv.ohlcv_ingest_limits import DEFAULT_INGEST_SYMBOL_DAY_BUDGET
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_START_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import symbol_day_ingest_cost
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_symbol_store import write_bars

_p_repo = Path(__file__).resolve().parent.parent
if str(_p_repo) not in sys.path:
    sys.path.insert(0, str(_p_repo))


log = logging.getLogger(__name__)

_thread_local = threading.local()


def _worker_session() -> requests.Session:
    """One ``requests.Session`` per ingest worker thread (TLS + connection reuse)."""
    sess = getattr(_thread_local, 'session', None)
    if sess is None:
        sess = requests.Session()
        _thread_local.session = sess
    return sess


def _parse_utc_datetime(value: str) -> datetime:
    """Parse ISO date or datetime; naive values are treated as UTC."""
    value = value.strip()
    if len(value) == 10 and value[4] == '-' and value[7] == '-':
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _default_ingest_utc_bounds() -> tuple[datetime, datetime]:
    """Match Massive inclusive window used in tests (``_massive_fetch_bounds_for_verify_dates``)."""
    start = datetime.combine(OHLCV_DEFAULT_INGEST_START_DATE, dt_time.min, tzinfo=UTC)
    end = datetime.combine(OHLCV_DEFAULT_INGEST_END_DATE, dt_time.max, tzinfo=UTC)
    return start, end


def _ingest_symbol(symbol: str, start: datetime, end: datetime) -> tuple[str, int]:
    t0 = time.perf_counter()
    df_bars = fetch_stock_minute_bars_dataframe(
        symbol,
        start,
        end,
        interval_minutes=1,
        session=_worker_session(),
    )
    t_fetch = time.perf_counter()
    if df_bars.empty:
        log.info(
            'ingest_timing symbol=%s rows=0 fetch_s=%.3f',
            symbol,
            t_fetch - t0,
        )
        return symbol, 0
    write_bars(df_bars, interval_minutes=1)
    t_write = time.perf_counter()
    n = len(df_bars)
    log.info(
        'ingest_timing symbol=%s rows=%s fetch_s=%.3f write_s=%.3f total_s=%.3f',
        symbol,
        f'{n:,}',
        t_fetch - t0,
        t_write - t_fetch,
        t_write - t0,
    )
    return symbol, n


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description='Ingest Massive minute bars to cold Parquet')
    parser.add_argument(
        '--start',
        default=None,
        help=(
            'ISO date or datetime (UTC if naive). Omit to use OHLCV_DEFAULT_INGEST_START_DATE '
            f'({OHLCV_DEFAULT_INGEST_START_DATE.isoformat()})'
        ),
    )
    parser.add_argument(
        '--end',
        default=None,
        help=(
            'ISO date or datetime (UTC if naive). Omit to use OHLCV_DEFAULT_INGEST_END_DATE '
            f'({OHLCV_DEFAULT_INGEST_END_DATE.isoformat()}, end-of-day UTC inclusive for REST)'
        ),
    )
    parser.add_argument(
        '--jobs',
        type=int,
        default=8,
        metavar='N',
        help=(
            'Parallel symbol fetches (default 8). Uses one HTTP session per worker for '
            'connection reuse; lower if Massive rate-limits or the host is memory-bound.'
        ),
    )
    parser.add_argument(
        '--max-symbols',
        type=int,
        default=None,
        metavar='N',
        help='Ingest at most the first N tickers from the list (order preserved)',
    )
    parser.add_argument(
        '--allow-high-volume-ingest',
        action='store_true',
        help=(
            'Allow calendar span × symbol count above the default symbol-day budget '
            f'({DEFAULT_INGEST_SYMBOL_DAY_BUDGET})'
        ),
    )
    args = parser.parse_args()

    if args.start is None and args.end is None:
        start, end = _default_ingest_utc_bounds()
    else:
        if args.start is None or args.end is None:
            log.error('Pass both --start and --end, or omit both for the default verify window')
            return 2
        start = _parse_utc_datetime(args.start)
        end = _parse_utc_datetime(args.end)
    if start > end:
        log.error('start must be <= end')
        return 2

    p_list = get_p_ohlcv_symbol_list_path()
    tickers = load_tickers_from_symbol_list_file(p_list)
    if args.max_symbols is not None:
        if args.max_symbols < 1:
            log.error('--max-symbols must be >= 1')
            return 2
        tickers = tickers[: args.max_symbols]
    log.info('Ticker list %s (%s symbols after --max-symbols)', p_list, len(tickers))

    cost = symbol_day_ingest_cost(start, end, len(tickers))
    log.info(
        'ingest_window start_utc=%s end_utc=%s symbol_days=%s budget=%s',
        start.isoformat(),
        end.isoformat(),
        cost,
        DEFAULT_INGEST_SYMBOL_DAY_BUDGET,
    )
    if cost > DEFAULT_INGEST_SYMBOL_DAY_BUDGET and not args.allow_high_volume_ingest:
        log.error(
            'Refusing ingest: %s symbol-days exceeds budget %s (narrow --start/--end, '
            'use --max-symbols, or pass --allow-high-volume-ingest after intentional review)',
            cost,
            DEFAULT_INGEST_SYMBOL_DAY_BUDGET,
        )
        return 2

    jobs = max(1, args.jobs)
    total_rows = 0
    t_run0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(_ingest_symbol, sym, start, end): sym
            for sym in tickers
        }
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                _, n = fut.result()
                total_rows += n
                log.info('%s: %s rows', sym, f'{n:,}')
            except Exception as e:
                log.warning('%s: failed: %s', sym, e)

    log.info(
        'Done; total_rows=%s wall_s=%.3f',
        f'{total_rows:,}',
        time.perf_counter() - t_run0,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
