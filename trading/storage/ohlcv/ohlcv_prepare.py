"""Slice and validate bar DataFrames before Parquet write."""


from typing import TYPE_CHECKING

from trading.storage.ohlcv.ohlcv_schema import OHLCV_COLD_PARQUET_COLUMNS

if TYPE_CHECKING:
    import pandas as pd


def validate_and_prepare(df_bars: 'pd.DataFrame') -> 'pd.DataFrame':
    """Validate columns and normalize timestamps for cold Parquet.

    Parameters
    ----------
    df_bars
        Must include cold-store columns (``symbol``, ``timestamp``, OHLCV, ``vwap``).
        Extra columns (e.g. ``interval`` from ingest) are ignored.

    Returns
    -------
    pd.DataFrame
        Columns in ``OHLCV_COLD_PARQUET_COLUMNS`` order. ``timestamp`` is taken as-is
        (callers must supply UTC-aware instants or Parquet-round-trip datetimes). Price
        fields and ``vwap`` cast to ``float32``; ``volume`` rounded to whole shares then
        ``int32`` (``NaN`` volume becomes ``0``). No ``interval`` column (bar size comes
        from the ``{N}m/`` path segment).
    """
    required = set(OHLCV_COLD_PARQUET_COLUMNS)
    missing = required - set(df_bars.columns)
    if missing:
        msg = f'Bars DataFrame missing columns: {sorted(missing)}'
        raise ValueError(msg)

    df_work = df_bars.loc[:, list(OHLCV_COLD_PARQUET_COLUMNS)]

    assign_kw: dict[str, pd.Series] = {
        'timestamp': df_work.timestamp,
    }
    for col in ('open', 'high', 'low', 'close', 'vwap'):
        assign_kw[col] = df_work[col].astype('float32')

    assign_kw['volume'] = df_work.volume.round().fillna(0).astype('int32')

    return df_work.assign(**assign_kw).loc[:, list(OHLCV_COLD_PARQUET_COLUMNS)]
