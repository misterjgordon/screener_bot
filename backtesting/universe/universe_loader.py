"""Load a symbol universe: cold bars → indicators → conditions → session gates."""

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol

from backtesting.conditions.condition_pipeline import ConditionPipeline
from backtesting.frames.universe_bar_frames import UniverseBarFrames
from backtesting.indicators.indicator_pipeline import IndicatorPipeline
from backtesting.signals.signal_pipeline import SignalPipeline
from backtesting.strategy.pipeline_ids import resolve_pipeline_condition_ids
from backtesting.strategy.pipeline_ids import resolve_pipeline_indicator_ids
from backtesting.strategy.universe_resolver import UniverseResolveResult
from backtesting.strategy.universe_resolver import resolve_universe_symbols
from backtesting.strategy.universe_resolver import resolve_universe_symbols_for_backtest
from backtesting.universe.universe_load_report import UniverseLoadReport
from trading.storage.ohlcv.ohlcv_paths import symbol_path

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import StrategyConfig


class BarSourceProtocol(Protocol):
    """Minimal cold-read interface used by :func:`load_universe_bars`."""

    start: date
    end: date
    interval_minutes: int

    def load(self, symbol: str) -> 'SymbolBarFrame':
        """Load one symbol's analysis window (and history for indicators)."""


def _run_pipelines(
    frame: 'SymbolBarFrame',
    *,
    indicator_ids: tuple[str, ...],
    condition_ids: tuple[str, ...],
    strategy: 'StrategyConfig | None',
) -> 'SymbolBarFrame':
    result = IndicatorPipeline(indicator_ids).run(frame)
    if strategy is not None:
        result = ConditionPipeline(
            condition_ids,
            session_config=strategy.session_config,
        ).run(result)
        result = SignalPipeline(strategy).run(result)
    elif condition_ids:
        result = ConditionPipeline(condition_ids).run(result)
    return result


def load_universe_bars(
    symbols: list[str],
    source: BarSourceProtocol,
    *,
    strategy: 'StrategyConfig | None' = None,
    indicator_ids: tuple[str, ...] | None = None,
    condition_ids: tuple[str, ...] | None = None,
) -> tuple[UniverseBarFrames, UniverseLoadReport]:
    """Load each symbol, run prep pipelines, and collect frames that succeeded.

    Missing cold Parquet, empty analysis windows, and per-symbol load failures are
    recorded in :class:`UniverseLoadReport` without aborting the full universe pass.
    """
    resolved_indicator_ids = resolve_pipeline_indicator_ids(
        strategy,
        indicator_ids=indicator_ids,
    )
    resolved_condition_ids = resolve_pipeline_condition_ids(
        strategy,
        condition_ids=condition_ids,
    )

    requested = tuple(sym.strip().upper() for sym in symbols if sym.strip())
    messages: list[str] = []
    loaded: dict[str, SymbolBarFrame] = {}
    skipped_no_parquet: list[str] = []
    skipped_empty: list[str] = []
    skipped_errors: list[str] = []

    interval = source.interval_minutes
    et_range = f'{source.start.isoformat()}..{source.end.isoformat()}'

    for sym in requested:
        p_parquet = symbol_path(sym, interval_minutes=interval)
        if not p_parquet.is_file():
            skipped_no_parquet.append(sym)
            messages.append(f'skip {sym}: no cold Parquet at {p_parquet}')
            continue

        try:
            frame = source.load(sym)
        except FileNotFoundError:
            skipped_no_parquet.append(sym)
            messages.append(f'skip {sym}: cold Parquet not found at {p_parquet}')
            continue
        except OSError as exc:
            skipped_errors.append(sym)
            messages.append(f'skip {sym}: read failed ({exc})')
            continue
        except ValueError as exc:
            skipped_errors.append(sym)
            messages.append(f'skip {sym}: invalid bars ({exc})')
            continue

        if frame.bars.empty:
            skipped_empty.append(sym)
            messages.append(f'skip {sym}: no bars in ET range {et_range}')
            continue

        try:
            loaded[sym] = _run_pipelines(
                frame,
                indicator_ids=resolved_indicator_ids,
                condition_ids=resolved_condition_ids,
                strategy=strategy,
            )
        except Exception as exc:
            skipped_errors.append(sym)
            messages.append(f'skip {sym}: pipeline failed ({type(exc).__name__}: {exc})')
            continue

        messages.append(f'loaded {sym}: {len(loaded[sym].bars)} analysis bars')

    report = UniverseLoadReport(
        requested_symbols=requested,
        loaded_symbols=tuple(sorted(loaded)),
        skipped_no_parquet=tuple(skipped_no_parquet),
        skipped_empty_window=tuple(skipped_empty),
        skipped_errors=tuple(skipped_errors),
        messages=tuple(messages),
    )
    return UniverseBarFrames(loaded), report


def load_prepared_universe(
    source: BarSourceProtocol,
    *,
    strategy: 'StrategyConfig | None' = None,
    explicit_symbols: tuple[str, ...] | None = None,
    p_symbol_list: Path | None = None,
    use_cold_dir: bool = False,
    universe_resolve: UniverseResolveResult | None = None,
    indicator_ids: tuple[str, ...] | None = None,
    condition_ids: tuple[str, ...] | None = None,
) -> tuple[UniverseBarFrames, UniverseLoadReport, UniverseResolveResult]:
    """Resolve symbols then load and prep each (skips missing Parquet / empty windows)."""
    if universe_resolve is not None:
        resolved = universe_resolve
    elif explicit_symbols or p_symbol_list is not None or use_cold_dir:
        resolved = resolve_universe_symbols(
            explicit_symbols=explicit_symbols,
            p_symbol_list=p_symbol_list,
            use_cold_dir=use_cold_dir,
            interval_minutes=source.interval_minutes,
        )
    else:
        resolved = resolve_universe_symbols_for_backtest(
            interval_minutes=source.interval_minutes,
        )
    universe, report = load_universe_bars(
        list(resolved.symbols),
        source,
        strategy=strategy,
        indicator_ids=indicator_ids,
        condition_ids=condition_ids,
    )
    return universe, report, resolved


def format_universe_load_report(report: UniverseLoadReport) -> str:
    """Human-readable summary for CLI or logs."""
    lines = [
        f'requested = {len(report.requested_symbols)} | loaded = {report.loaded_count} | '
        f'skipped = {report.skipped_count}',
        f'loaded_symbols = {list(report.loaded_symbols)}',
    ]
    if report.skipped_no_parquet:
        lines.append(f'skipped_no_parquet ({len(report.skipped_no_parquet)}) = '
                     f'{list(report.skipped_no_parquet)}')
    if report.skipped_empty_window:
        lines.append(f'skipped_empty_window ({len(report.skipped_empty_window)}) = '
                     f'{list(report.skipped_empty_window)}')
    if report.skipped_errors:
        lines.append(f'skipped_errors ({len(report.skipped_errors)}) = '
                     f'{list(report.skipped_errors)}')
    lines.extend(report.messages)
    return '\n'.join(lines)
