"""Per-symbol Parquet layout: group rows and merge-write one file per ticker."""

from pathlib import Path
from typing import TYPE_CHECKING

from trading.storage.ohlcv.ohlcv_merge_write import merge_and_write
from trading.storage.ohlcv.ohlcv_paths import symbol_path
from trading.storage.ohlcv.ohlcv_prepare import validate_and_prepare

if TYPE_CHECKING:
    import pandas as pd


def write_bars(df_bars: 'pd.DataFrame', *, interval_minutes: int = 1) -> list[Path]:
    """Write bar data grouped by symbol (one merged Parquet file per ticker).

    Parameters
    ----------
    df_bars
        Ingest-shaped frame: ``symbol``, ``interval``, ``timestamp``, OHLCV, ``vwap``.
        ``interval`` is dropped before Parquet write (implied by ``{interval_minutes}m/`` path).
    interval_minutes
        Folder segment under the cold root (e.g. ``1m``).

    Returns
    -------
    list[Path]
        Paths of files written
    """
    if df_bars.empty:
        return []

    df_norm = validate_and_prepare(df_bars)
    paths_written: list[Path] = []

    if len(df_norm) > 0 and bool((df_norm.symbol == df_norm.symbol.iloc[0]).all()):
        sym = str(df_norm.symbol.iloc[0]).strip().upper()
        p_parquet = symbol_path(sym, interval_minutes=interval_minutes)
        merge_and_write(p_parquet, df_norm)
        paths_written.append(p_parquet)
        return paths_written

    for symbol, df_sym in df_norm.groupby('symbol', sort=False):
        sym = str(symbol).strip().upper()
        p_parquet = symbol_path(sym, interval_minutes=interval_minutes)
        merge_and_write(p_parquet, df_sym)
        paths_written.append(p_parquet)

    return paths_written
