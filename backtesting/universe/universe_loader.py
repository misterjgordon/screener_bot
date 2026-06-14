"""Load a symbol universe: cold bars → indicators → conditions → session gates."""

from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol
from typing import cast

from backtesting.conditions.condition_pipeline import ConditionPipeline
from backtesting.frames.universe_bar_frames import UniverseBarFrames
from backtesting.indicators.indicator_pipeline import IndicatorPipeline
from backtesting.io.cold_bar_source import ColdBarSource
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

    @property
    def start(self) -> date: ...

    @property
    def end(self) -> date: ...

    @property
    def interval_minutes(self) -> int: ...

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


@dataclass(frozen=True)
class _ColdSymbolLoadRequest:
    """Picklable per-symbol cold load + prep (for process pool workers)."""

    sym: str
    start: date
    end: date
    interval_minutes: int
    warmup_bars: int
    indicator_ids: tuple[str, ...]
    condition_ids: tuple[str, ...]
    strategy: 'StrategyConfig | None'


@dataclass(frozen=True)
class _SymbolLoadOutcome:
    """Per-symbol load result merged into :class:`UniverseLoadReport`."""

    sym: str
    frame: 'SymbolBarFrame | None' = None
    skipped_no_parquet: bool = False
    skipped_empty: bool = False
    skipped_error: bool = False
    message: str = ''


def _load_and_prep_symbol_cold(request: _ColdSymbolLoadRequest) -> _SymbolLoadOutcome:
    """Worker: ``ColdBarSource.load`` then indicator/condition/signal pipelines."""
    sym = request.sym
    et_range = f'{request.start.isoformat()}..{request.end.isoformat()}'
    source = ColdBarSource(
        request.start,
        request.end,
        interval_minutes=request.interval_minutes,
        warmup_bars=request.warmup_bars,
    )
    try:
        frame = source.load(sym)
    except FileNotFoundError:
        p_parquet = symbol_path(sym, interval_minutes=request.interval_minutes)
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_no_parquet=True,
            message=f'skip {sym}: cold Parquet not found at {p_parquet}',
        )
    except OSError as exc:
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_error=True,
            message=f'skip {sym}: read failed ({exc})',
        )
    except ValueError as exc:
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_error=True,
            message=f'skip {sym}: invalid bars ({exc})',
        )

    if frame.bars.empty:
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_empty=True,
            message=f'skip {sym}: no bars in ET range {et_range}',
        )

    try:
        prepped = _run_pipelines(
            frame,
            indicator_ids=request.indicator_ids,
            condition_ids=request.condition_ids,
            strategy=request.strategy,
        )
    except Exception as exc:
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_error=True,
            message=f'skip {sym}: pipeline failed ({type(exc).__name__}: {exc})',
        )

    return _SymbolLoadOutcome(
        sym=sym,
        frame=prepped,
        message=f'loaded {sym}: {len(prepped.bars)} analysis bars',
    )


def _load_and_prep_symbol_inprocess(
    sym: str,
    source: BarSourceProtocol,
    *,
    et_range: str,
    indicator_ids: tuple[str, ...],
    condition_ids: tuple[str, ...],
    strategy: 'StrategyConfig | None',
) -> _SymbolLoadOutcome:
    """Load one symbol from an in-process ``BarSourceProtocol`` (tests, stubs)."""
    try:
        frame = source.load(sym)
    except FileNotFoundError:
        p_parquet = symbol_path(sym, interval_minutes=source.interval_minutes)
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_no_parquet=True,
            message=f'skip {sym}: cold Parquet not found at {p_parquet}',
        )
    except OSError as exc:
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_error=True,
            message=f'skip {sym}: read failed ({exc})',
        )
    except ValueError as exc:
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_error=True,
            message=f'skip {sym}: invalid bars ({exc})',
        )

    if frame.bars.empty:
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_empty=True,
            message=f'skip {sym}: no bars in ET range {et_range}',
        )

    try:
        prepped = _run_pipelines(
            frame,
            indicator_ids=indicator_ids,
            condition_ids=condition_ids,
            strategy=strategy,
        )
    except Exception as exc:
        return _SymbolLoadOutcome(
            sym=sym,
            skipped_error=True,
            message=f'skip {sym}: pipeline failed ({type(exc).__name__}: {exc})',
        )

    return _SymbolLoadOutcome(
        sym=sym,
        frame=prepped,
        message=f'loaded {sym}: {len(prepped.bars)} analysis bars',
    )


