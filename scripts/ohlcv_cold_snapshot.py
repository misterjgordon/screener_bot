#!/usr/bin/env python3
"""Load cold OHLCV Parquet and print a pandas snapshot (schema-normalized rows).

Requires ``OHLCV_COLD_ROOT``. Paths match ``symbol_path``: ``{root}/1m/{SYMBOL}.parquet``.

If ``--symbol`` is omitted and exactly one ``*.parquet`` exists under ``1m/``, that symbol is used;
otherwise pass ``--symbol`` explicitly.

Example::

    export OHLCV_COLD_ROOT=/Users/joel/Data/equities
    uv run --frozen python scripts/ohlcv_cold_snapshot.py --list-symbols
    uv run --frozen python scripts/ohlcv_cold_snapshot.py --symbol NVDA --head 25
    uv run --frozen python -c "import pandas as pd; from trading.storage.ohlcv.ohlcv_paths import symbol_path; from trading.storage.ohlcv.ohlcv_prepare import validate_and_prepare; df = validate_and_prepare(pd.read_parquet(symbol_path('NVDA'))); print(df.head(20))"
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

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
