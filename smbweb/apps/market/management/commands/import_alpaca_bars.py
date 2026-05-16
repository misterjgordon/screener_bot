"""Load OHLCV bars from Alpaca into ``market_bars`` for tickers listed in a CSV file.

Edit ``SYMBOL_LIST_CSV_FILENAME`` (full export vs shortlist), ``BACKFILL_START``, and
``BACKFILL_END`` for normal runs; use ``None`` for both bounds to use each symbol’s DB
watermark + now.

Example::

    uv run --frozen python smbweb/manage.py import_alpaca_bars
"""

import argparse
import csv
from datetime import UTC
from datetime import datetime as dt
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import OutputWrapper

from smbweb.apps.market.models import Symbol
from trading.integrations import alpaca_bars as alpaca_bars_mod

_p_alpaca_bars_file = alpaca_bars_mod.__file__
assert _p_alpaca_bars_file is not None
p_symbols_dir = Path(_p_alpaca_bars_file).resolve().parent.parent / 'data' / 'symbols'
SYMBOL_LIST_CSV_FILENAME = 'All_stocks_filtered.csv'

# Edit here for normal runs; use ``None`` for both bounds to use each symbol’s DB watermark + now.
BACKFILL_START: str | None = '2026-05-08T09:30:00-04:00'
BACKFILL_END: str | None = '2026-05-08T09:32:00-04:00'


def _parse_iso_bound(value: str | None) -> dt | None:
    """Parse ISO datetime string and normalize to timezone-aware UTC."""
    if value is None:
        return None
    parsed = dt.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def _load_tickers_from_csv_file(p_csv: Path) -> list[str]:
    """First column per row; skip a leading ``Symbol`` header row; dedupe preserving order."""
    if not p_csv.is_file():
        raise CommandError(f'Symbol list CSV not found: {p_csv}')

    with p_csv.open(encoding='utf-8-sig', newline='') as csv_file:
        rows = list(csv.reader(csv_file))

    if not rows:
        raise CommandError(f'Symbol list CSV is empty: {p_csv}')

    start = 0
    first_cell = rows[0][0].strip().upper() if rows[0] else ''
    if first_cell == 'SYMBOL':
        start = 1

    tickers: list[str] = []
    seen: set[str] = set()
    for row in rows[start:]:
        if not row:
            continue
        sym = row[0].strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        tickers.append(sym)

    if not tickers:
        raise CommandError(f'No tickers parsed from symbol list CSV: {p_csv}')

    return tickers


def _ensure_active_symbols(tickers: list[str], stdout: OutputWrapper) -> None:
    """Create missing ``Symbol`` rows so ingest can attach bars."""
    existing = set(Symbol.objects.filter(symbol__in=tickers).values_list('symbol', flat=True))
    missing = set(tickers) - existing
    if not missing:
        return
    Symbol.objects.bulk_create(
        [
            Symbol(
                symbol=sym,
                exchange='UNKNOWN',
                company_name='',
                is_active=True,
            )
            for sym in sorted(missing)
        ],
    )
    stdout.write(f'Created missing symbols: {sorted(missing)}')


class Command(BaseCommand):
    help = (
        'Fetch bars from Alpaca and append to market_bars. '
        'Tickers come from SYMBOL_LIST_CSV_FILENAME under trading/data/symbols/. '
        'Default window uses BACKFILL_START/BACKFILL_END in this module; '
        '--start/--end override those strings when passed.'
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            '--interval',
            type=int,
            default=alpaca_bars_mod.DEFAULT_BAR_SIZE_MINUTES,
            help='Bar size in minutes (default: %(default)s)',
        )
        parser.add_argument(
            '--start',
            default=None,
            help='Override BACKFILL_START (ISO); omit to use module constant or watermark when it is None.',
        )
        parser.add_argument(
            '--end',
            default=None,
            help='Override BACKFILL_END (ISO); omit to use module constant or now when it is None.',
        )
        parser.add_argument(
            '--chunk-days',
            type=int,
            default=None,
            metavar='N',
            help='Split each symbol’s Alpaca window into N-day chunks (large backfills)',
        )

    def handle(self, *args, **options) -> None:
        interval = options['interval']
        chunk_calendar_days = options['chunk_days']
        start_raw = options['start'] if options['start'] is not None else BACKFILL_START
        end_raw = options['end'] if options['end'] is not None else BACKFILL_END
        start = _parse_iso_bound(start_raw)
        end = _parse_iso_bound(end_raw)

        p_symbol_csv = p_symbols_dir / SYMBOL_LIST_CSV_FILENAME
        tickers = _load_tickers_from_csv_file(p_symbol_csv)
        _ensure_active_symbols(tickers, self.stdout)
        qs = Symbol.objects.active().filter(symbol__in=tickers)

        df = qs.update_from_data_source(
            interval=interval,
            start=start,
            end=end,
            chunk_calendar_days=chunk_calendar_days,
        )
        self.stdout.write(f'Rows appended: {len(df)}')
