"""Multi-index views for strategy code that prefers naive wall times (jambot-style)."""

from datetime import datetime

import pandas as pd

from trading.storage.ohlcv.ohlcv_schema import OHLCV_COLS


def empty_multi_index_df() -> pd.DataFrame:
    """Empty DataFrame matching multi-index OHLCV output shape."""
    return pd.DataFrame(
        columns=pd.Index(list(OHLCV_COLS)),
        index=pd.MultiIndex.from_tuples([], names=['symbol', 'timestamp']),
    ).astype(float)


def to_multi_index(df_bars: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    """Strip timezone for downstream code; filter ``timestamp`` to ``[start, end]`` inclusive.

    Parameters
    ----------
    df_bars
        Raw DataFrame with symbol, timestamp columns (UTC-aware)
    start
        Inclusive start (UTC-aware)
    end
        Inclusive end (UTC-aware)

    Returns
    -------
    pd.DataFrame
        MultiIndex [symbol, timestamp (naive)], float OHLCV columns
    """
    df_local = df_bars.assign(timestamp=df_bars.timestamp.dt.tz_localize(None))
    start_naive = start.replace(tzinfo=None)
    end_naive = end.replace(tzinfo=None)

    df_filtered = df_local[df_local.timestamp.between(start_naive, end_naive)]

    return df_filtered \
        .set_index(['symbol', 'timestamp']) \
        .sort_index() \
        .loc[:, list(OHLCV_COLS)] \
        .astype(float)
