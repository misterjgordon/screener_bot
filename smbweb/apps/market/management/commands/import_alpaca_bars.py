"""Load OHLCV bars from Alpaca into ``market_bars`` for selected or all active symbols."""

import argparse
from datetime import UTC
from datetime import datetime as dt

from django.core.management.base import BaseCommand

from smbweb.apps.market.models import Symbol
from trading.integrations.alpaca_bars import DEFAULT_BAR_SIZE_MINUTES
"""
uv run --frozen python smbweb/manage.py import_alpaca_bars
"""
# Backfill window controls (edit in-script; None keeps incremental behavior).
# Example: BACKFILL_START = '2026-04-01T09:30:00-04:00'
BACKFILL_START: str | None = '2026-04-16T09:30:00-04:00'
BACKFILL_END: str | None = '2026-04-16T09:40:00-04:00'
# Symbol controls (edit in-script; empty list uses all active symbols).
BACKFILL_SYMBOLS: list[str] = ['MYSE', 'IONQ', 'HIMS']


def _parse_iso_bound(value: str | None) -> dt | None:
    """Parse ISO datetime string and normalize to timezone-aware UTC."""
    if value is None:
        return None
    parsed = dt.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class Command(BaseCommand):
    help = 'Fetch bars from Alpaca and append to market_bars for active symbols'

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            '--interval',
            type=int,
            default=DEFAULT_BAR_SIZE_MINUTES,
            help='Bar size in minutes (default: %(default)s)',
        )

    def handle(self, *args, **options) -> None:
        interval = options['interval']
        start = _parse_iso_bound(BACKFILL_START)
        end = _parse_iso_bound(BACKFILL_END)

        qs = Symbol.objects.active()
        if BACKFILL_SYMBOLS:
            tickers = [s.strip().upper() for s in BACKFILL_SYMBOLS if s.strip()]
            existing = set(Symbol.objects.filter(symbol__in=tickers).values_list('symbol', flat=True))
            missing = set(tickers) - existing
            if missing:
                Symbol.objects.bulk_create(
                    [
                        Symbol(
                            symbol=sym,
                            exchange='UNKNOWN',
                            company_name='',
                            is_active=True,
                        )
                        for sym in sorted(missing)
                    ]
                )
                self.stdout.write(f'Created missing symbols: {sorted(missing)}')
            qs = qs.filter(symbol__in=tickers)

        df = qs.update_from_data_source(interval=interval, start=start, end=end)
        self.stdout.write(f'Rows appended: {len(df)}')
