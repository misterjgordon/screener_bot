"""Volume guardrails for cold OHLCV ingest (calendar span × symbol count)."""

from datetime import UTC
from datetime import date
from datetime import datetime

# Default cap on (inclusive UTC calendar days in range) × (number of symbols) unless
# scripts/ingest_ohlcv_cold.py is run with --allow-high-volume-ingest.
DEFAULT_INGEST_SYMBOL_DAY_BUDGET = 250

# Default Massive ingest window when ``--start`` / ``--end`` are omitted (UTC calendar days,
# inclusive of full last day for REST — same bounds as ``tests/test_ohlcv_bars_cold.py``).
OHLCV_DEFAULT_INGEST_START_DATE = date(2021, 5, 20)
OHLCV_DEFAULT_INGEST_END_DATE = date(2022, 5, 20)


def utc_calendar_span_inclusive_days(start: datetime, end: datetime) -> int:
    """Count UTC calendar days touched from ``start`` through ``end`` (inclusive)."""
    start_utc = start.astimezone(UTC) if start.tzinfo else start.replace(tzinfo=UTC)
    end_utc = end.astimezone(UTC) if end.tzinfo else end.replace(tzinfo=UTC)
    s_date = start_utc.date()
    e_date = end_utc.date()
    return max(1, (e_date - s_date).days + 1)


def symbol_day_ingest_cost(start: datetime, end: datetime, symbol_count: int) -> int:
    """Product of inclusive UTC calendar span and symbol count."""
    return utc_calendar_span_inclusive_days(start, end) * max(0, symbol_count)
