"""Polars reads over cold-store per-ticker Parquet files."""

from datetime import UTC
from datetime import datetime
from typing import cast

import polars as pl

from trading.storage.ohlcv.ohlcv_paths import symbol_path


def load_bars_1m(
        symbols: list[str],
        start: datetime,
        end: datetime,
        *,
        interval_minutes: int = 1,
) -> pl.DataFrame:
    """Load 1-minute bars for ``symbols`` within ``[start, end]`` on ``timestamp``.

    Missing Parquet files for a symbol are skipped.
    ``start`` / ``end`` are interpreted in UTC if naive.
    """
    paths: list[str] = []
    for sym in symbols:
        p_parquet = symbol_path(sym.strip(), interval_minutes=interval_minutes)
        if p_parquet.is_file():
            paths.append(str(p_parquet))

    if not paths:
        return pl.DataFrame(
            schema={
                'symbol': pl.String,
                'timestamp': pl.Datetime('us', 'UTC'),
                'open': pl.Float32,
                'high': pl.Float32,
                'low': pl.Float32,
                'close': pl.Float32,
                'volume': pl.Int32,
                'vwap': pl.Float32,
            }
        )

    start_utc = start.astimezone(UTC) if start.tzinfo else start.replace(tzinfo=UTC)
    end_utc = end.astimezone(UTC) if end.tzinfo else end.replace(tzinfo=UTC)

    lf = pl.scan_parquet(paths)
    return cast(
        'pl.DataFrame',
        lf.filter((pl.col('timestamp') >= pl.lit(start_utc)) & (pl.col('timestamp') <= pl.lit(end_utc)))
        .sort(['symbol', 'timestamp'])
        .collect(),
    )
