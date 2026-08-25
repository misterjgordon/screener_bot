"""Aggregate 1-min OHLCV bars to a higher interval for multi-timeframe indicators."""

import pandas as pd

_OHLCV_AGG: dict[str, str] = {
    'timestamp': 'first',
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last',
    'volume': 'sum',
}


def resample_to_interval(df_bars: pd.DataFrame, interval_minutes: int) -> pd.DataFrame:
    """Aggregate 1-min bars to a higher interval, respecting trading_date boundaries.

    Bucketing is sequential within each trading_date (not wall-clock aligned), so session
    boundaries are always respected regardless of bar start time.

    OHLCV columns follow standard rules (first open, max high, min low, last close, sum
    volume). ``timestamp`` and string/object columns use first. All other numeric columns
    use last (most recent value within the bucket, e.g. indicator columns).

    Parameters
    ----------
    df_bars:
        DataFrame with at minimum: timestamp, open, high, low, close, volume, trading_date.
        May include pre-computed indicator columns (session, rvol, atr, etc.).
    interval_minutes:
        Target interval in minutes. Must be >= the source interval.
    """
    df = df_bars.sort_values(['trading_date', 'timestamp']).copy()
    df['_bucket'] = df.groupby('trading_date').cumcount() // interval_minutes

    group_keys = {'trading_date', '_bucket'}
    agg: dict[str, str] = {}
    for col in df.columns:
        if col in group_keys:
            continue
        if col in _OHLCV_AGG:
            agg[col] = _OHLCV_AGG[col]
        elif pd.api.types.is_numeric_dtype(df[col]):
            agg[col] = 'last'
        else:
            agg[col] = 'first'

    return (
        df.groupby(['trading_date', '_bucket'])
        .agg(agg)
        .reset_index()
        .drop(columns=['_bucket'])
    )
