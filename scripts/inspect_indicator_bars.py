#!/usr/bin/env python3
"""Per-symbol bar table for one PT equity day (04:00–04:00) with indicator columns.

Loads cold 1m Parquet via :class:`~backtesting.io.cold_bar_source.ColdBarSource`, runs
:class:`~backtesting.indicators.indicator_pipeline.IndicatorPipeline`, and prints a display
frame: raw OHLC, ``datetime_pt`` (PT view of UTC ``timestamp``), and indicator columns in a
fixed-width aligned table (``cum_avg_vol`` etc. via :func:`~backtesting.frames.bar_price_round.inspect_bar_table_string`).
Pass ``--conditions`` to include strategy condition columns from
:class:`~backtesting.conditions.condition_pipeline.ConditionPipeline`.
Pass ``--strategy ema_cross`` to resolve indicator + condition ids from YAML (same merge as
:class:`~backtesting.universe.universe_loader.load_prepared_universe`). Header notes report
whether ``daily_bars`` / ``history_bars`` have enough prior sessions for adr/atr/rvol.

Example::

    # Prefer repo ``.env`` (OHLCV_COLD_ROOT) or: export OHLCV_COLD_ROOT=/Users/joel/Data/equities
    uv run --frozen python scripts/inspect_indicator_bars.py --symbol AAPL --date 2026-05-15
    uv run --frozen python scripts/inspect_indicator_bars.py --symbol AAPL --date 2026-05-15 --strategy ema_cross
    uv run --frozen python scripts/inspect_indicator_bars.py --symbol AAPL --date 2026-05-15 --end-time 13:00
"""

import argparse
import os
import sys
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
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

from typing import TYPE_CHECKING  # noqa: E402

from backtesting.bt_config import BACKTEST_DISPLAY_TIMEZONE_NAME  # noqa: E402
from backtesting.bt_config import DEFAULT_WARMUP_BARS  # noqa: E402
from backtesting.bt_config import DISPLAY_EQUITY_DAY_START  # noqa: E402
from backtesting.conditions.condition_pipeline import ConditionPipeline  # noqa: E402
from backtesting.conditions.condition_registry import CONDITION_REGISTRY  # noqa: E402
from backtesting.conditions.session_regime import SESSION_COLUMN  # noqa: E402
from backtesting.conditions.session_regime import SIGNAL_ELIGIBLE_COLUMN  # noqa: E402
from backtesting.frames.bar_price_round import inspect_bar_table_string  # noqa: E402
from backtesting.indicators.indicator_pipeline import IndicatorPipeline  # noqa: E402
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY  # noqa: E402
from backtesting.inspect.indicator_context import display_window_nan_columns  # noqa: E402
from backtesting.inspect.indicator_context import indicator_context_notes  # noqa: E402
from backtesting.io.cold_bar_source import ColdBarSource  # noqa: E402
from backtesting.signals.signal_columns import signal_diagnostic_column_names  # noqa: E402
from backtesting.signals.signal_pipeline import SignalPipeline  # noqa: E402
from backtesting.strategy.pipeline_ids import resolve_pipeline_condition_ids  # noqa: E402
from backtesting.strategy.pipeline_ids import resolve_pipeline_indicator_ids  # noqa: E402
from backtesting.strategy.strategy_loader import load_strategy_config  # noqa: E402
from trading import config as cf  # noqa: E402
from trading.market_timezones import display_timezone_name  # noqa: E402
from trading.market_timezones import display_zone  # noqa: E402
from trading.market_timezones import exchange_zone  # noqa: E402
from trading.market_timezones import timestamp_utc_series_to_zone  # noqa: E402
from trading.market_timezones import timezone_display_label  # noqa: E402
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE  # noqa: E402
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path  # noqa: E402
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file  # noqa: E402
from trading.storage.ohlcv.ohlcv_paths import require_p_ohlcv_cold_root  # noqa: E402
from trading.storage.ohlcv.ohlcv_paths import symbol_path  # noqa: E402

if TYPE_CHECKING:
    import pandas as pd

    from backtesting.strategy.strategy_config import StrategyConfig

