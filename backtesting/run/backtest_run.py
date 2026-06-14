"""Orchestrate cold load → prep pipelines → portfolio simulation for one backtest run."""

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from backtesting.io.cold_bar_source import ColdBarSource
from backtesting.portfolio.portfolio_simulator import PortfolioSimResult
from backtesting.portfolio.portfolio_simulator import PortfolioSimulator
from backtesting.strategy.pipeline_ids import resolve_pipeline_indicator_ids
from backtesting.strategy.strategy_loader import load_strategy_config
from backtesting.universe.universe_loader import format_universe_load_report
from backtesting.universe.universe_loader import load_prepared_universe

if TYPE_CHECKING:
    from backtesting.frames.universe_bar_frames import UniverseBarFrames
    from backtesting.portfolio.trade import Trade
    from backtesting.strategy.strategy_config import StrategyConfig
    from backtesting.strategy.universe_resolver import UniverseResolveResult
    from backtesting.universe.universe_load_report import UniverseLoadReport


@dataclass(frozen=True)
class BacktestTimings:
    """Wall-clock seconds for load/prep vs simulation."""

    load_seconds: float
    sim_seconds: float

    @property
    def total_seconds(self) -> float:
        return self.load_seconds + self.sim_seconds


@dataclass(frozen=True)
class BacktestRunResult:
    """Outputs from a full backtest pass."""

    strategy: 'StrategyConfig'
    indicator_ids: tuple[str, ...]
    resolve: 'UniverseResolveResult'
    load_report: 'UniverseLoadReport'
    universe: 'UniverseBarFrames'
    sim_result: PortfolioSimResult
    timings: BacktestTimings


def run_backtest(
    *,
    strategy_id_or_path: str,
    start: date,
    end: date,
    explicit_symbols: tuple[str, ...] | None = None,
    p_symbol_list: Path | None = None,
    use_cold_dir: bool = False,
    universe_resolve: 'UniverseResolveResult | None' = None,
    warmup_bars: int,
    indicator_ids: tuple[str, ...] | None = None,
    condition_ids: tuple[str, ...] | None = None,
    jobs: int = 1,
) -> BacktestRunResult:
    """Load universe from cold Parquet, prep bars, and simulate trades.

    ``start`` / ``end`` are inclusive ET session calendar dates (same as
    :class:`~backtesting.io.cold_bar_source.ColdBarSource`).
    """
    if end < start:
        msg = f'end date {end} is before start date {start}'
        raise ValueError(msg)

    strategy = load_strategy_config(strategy_id_or_path)
    resolved_indicator_ids = resolve_pipeline_indicator_ids(
        strategy,
        indicator_ids=indicator_ids,
    )
    source = ColdBarSource(start, end, warmup_bars=warmup_bars)
    load_started = time.perf_counter()
    universe, load_report, resolve = load_prepared_universe(
        source,
        strategy=strategy,
        explicit_symbols=explicit_symbols,
        p_symbol_list=p_symbol_list,
        use_cold_dir=use_cold_dir,
        universe_resolve=universe_resolve,
        indicator_ids=resolved_indicator_ids,
        condition_ids=condition_ids,
        jobs=jobs,
    )
    load_seconds = time.perf_counter() - load_started
    sim_started = time.perf_counter()
    sim_result = PortfolioSimulator(strategy).run(universe)
    sim_seconds = time.perf_counter() - sim_started
    timings = BacktestTimings(load_seconds=load_seconds, sim_seconds=sim_seconds)
    return BacktestRunResult(
        strategy=strategy,
        indicator_ids=resolved_indicator_ids,
        resolve=resolve,
        load_report=load_report,
        universe=universe,
        sim_result=sim_result,
        timings=timings,
    )


def _format_trade_line(trade: 'Trade') -> str:
    entry_ts = str(trade.entry_timestamp_utc)
    exit_ts = str(trade.exit_timestamp_utc)
    return (
        f'  {trade.symbol} {trade.trading_date} {trade.exit_reason} '
        f'entry={trade.entry_price:.2f} exit={trade.exit_price:.2f} '
        f'shares={trade.shares:.2f} pnl={trade.pnl:.2f} ({trade.pnl_pct:.2%}) '
        f'{entry_ts} -> {exit_ts}'
    )


def _format_seconds(seconds: float) -> str:
    """Human-readable duration (ms below 1s, else seconds with one decimal)."""
    if seconds < 1.0:
        return f'{seconds * 1000:.0f}ms'
    return f'{seconds:.1f}s'


def format_backtest_summary(
    result: BacktestRunResult,
    *,
    p_cold_root: Path,
    et_start: date,
    et_end: date,
    p_strategy_config: Path,
    summary_only: bool = False,
) -> str:
    """Human-readable run summary for CLI or logs."""
    lines = [
        f'strategy_id = {result.strategy.id}',
        f'strategy_config = {p_strategy_config}',
        f'et_range = {et_start.isoformat()} .. {et_end.isoformat()}',
        f'cold_root = {p_cold_root}',
        f'universe_source = {result.resolve.source} ({result.resolve.source_detail})',
        f'indicator_ids = {list(result.indicator_ids)}',
        '',
        format_universe_load_report(result.load_report),
        '',
        f'trades = {result.sim_result.trade_count}',
        f'total_pnl = {result.sim_result.total_pnl:.2f}',
        (
            f'elapsed = {_format_seconds(result.timings.total_seconds)} '
            f'(load={_format_seconds(result.timings.load_seconds)}, '
            f'sim={_format_seconds(result.timings.sim_seconds)})'
        ),
    ]
    if result.sim_result.trades and not summary_only:
        lines.append('')
        lines.append('trades_detail:')
        for trade in result.sim_result.trades:
            lines.append(_format_trade_line(trade))
    by_symbol: dict[str, list] = {}
    for trade in result.sim_result.trades:
        by_symbol.setdefault(trade.symbol, []).append(trade)
    if by_symbol:
        lines.append('')
        lines.append('pnl_by_symbol:')
        for sym in sorted(by_symbol):
            sym_pnl = sum(t.pnl for t in by_symbol[sym])
            lines.append(f'  {sym}: {len(by_symbol[sym])} trades, pnl={sym_pnl:.2f}')
    return '\n'.join(lines)
