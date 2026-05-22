"""Exchange trading calendar date from UTC bar timestamps."""

import pandas as pd

from trading.market_timezones import exchange_zone


def trading_date_series_utc(timestamp_utc: pd.Series) -> pd.Series:
    """Map UTC bar timestamps to US equity session calendar dates (Eastern)."""
    ts = pd.to_datetime(timestamp_utc, utc=True)
    return ts.dt.tz_convert(exchange_zone()).dt.date