DEFAULT_DISPLAY_START_TIME = time(6, 0)
DEFAULT_DISPLAY_END_TIME = time(12, 0)
OHLCV_TEST_SYMBOL_ENV = 'OHLCV_TEST_SYMBOL'
_INSPECT_SKIP_COLUMNS: frozenset[str] = frozenset({'symbol', 'trading_date'})
_BASE_DISPLAY_COLUMNS: tuple[str, ...] = (
    'time',
    'open',
    'high',
    'low',
    'close',
    'volume',
)


def _display_columns(
    indicator_ids: tuple[str, ...],
    condition_ids: tuple[str, ...],
    *,
    include_session_columns: bool,
    strategy: 'StrategyConfig | None' = None,
) -> tuple[str, ...]:
    """Base OHLCV + indicators + conditions + session + optional signal columns."""
    out: list[str] = list(_BASE_DISPLAY_COLUMNS)
    seen = set(out)
    for col in INDICATOR_REGISTRY.output_columns_for(indicator_ids):
        if col in seen:
            continue
        out.append(col)
        seen.add(col)
    for col in CONDITION_REGISTRY.output_columns_for(condition_ids):
        if col in seen:
            continue
        out.append(col)
        seen.add(col)
    if include_session_columns:
        for col in (SESSION_COLUMN, SIGNAL_ELIGIBLE_COLUMN):
            if col in seen:
                continue
            out.append(col)
            seen.add(col)
    if strategy is not None:
        trigger_ids = tuple(rule.id for rule in strategy.triggers)
        filter_ids = tuple(rule.id for rule in strategy.filters)
        for col in signal_diagnostic_column_names(
            trigger_ids,
            filter_ids,
            include_entry_columns=True,
        ):
            if col in seen:
                continue
            out.append(col)
            seen.add(col)
    return tuple(c for c in out if c not in _INSPECT_SKIP_COLUMNS)


def _display_equity_day_bounds(session_date_display: date) -> tuple[datetime, datetime]:
    """Half-open equity day on the display clock (default 04:00 PT → next 04:00)."""
    lo = datetime.combine(session_date_display, DISPLAY_EQUITY_DAY_START, tzinfo=display_zone())
    return lo, lo + timedelta(days=1)


def _exchange_analysis_dates_for_display_day(session_date_display: date) -> tuple[date, date]:
    """Exchange-calendar load range covering the display equity day window."""
    lo_display, hi_display = _display_equity_day_bounds(session_date_display)
    lo_et = lo_display.astimezone(exchange_zone()).date()
    hi_et = (hi_display - timedelta(microseconds=1)).astimezone(exchange_zone()).date()
    return lo_et, hi_et


def _mask_display_equity_day(timestamp_utc: 'pd.Series', session_date_display: date) -> 'pd.Series':
    lo_display, hi_display = _display_equity_day_bounds(session_date_display)
    ts_display = timestamp_utc_series_to_zone(timestamp_utc, display_timezone_name())
    return (ts_display >= lo_display) & (ts_display < hi_display)


def _parse_display_time(value: str) -> time:
    """Parse ``HH:MM`` clock time for the PT display window on ``--date``."""
    return time.fromisoformat(value)


def _display_print_bounds(
    session_date_display: date,
    start_time: time,
    end_time: time,
) -> tuple[datetime, datetime]:
    """Half-open print window ``[start_time, end_time)`` on ``session_date_display``."""
    lo = datetime.combine(session_date_display, start_time, tzinfo=display_zone())
    hi = datetime.combine(session_date_display, end_time, tzinfo=display_zone())
    return lo, hi


def _mask_display_print_window(
    timestamp_utc: 'pd.Series',
    session_date_display: date,
    start_time: time,
    end_time: time,
) -> 'pd.Series':
    """Bars whose display-zone timestamp falls in ``[start_time, end_time)``."""
    lo_display, hi_display = _display_print_bounds(session_date_display, start_time, end_time)
    ts_display = timestamp_utc_series_to_zone(timestamp_utc, display_timezone_name())
    return (ts_display >= lo_display) & (ts_display < hi_display)


