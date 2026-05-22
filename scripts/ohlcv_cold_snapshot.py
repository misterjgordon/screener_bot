#!/usr/bin/env python3
"""Load cold OHLCV Parquet and print a pandas snapshot (schema-normalized rows).

Requires ``OHLCV_COLD_ROOT``. Paths match ``symbol_path``: ``{root}/1m/{SYMBOL}.parquet``.

If ``--symbol`` is omitted and exactly one ``*.parquet`` exists under ``1m/``, that symbol is used;
otherwise pass ``--symbol`` explicitly.

Pass ``--stats`` for a Polars scan summary (file size, row count, time span, null counts, bars per
UTC year, and **avg bars per ET calendar day** split into pre-market / RTH / after-hours / ``other``
using ``America/New_York``: 04:00–09:30 (PM), 09:30–16:00 (RTH), 16:00–20:00 (AH); outside 04:00–20:00
ET is ``other``. Naive ``Datetime`` columns are treated as UTC wall clock before conversion to ET;
UTC-aware columns use ``convert_time_zone`` only. Minutes-from-midnight uses ``Int64`` so
``hour * 60 + minute`` does not overflow Polars' small integer dtypes.

Example::

    export OHLCV_COLD_ROOT=/Users/joel/Data/equities
    uv run --frozen python scripts/ohlcv_cold_snapshot.py --list-symbols
    uv run --frozen python scripts/ohlcv_cold_snapshot.py --symbol HIMS --stats
    uv run --frozen python scripts/ohlcv_cold_snapshot.py --symbol HIMS --head 25
    uv run --frozen python -c "import polars as pl; from trading.storage.ohlcv.ohlcv_paths import symbol_path; p = str(symbol_path('HIMS')); print(pl.scan_parquet(p).select(pl.col('timestamp').min().alias('ts_min'), pl.col('timestamp').max().alias('ts_max'), pl.len().alias('rows')).collect())"
"""

import argparse
import sys
from pathlib import Path
from typing import cast

import pandas as pd
import polars as pl
from polars.datatypes import Datetime

from trading import config as cf
from trading.storage.ohlcv.ohlcv_paths import require_p_ohlcv_cold_root
from trading.storage.ohlcv.ohlcv_paths import symbol_path
from trading.storage.ohlcv.ohlcv_prepare import validate_and_prepare

_p_repo = Path(__file__).resolve().parent.parent
if str(_p_repo) not in sys.path:
    sys.path.insert(0, str(_p_repo))


def _symbols_with_parquet(p_cold_root: Path) -> list[str]:
    p_1m = p_cold_root / '1m'
    if not p_1m.is_dir():
        return []
    return sorted(p.stem.upper() for p in p_1m.glob('*.parquet') if p.is_file())


def _resolve_symbol_arg(raw: str | None, p_cold_root: Path) -> str:
    available = _symbols_with_parquet(p_cold_root)
    if raw:
        sym = raw.strip().upper()
        p_try = symbol_path(sym, interval_minutes=1)
        if p_try.is_file():
            return sym
        msg = f'No Parquet for {sym!r} at {p_try}. Available: {available[:20]}'
        if len(available) > 20:
            msg += f' … ({len(available)} total)'
        raise SystemExit(msg)
    if len(available) == 1:
        return available[0]
    if not available:
        raise SystemExit(f'No *.parquet under {p_cold_root / "1m"}; ingest or pass --symbol')
    raise SystemExit(
        f'Multiple symbols under 1m/ ({len(available)}); pass --symbol (e.g. {available[0]})',
    )


def _timestamp_to_et_expr(*, path_s: str) -> pl.Expr:
    """``timestamp`` → America/New_York; naive columns get ``replace_time_zone('UTC')`` first."""
    sch = pl.scan_parquet(path_s).collect_schema()
    ts_dtype = sch['timestamp']
    col = pl.col('timestamp')
    if isinstance(ts_dtype, Datetime) and ts_dtype.time_zone is None:
        return col.dt.replace_time_zone('UTC').dt.convert_time_zone('America/New_York')
    if isinstance(ts_dtype, Datetime):
        return col.dt.convert_time_zone('America/New_York')
    return col.cast(pl.Datetime('us', 'UTC')).dt.convert_time_zone('America/New_York')


