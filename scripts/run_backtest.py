#!/usr/bin/env python3
"""Run a strategy backtest over cold 1m Parquet: load → indicators → signals → sim.

Requires ``OHLCV_COLD_ROOT`` pointing at the parent of ``1m/`` and ``1440m/`` (not the
``1m`` folder itself). Strategy YAML lives under ``strategies/configs/`` or pass a path.

Default universe (no ``--symbol`` / ``--symbols-file``): every ``*.parquet`` stem under
``{OHLCV_COLD_ROOT}/1m/``. Use ``--symbol`` or ``--symbols-file`` to narrow.

Example::

    export OHLCV_COLD_ROOT=/Users/joel/Data/equities
    uv run --frozen python scripts/run_backtest.py --strategy ema_cross --symbol AAPL \\
        --start 2026-05-15 --end 2026-05-15
    uv run --frozen python scripts/run_backtest.py --strategy ema_cross --start 2023-05-15 \\
        --end 2026-05-15 --summary-only
    uv run --frozen python scripts/run_backtest.py --strategy ema_cross --symbol AAPL \\
        --start 2026-05-15 --end 2026-05-15
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

_p_repo = Path(__file__).resolve().parent.parent
if str(_p_repo) not in sys.path:
    sys.path.insert(0, str(_p_repo))

from dotenv import dotenv_values  # noqa: E402

_DOC_PLACEHOLDER_COLD_ROOTS = frozenset({'/path/to/cold', '/path/to/ohlcv'})


def _apply_dotenv_cold_root_when_unset_or_placeholder() -> None:
    """Use repo ``.env`` when the shell still has a docs placeholder ``OHLCV_COLD_ROOT``."""
    current = os.environ.get('OHLCV_COLD_ROOT', '').strip()
    if current and current not in _DOC_PLACEHOLDER_COLD_ROOTS:
        return
    p_env = _p_repo / '.env'
    if not p_env.is_file():
        return

    root = (dotenv_values(p_env).get('OHLCV_COLD_ROOT') or '').strip()
    if root:
        os.environ['OHLCV_COLD_ROOT'] = root


_apply_dotenv_cold_root_when_unset_or_placeholder()

from backtesting.bt_config import DEFAULT_WARMUP_BARS  # noqa: E402
from backtesting.run.backtest_run import format_backtest_summary  # noqa: E402
from backtesting.run.backtest_run import run_backtest  # noqa: E402
from backtesting.strategy.strategy_loader import resolve_strategy_config_path  # noqa: E402
from backtesting.strategy.universe_resolver import UniverseResolveResult  # noqa: E402
from backtesting.strategy.universe_resolver import resolve_universe_symbols  # noqa: E402
from trading import config as cf  # noqa: E402
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE  # noqa: E402
from trading.storage.ohlcv.ohlcv_paths import require_p_ohlcv_cold_root  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Backtest a strategy on cold 1m bars: IndicatorPipeline, ConditionPipeline, '
            'SignalPipeline, then PortfolioSimulator.'
        ),
    )
    parser.add_argument(
        '--strategy',
        required=True,
        metavar='ID_OR_PATH',
        help='Strategy id (e.g. ema_cross) or path to a YAML file',
    )
    parser.add_argument(
        '--start',
        type=date.fromisoformat,
        default=OHLCV_DEFAULT_INGEST_END_DATE,
        help=f'ET session start date inclusive (default: {OHLCV_DEFAULT_INGEST_END_DATE.isoformat()})',
    )
    parser.add_argument(
        '--end',
        type=date.fromisoformat,
        default=OHLCV_DEFAULT_INGEST_END_DATE,
        help=f'ET session end date inclusive (default: {OHLCV_DEFAULT_INGEST_END_DATE.isoformat()})',
    )
    parser.add_argument(
        '--symbol',
        action='append',
        dest='symbols',
        metavar='TICKER',
        help='Ticker to include (repeatable). Default: all 1m/*.parquet under OHLCV_COLD_ROOT',
    )
    parser.add_argument(
        '--symbols-file',
        type=Path,
        metavar='PATH',
        help='CSV symbol list instead of default (all 1m/*.parquet stems)',
    )
    parser.add_argument(
        '--summary-only',
        action='store_true',
        help='Print aggregate stats only (skip per-trade trades_detail lines)',
    )
    parser.add_argument(
        '--warmup-bars',
        type=int,
        default=DEFAULT_WARMUP_BARS,
        help=f'Warmup 1m bars before analysis window (default: {DEFAULT_WARMUP_BARS})',
    )
    parser.add_argument(
        '--indicators',
        nargs='+',
        metavar='INDICATOR_ID',
        help='Override indicator pipeline ids (default: strategy + catalog defaults)',
    )
    parser.add_argument(
        '--condition-ids',
        nargs='+',
        metavar='CONDITION_ID',
        help='Optional condition registry ids (default: strategy conditions: list)',
    )
    return parser.parse_args()


def _resolve_universe_from_args(args: argparse.Namespace) -> UniverseResolveResult:
    """CLI universe: --symbol → --symbols-file → all cold 1m/*.parquet stems."""
    if args.symbols:
        return resolve_universe_symbols(explicit_symbols=tuple(args.symbols))
    if args.symbols_file is not None:
        return resolve_universe_symbols(p_symbol_list=args.symbols_file)
    return resolve_universe_symbols(use_cold_dir=True)


def main() -> None:
    if not cf.OHLCV_COLD_ROOT.strip():
        raise SystemExit(
            'Set OHLCV_COLD_ROOT to the cold OHLCV Parquet root (parent of 1m/, not 1m/ itself)',
        )

    args = _parse_args()
    if args.start > args.end:
        raise SystemExit(f'--start must be on or before --end (got {args.start} .. {args.end})')

    indicator_ids = tuple(args.indicators) if args.indicators else None
    condition_ids = tuple(args.condition_ids) if args.condition_ids else None

    p_cold_root = require_p_ohlcv_cold_root()
    p_strategy = resolve_strategy_config_path(args.strategy)
    universe_resolve = _resolve_universe_from_args(args)
    print(
        f'universe = {len(universe_resolve.symbols)} symbol(s) '
        f'[{universe_resolve.source}: {universe_resolve.source_detail}]',
    )
    print(f'symbols = {list(universe_resolve.symbols)}')

    try:
        result = run_backtest(
            strategy_id_or_path=args.strategy,
            start=args.start,
            end=args.end,
            universe_resolve=universe_resolve,
            warmup_bars=args.warmup_bars,
            indicator_ids=indicator_ids,
            condition_ids=condition_ids,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if result.load_report.loaded_count == 0:
        print(format_backtest_summary(
            result,
            p_cold_root=p_cold_root,
            et_start=args.start,
            et_end=args.end,
            p_strategy_config=p_strategy,
            summary_only=args.summary_only,
        ))
        raise SystemExit('No symbols loaded; cannot simulate')

    print(format_backtest_summary(
        result,
        p_cold_root=p_cold_root,
        et_start=args.start,
        et_end=args.end,
        p_strategy_config=p_strategy,
        summary_only=args.summary_only,
    ))


if __name__ == '__main__':
    main()