def _missing_parquet_message(sym: str) -> str:
    p_expected = symbol_path(sym)
    p_root = require_p_ohlcv_cold_root()
    hint = ''
    if p_root.as_posix() in _DOC_PLACEHOLDER_COLD_ROOTS:
        hint = (
            '\n  Shell still has a docs placeholder. Run:\n'
            '    unset OHLCV_COLD_ROOT\n'
            '  or set OHLCV_COLD_ROOT=/Users/joel/Data/equities (parent of the 1m folder).'
        )
    return (
        f'No cold Parquet for {sym}\n'
        f'  expected: {p_expected}\n'
        f'  OHLCV_COLD_ROOT = {p_root}\n'
        '  Layout is {root}/1m/{SYMBOL}.parquet — root is the parent of ``1m``, not ``1m`` itself.'
        f'{hint}'
    )


def _resolve_symbol(symbol_arg: str | None) -> str:
    if symbol_arg is not None and symbol_arg.strip():
        sym = symbol_arg.strip().upper()
        if not symbol_path(sym).is_file():
            raise SystemExit(_missing_parquet_message(sym))
        return sym

    sym_env = os.environ.get(OHLCV_TEST_SYMBOL_ENV, '').strip().upper()
    if sym_env:
        if not symbol_path(sym_env).is_file():
            raise SystemExit(_missing_parquet_message(sym_env))
        return sym_env

    tickers = load_tickers_from_symbol_list_file(get_p_ohlcv_symbol_list_path())
    for sym in tickers:
        if symbol_path(sym).is_file():
            return sym
    raise SystemExit('No cold Parquet files found; pass --symbol or run ingest')


def _build_display_frame(
    sym: str,
    df_bars: 'pd.DataFrame',
    session_date_pt: date,
    display_columns: tuple[str, ...],
    *,
    display_start_time: time,
    display_end_time: time,
) -> 'pd.DataFrame':
    mask = _mask_display_equity_day(df_bars.timestamp, session_date_pt) & _mask_display_print_window(
        df_bars.timestamp,
        session_date_pt,
        display_start_time,
        display_end_time,
    )
    df_day = df_bars.loc[mask].copy()
    if df_day.empty:
        return df_day

    ts_display = timestamp_utc_series_to_zone(df_day.timestamp, display_timezone_name())
    df_day = df_day.assign(time=ts_display.dt.strftime('%H:%M'))
    return df_day.loc[:, [c for c in display_columns if c in df_day.columns]]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Print one symbol’s 1m bars with indicators. '
            f'Bars are filtered to a {BACKTEST_DISPLAY_TIMEZONE_NAME} clock window on --date.'
        ),
    )
    parser.add_argument(
        '--symbol',
        help='Ticker (default: OHLCV_TEST_SYMBOL or first symbol with cold Parquet)',
    )
    parser.add_argument(
        '--date',
        type=date.fromisoformat,
        default=OHLCV_DEFAULT_INGEST_END_DATE,
        help=(
            f'Display-zone session day anchor ({BACKTEST_DISPLAY_TIMEZONE_NAME}; '
            f'default: {OHLCV_DEFAULT_INGEST_END_DATE.isoformat()})'
        ),
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
        help='Indicator ids (default: default_pipeline_ids in indicator_catalog.yaml)',
    )
    parser.add_argument(
        '--conditions',
        action='store_true',
        help='Include all registered strategy condition columns (for strategy preview)',
    )
    parser.add_argument(
        '--condition-ids',
        nargs='+',
        metavar='CONDITION_ID',
        help='Specific condition ids (implies condition columns; default: all registered)',
    )
    parser.add_argument(
        '--strategy',
        metavar='ID_OR_PATH',
        help='Strategy id or YAML path (indicator ids, conditions, session/signal columns)',
    )
    parser.add_argument(
        '--start-time',
        type=_parse_display_time,
        default=DEFAULT_DISPLAY_START_TIME,
        metavar='HH:MM',
        help=(
            f'Display window start on --date ({BACKTEST_DISPLAY_TIMEZONE_NAME}; '
            f'default: {DEFAULT_DISPLAY_START_TIME.strftime("%H:%M")})'
        ),
    )
    parser.add_argument(
        '--end-time',
        type=_parse_display_time,
        default=DEFAULT_DISPLAY_END_TIME,
        metavar='HH:MM',
        help=(
            f'Display window end on --date, exclusive ({BACKTEST_DISPLAY_TIMEZONE_NAME}; '
            f'default: {DEFAULT_DISPLAY_END_TIME.strftime("%H:%M")})'
        ),
    )
    return parser.parse_args()