def _merge_symbol_outcome(
    outcome: _SymbolLoadOutcome,
    *,
    loaded: dict[str, 'SymbolBarFrame'],
    skipped_no_parquet: list[str],
    skipped_empty: list[str],
    skipped_errors: list[str],
    messages: list[str],
) -> None:
    messages.append(outcome.message)
    if outcome.skipped_no_parquet:
        skipped_no_parquet.append(outcome.sym)
        return
    if outcome.skipped_empty:
        skipped_empty.append(outcome.sym)
        return
    if outcome.skipped_error:
        skipped_errors.append(outcome.sym)
        return
    if outcome.frame is not None:
        loaded[outcome.sym] = outcome.frame


def _partition_requested_symbols(
    requested: tuple[str, ...],
    *,
    interval_minutes: int,
) -> tuple[tuple[str, ...], list[str], list[str]]:
    """Split symbols into loadable vs missing Parquet (no read)."""
    to_load: list[str] = []
    skipped_no_parquet: list[str] = []
    messages: list[str] = []
    for sym in requested:
        p_parquet = symbol_path(sym, interval_minutes=interval_minutes)
        if not p_parquet.is_file():
            skipped_no_parquet.append(sym)
            messages.append(f'skip {sym}: no cold Parquet at {p_parquet}')
        else:
            to_load.append(sym)
    return tuple(to_load), skipped_no_parquet, messages


def load_universe_bars(
    symbols: list[str],
    source: BarSourceProtocol,
    *,
    strategy: 'StrategyConfig | None' = None,
    indicator_ids: tuple[str, ...] | None = None,
    condition_ids: tuple[str, ...] | None = None,
    jobs: int = 1,
) -> tuple[UniverseBarFrames, UniverseLoadReport]:
    """Load each symbol, run prep pipelines, and collect frames that succeeded.

    Missing cold Parquet, empty analysis windows, and per-symbol load failures are
    recorded in :class:`UniverseLoadReport` without aborting the full universe pass.

    When ``jobs > 1`` and ``source`` is :class:`~backtesting.io.cold_bar_source.ColdBarSource`,
    symbols are loaded and prepped in parallel worker processes.
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
    symbols_to_load, missing, missing_msgs = _partition_requested_symbols(
        requested,
        interval_minutes=interval,
    )
    skipped_no_parquet.extend(missing)
    messages.extend(missing_msgs)

    worker_count = max(1, jobs)
    use_process_pool = worker_count > 1 and isinstance(source, ColdBarSource)

    if use_process_pool:
        cold_source = cast('ColdBarSource', source)
        requests = [
            _ColdSymbolLoadRequest(
                sym=sym,
                start=cold_source.start,
                end=cold_source.end,
                interval_minutes=cold_source.interval_minutes,
                warmup_bars=cold_source.warmup_bar_count,
                indicator_ids=resolved_indicator_ids,
                condition_ids=resolved_condition_ids,
                strategy=strategy,
            )
            for sym in symbols_to_load
        ]
        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            futures = {pool.submit(_load_and_prep_symbol_cold, req): req.sym for req in requests}
            for fut in as_completed(futures):
                _merge_symbol_outcome(
                    fut.result(),
                    loaded=loaded,
                    skipped_no_parquet=skipped_no_parquet,
                    skipped_empty=skipped_empty,
                    skipped_errors=skipped_errors,
                    messages=messages,
                )
    else:
        for sym in symbols_to_load:
            outcome = _load_and_prep_symbol_inprocess(
                sym,
                source,
                et_range=et_range,
                indicator_ids=resolved_indicator_ids,
                condition_ids=resolved_condition_ids,
                strategy=strategy,
            )
            _merge_symbol_outcome(
                outcome,
                loaded=loaded,
                skipped_no_parquet=skipped_no_parquet,
                skipped_empty=skipped_empty,
                skipped_errors=skipped_errors,
                messages=messages,
            )

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
    jobs: int = 1,
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
        jobs=jobs,
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
