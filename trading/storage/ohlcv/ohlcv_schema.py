"""Canonical OHLCV bar column contract for Parquet cold store and ingest DataFrames.

Ingest frames (Alpaca, Massive REST) include ``interval`` (minutes). The cold Parquet
layout omits it: bar size is implied by ``{root}/{interval}m/{SYMBOL}.parquet``. On disk,
``open``/``high``/``low``/``close``/``vwap`` use ``float32``; ``volume`` is ``int32`` (whole shares).

Column names for the full ingest row match ``smbweb.apps.market.models.Bars`` (+ ``symbol``
as ticker string). ``SymbolQuerySet.default_bar_columns`` uses ``BAR_FRAME_COLUMNS``;
Parquet schema literals live here so cold storage stays Django-free.
"""

from typing import Final

import pandas as pd
import pyarrow as pa

# OHLC + volume only (multi-index / jambot-style views); single literal for price fields.
BAR_FRAME_OHLCV_COLUMNS: Final[tuple[str, ...]] = (
    'open',
    'high',
    'low',
    'close',
    'volume',
)

# Ingest / API / Django-shaped row (interval explicit for DB and fetch helpers).
BAR_FRAME_COLUMNS: Final[tuple[str, ...]] = (
    'symbol',
    'interval',
    'timestamp',
    *BAR_FRAME_OHLCV_COLUMNS,
    'vwap',
)

OHLCV_COLS: Final[tuple[str, ...]] = BAR_FRAME_OHLCV_COLUMNS

# Per-symbol Parquet under ``{root}/{N}m/{SYM}.parquet`` — no ``interval`` column.
OHLCV_COLD_PARQUET_COLUMNS: Final[tuple[str, ...]] = (
    'symbol',
    'timestamp',
    *BAR_FRAME_OHLCV_COLUMNS,
    'vwap',
)

OHLCV_COLD_PARQUET_SCHEMA: Final[pa.Schema] = pa.schema(
    [
        ('symbol', pa.string()),
        ('timestamp', pa.timestamp('us', tz='UTC')),
        ('open', pa.float32()),
        ('high', pa.float32()),
        ('low', pa.float32()),
        ('close', pa.float32()),
        ('volume', pa.int32()),
        ('vwap', pa.float32()),
    ]
)


def empty_bars_dataframe() -> pd.DataFrame:
    """Return an empty bars frame with stable dtypes (matches populated Alpaca-shaped rows)."""
    return pd.DataFrame(
        {
            'symbol': pd.Series(dtype='string'),
            'interval': pd.Series(dtype='int64'),
            'timestamp': pd.Series(dtype='datetime64[us, UTC]'),
            'open': pd.Series(dtype='float64'),
            'high': pd.Series(dtype='float64'),
            'low': pd.Series(dtype='float64'),
            'close': pd.Series(dtype='float64'),
            'volume': pd.Series(dtype='float64'),
            'vwap': pd.Series(dtype='Float64'),
        }
    )