def main() -> None:
    if not cf.OHLCV_COLD_ROOT.strip():
        raise SystemExit('Set OHLCV_COLD_ROOT to the cold OHLCV Parquet root')

    args = _parse_args()
    if args.start_time >= args.end_time:
        raise SystemExit(
            f'--start-time must be before --end-time (got {args.start_time} .. {args.end_time})',
        )
    p_cold_root = require_p_ohlcv_cold_root()
    sym = _resolve_symbol(args.symbol)
    session_date_pt = args.date
    display_start_time = args.start_time
    display_end_time = args.end_time
    et_start, et_end = _exchange_analysis_dates_for_display_day(session_date_pt)

    include_session_columns = False
    if args.strategy:
        strategy = load_strategy_config(args.strategy)
        indicator_ids = resolve_pipeline_indicator_ids(
            strategy,
            indicator_ids=tuple(args.indicators) if args.indicators else None,
        )
        condition_ids = resolve_pipeline_condition_ids(
            strategy,
            condition_ids=tuple(args.condition_ids) if args.condition_ids else None,
            include_all_registered_conditions=args.conditions,
        )
        session_config = strategy.session_config
        include_session_columns = True
    else:
        strategy = None
        indicator_ids = resolve_pipeline_indicator_ids(
            None,
            indicator_ids=tuple(args.indicators) if args.indicators else None,
        )
        condition_ids = resolve_pipeline_condition_ids(
            None,
            condition_ids=tuple(args.condition_ids) if args.condition_ids else None,
            include_all_registered_conditions=args.conditions,
        )
        session_config = None

    source = ColdBarSource(et_start, et_end, warmup_bars=args.warmup_bars)
    frame = source.load(sym)
    if frame.bars.empty:
        raise SystemExit(f'No analysis rows for {sym} in ET range {et_start}..{et_end}')

    frame = IndicatorPipeline(indicator_ids).run(frame)
    if session_config is not None or condition_ids:
        frame = ConditionPipeline(condition_ids, session_config=session_config).run(frame)
    if strategy is not None:
        frame = SignalPipeline(strategy).run(frame)

    display_columns = _display_columns(
        indicator_ids,
        condition_ids,
        include_session_columns=include_session_columns,
        strategy=strategy,
    )
    df_display = _build_display_frame(
        sym,
        frame.bars,
        session_date_pt,
        display_columns,
        display_start_time=display_start_time,
        display_end_time=display_end_time,
    )
    lo_print, hi_print = _display_print_bounds(session_date_pt, display_start_time, display_end_time)
    if df_display.empty:
        raise SystemExit(
            f'No bars for {sym} in display print window '
            f'[{lo_print.isoformat()}, {hi_print.isoformat()})',
        )

    lo_equity, hi_equity = _display_equity_day_bounds(session_date_pt)
    display_tz = timezone_display_label(
        session_date_pt,
        display_start_time,
        display_timezone_name(),
    )
    print(f'display_timezone = {display_tz}')
    print(f'symbol = {sym}')
    print(f'display_session_day = {session_date_pt.isoformat()}')
    print(
        f'display_print_window = [{lo_print.strftime("%Y-%m-%d %H:%M %Z")}, '
        f'{hi_print.strftime("%Y-%m-%d %H:%M %Z")})',
    )
    print(
        f'display_equity_day = [{lo_equity.strftime("%Y-%m-%d %H:%M %Z")}, '
        f'{hi_equity.strftime("%Y-%m-%d %H:%M %Z")})',
    )
    print(f'et_load_range = {et_start.isoformat()} .. {et_end.isoformat()}')
    print(f'cold_root = {p_cold_root}')
    print(f'strategy_id = {strategy.id if strategy is not None else None}')
    print(f'indicator_ids = {list(indicator_ids)}')
    print(f'condition_ids = {list(condition_ids)}')
    for note in indicator_context_notes(frame, indicator_ids):
        print(note)
    nan_cols = display_window_nan_columns(df_display, indicator_ids)
    if nan_cols:
        print(f'display_all_nan = {list(nan_cols)}')
    print(f'bar_count = {len(df_display)}')
    print()
    print(inspect_bar_table_string(df_display))


if __name__ == '__main__':
    main()