def _print_cold_parquet_stats(*, sym: str, p_parquet: Path) -> None:
    """Polars scan: size, counts, span, nulls, UTC year histogram, ET session-day averages.

    ET session buckets use ``America/New_York`` (see module docstring). ``mins_et`` must use
    wide integer arithmetic: ``dt.hour()`` / ``dt.minute()`` are small dtypes; ``hour * 60`` overflows
    before 10:00 ET if not cast to ``Int64``.
    """
    path_s = str(p_parquet)
    size_mb = p_parquet.stat().st_size / (1024 * 1024)
    # Minutes from midnight ET: 04:00=240, 09:30=570, 16:00=960, 20:00=1200
    _m_pm0 = 4 * 60
    _m_rth0 = 9 * 60 + 30
    _m_ah0 = 16 * 60
    _m_ah1 = 20 * 60

    lf = pl.scan_parquet(path_s)
    overview = cast(
        'pl.DataFrame',
        lf.select(
            pl.len().alias('rows'),
            pl.col('timestamp').min().alias('ts_min'),
            pl.col('timestamp').max().alias('ts_max'),
        ).collect(),
    )
    row0 = overview.row(0, named=True)
    n_rows = int(row0['rows'])
    ts_min = row0['ts_min']
    ts_max = row0['ts_max']

    nulls = cast(
        'pl.DataFrame',
        pl.scan_parquet(path_s).select(pl.all().null_count()).collect(),
    )
    null_parts: list[str] = []
    for col in nulls.columns:
        v = int(nulls[col][0])
        if v != 0:
            null_parts.append(f'{col}={v:,}')
    null_summary = ', '.join(null_parts) if null_parts else '(none)'

    by_year = cast(
        'pl.DataFrame',
        pl.scan_parquet(path_s)
        .with_columns(pl.col('timestamp').dt.year().alias('year'))
        .group_by('year')
        .len()
        .sort('year')
        .collect(),
    )

    distinct_df = cast(
        'pl.DataFrame',
        pl.scan_parquet(path_s)
        .select(pl.col('timestamp').dt.date().n_unique().alias('distinct_utc_dates'))
        .collect(),
    )
    distinct_dates = int(distinct_df['distinct_utc_dates'][0])
    avg_bars_per_day = n_rows / distinct_dates if distinct_dates else 0.0

    et_stats = cast(
        'pl.DataFrame',
        pl.scan_parquet(path_s)
        .with_columns(_timestamp_to_et_expr(path_s=path_s).alias('ts_et'))
        .with_columns(
            (
                pl.col('ts_et').dt.hour().cast(pl.Int64) * 60
                + pl.col('ts_et').dt.minute().cast(pl.Int64)
            ).alias('mins_et'),
        )
        .with_columns(
            pl.when((pl.col('mins_et') >= _m_pm0) & (pl.col('mins_et') < _m_rth0))
            .then(pl.lit('PM'))
            .when((pl.col('mins_et') >= _m_rth0) & (pl.col('mins_et') < _m_ah0))
            .then(pl.lit('RTH'))
            .when((pl.col('mins_et') >= _m_ah0) & (pl.col('mins_et') < _m_ah1))
            .then(pl.lit('AH'))
            .otherwise(pl.lit('other'))
            .alias('et_segment'),
        )
        .with_columns(pl.col('ts_et').dt.date().alias('et_date'))
        .group_by(['et_segment', 'et_date'])
        .len()
        .group_by('et_segment')
        .agg(
            pl.col('len').sum().alias('bars'),
            pl.len().alias('et_days_with_bars'),
        )
        .with_columns((pl.col('bars') / pl.col('et_days_with_bars')).alias('avg_bars_per_et_day'))
        .sort('et_segment')
        .collect(),
    )

    print(f'symbol = {sym}')
    print(f'path = {p_parquet}')
    print(f'file_size_mb = {size_mb:.2f}')
    print(f'rows = {n_rows:,}')
    print(f'ts_min = {ts_min}')
    print(f'ts_max = {ts_max}')
    print(f'distinct_utc_dates = {distinct_dates:,}')
    print(f'avg_bars_per_utc_date = {avg_bars_per_day:.2f}')
    print(
        'avg_bars_per_et_day_by_segment (America/New_York; '
        'PM=04:00–09:30, RTH=09:30–16:00, AH=16:00–20:00, other=outside; '
        'timestamps: naive → UTC then ET; aware-UTC → ET only; '
        'denominator = ET dates with ≥1 bar in that segment):',
    )
    print(et_stats)
    print(f'null_counts_nonzero = {null_summary}')
    print('rows_by_utc_year:')
    print(by_year)


def main() -> int:
    parser = argparse.ArgumentParser(description='Print cold OHLCV Parquet as a normalized DataFrame snapshot')
    parser.add_argument('--symbol', default=None, help='Ticker (default: only symbol if one file under 1m/)')
    parser.add_argument(
        '--list-symbols',
        action='store_true',
        help='List tickers that have a 1m Parquet file and exit',
    )
    parser.add_argument(
        '--head',
        type=int,
        default=25,
        metavar='N',
        help='Number of rows to print from the start after sort (default 25)',
    )
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Print scan summary (size, rows, span, nulls, UTC year + ET PM/RTH/AH day averages) and exit',
    )
    args = parser.parse_args()

    if not cf.OHLCV_COLD_ROOT.strip():
        raise SystemExit('Set OHLCV_COLD_ROOT to your cold Parquet root')

    p_cold_root = require_p_ohlcv_cold_root()
    if args.list_symbols:
        names = _symbols_with_parquet(p_cold_root)
        print(f'cold_root = {p_cold_root}')
        print(f'symbols_with_parquet ({len(names)}):')
        for name in names:
            print(name)
        return 0

    sym = _resolve_symbol_arg(args.symbol, p_cold_root)
    p_parquet = symbol_path(sym, interval_minutes=1)

    if not p_parquet.is_file():
        raise SystemExit(f'Missing file: {p_parquet}')

    if args.stats:
        _print_cold_parquet_stats(sym=sym, p_parquet=p_parquet)
        return 0

    n_show = max(1, min(args.head, 50_000))
    df_raw = pd.read_parquet(p_parquet)
    df_norm = validate_and_prepare(df_raw)
    df_norm = df_norm.sort_values(['symbol', 'timestamp']).reset_index(drop=True)
    df_view = df_norm.head(n_show)

    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 200)
    pd.set_option('display.max_colwidth', 32)

    print(f'symbol = {sym}')
    print(f'path = {p_parquet}')
    print(f'total_rows = {len(df_norm):,} | showing_head = {len(df_view):,}')
    print()
    print(df_view.to_string(index=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
