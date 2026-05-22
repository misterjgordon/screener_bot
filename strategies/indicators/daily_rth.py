"""RTH-only daily OHLCV aggregates from intraday or daily Parquet bars."""

import pandas as pd

from strategies.indicators.trading_date import trading_date_series_utc
from strategies.utils import RTH_END
from strategies.utils import RTH_START
from trading.market_timezones import exchange_timezone_name
from trading.market_timezones import timestamp_utc_series_to_zone

_EMPTY_DAILY_COLUMNS: list[str] = ['trading_date', 'open', 'high', 'low', 'close', 'volume']


def aggregate_rth_daily_from_intraday(df_intraday: pd.DataFrame) -> pd.DataFrame:
    """Build one RTH daily row per ``trading_date`` from minute (or sub-daily) bars.

    PM/AH bars are excluded. ``volume`` sums RTH minutes; OHLC uses RTH bars only.
    """
    if df_intraday.empty:
        return pd.DataFrame({col: pd.Series(dtype='float64') for col in _EMPTY_DAILY_COLUMNS})

    df_work = df_intraday.copy()
    ts_et = timestamp_utc_series_to_zone(df_work.timestamp, exchange_timezone_name())
    mins = ts_et.dt.hour * 60 + ts_et.dt.minute
    rth_lo = RTH_START.hour * 60 + RTH_START.minute
    rth_hi = RTH_END.hour * 60 + RTH_END.minute
    rth_mask = (mins >= rth_lo) & (mins < rth_hi)
    df_work = df_work.loc[rth_mask].copy()
    if df_work.empty:
        return pd.DataFrame({col: pd.Series(dtype='float64') for col in _EMPTY_DAILY_COLUMNS})

    df_work = df_work.assign(trading_date=trading_date_series_utc(df_work.timestamp))
    grouped = df_work.groupby('trading_date', sort=True)
    return grouped.agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
    ).reset_index()
