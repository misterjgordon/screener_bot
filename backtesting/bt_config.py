"""Backtest engine defaults (env overrides in later steps)."""

from datetime import time

from trading.market_timezones import display_timezone_name

DEFAULT_INTERVAL_MINUTES = 1
DEFAULT_WARMUP_BARS = 100

# Cushion before analysis start when reading Parquet (captures PM on first session day).
PM_LOAD_CUSHION_HOURS = 18

# Parquet path segment for daily bars: ``{OHLCV_COLD_ROOT}/1440m/{SYMBOL}.parquet``
DAILY_INTERVAL_MINUTES = 1440

# Desk / CLI bar tables use :func:`display_timezone_name` (``market_timezones.yaml``).
BACKTEST_DISPLAY_TIMEZONE_NAME = display_timezone_name()

# Half-open equity session day on the display clock (e.g. 04:00 PT → next 04:00).
DISPLAY_EQUITY_DAY_START = time(4, 0)
