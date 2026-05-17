"""Atomic merge-and-write for per-symbol OHLCV Parquet files."""

import logging
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from trading.storage.ohlcv.ohlcv_schema import OHLCV_COLD_PARQUET_SCHEMA

log = logging.getLogger(__name__)


def merge_and_write(p_parquet: Path, df_new: pd.DataFrame) -> None:
    """Write a Parquet file atomically, merging with existing data.

    Deduplicates on (symbol, timestamp), keeping last (new data wins).

    Parameters
    ----------
    p_parquet
        Target ``.parquet`` path (parent dirs created as needed).
    df_new
        New rows to write/merge; must already match cold Parquet dtypes (callers use
        ``validate_and_prepare`` before merge). Existing on-disk rows are not re-parsed.
    """
    if df_new.empty:
        return

    if p_parquet.exists():
        df_existing = pd.read_parquet(p_parquet)
        df_merged = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df_merged = df_new

    df_merged = df_merged \
        .drop_duplicates(subset=['symbol', 'timestamp'], keep='last') \
        .sort_values(['symbol', 'timestamp']) \
        .reset_index(drop=True)

    table = pa.Table.from_pandas(df_merged, schema=OHLCV_COLD_PARQUET_SCHEMA, preserve_index=False)

    p_parquet.parent.mkdir(parents=True, exist_ok=True)
    p_tmp = p_parquet.with_suffix('.parquet.tmp')
    pq.write_table(table, p_tmp, compression='snappy')
    p_tmp.replace(p_parquet)

    log.info('Wrote %s rows to %s', f'{len(df_merged):,}', p_parquet)
