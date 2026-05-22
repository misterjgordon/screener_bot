"""Backtest runner orchestration (step 10) without cold Parquet."""

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from backtesting.conditions.session_regime import SESSION_COLUMN
from backtesting.conditions.session_regime import SIGNAL_ELIGIBLE_COLUMN
from backtesting.frames.symbol_bar_frame import SymbolBarFrame
from backtesting.frames.universe_bar_frames import UniverseBarFrames
from backtesting.portfolio.portfolio_simulator import PortfolioSimResult
from backtesting.portfolio.trade import Trade
from backtesting.run.backtest_run import BacktestRunResult
from backtesting.run.backtest_run import BacktestTimings
from backtesting.run.backtest_run import format_backtest_summary
from backtesting.run.backtest_run import run_backtest
from backtesting.signals.entry_columns import ENTRY_EVENT_COLUMN
from backtesting.strategy.strategy_loader import resolve_strategy_config_path
from backtesting.strategy.universe_resolver import UniverseResolveResult
from backtesting.universe.universe_load_report import UniverseLoadReport
from tests.strategy_signal_test_support import TRADING_DATE
from tests.strategy_signal_test_support import load_strategy
from tests.strategy_signal_test_support import minimal_strategy_bars

if TYPE_CHECKING:
    import pytest

    from backtesting.strategy.strategy_config import StrategyConfig

STRATEGY_ID = 'ema_cross'
SYMBOL = 'AAA'
SESSION_DATE = date(2026, 5, 15)


class _StubColdBarSource:
    """Minimal cold source for runner tests."""

    def __init__(
        self,
        *,
        start: date,
        end: date,
        frames_by_symbol: dict[str, SymbolBarFrame],
        interval_minutes: int = 1,
    ) -> None:
        self.start = start
        self.end = end
        self.interval_minutes = interval_minutes
        self._frames = frames_by_symbol

    def load(self, symbol: str) -> SymbolBarFrame:
        sym = symbol.strip().upper()
        return self._frames[sym]


def _frame_with_entry_event(strategy: 'StrategyConfig', symbol: str) -> SymbolBarFrame:
    df = minimal_strategy_bars(strategy, symbol).assign(
        **{
            SESSION_COLUMN: 'RTH',
            SIGNAL_ELIGIBLE_COLUMN: True,
            ENTRY_EVENT_COLUMN: True,
        },
    )
    return SymbolBarFrame(symbol=symbol, interval_minutes=1, bars=df)


def test_run_backtest_simulates_loaded_universe(monkeypatch: 'pytest.MonkeyPatch') -> None:
    strategy = load_strategy(STRATEGY_ID)
    frame = _frame_with_entry_event(strategy, SYMBOL)
    source = _StubColdBarSource(
        start=SESSION_DATE,
        end=SESSION_DATE,
        frames_by_symbol={SYMBOL: frame},
    )

    def _fake_load_prepared_universe(
        source: object,
        **kw: object,
    ) -> tuple[UniverseBarFrames, UniverseLoadReport, UniverseResolveResult]:
        universe = UniverseBarFrames({SYMBOL: frame})
        report = UniverseLoadReport(
            requested_symbols=(SYMBOL,),
            loaded_symbols=(SYMBOL,),
            skipped_no_parquet=(),
            skipped_empty_window=(),
            skipped_errors=(),
            messages=(f'loaded {SYMBOL}: 1 analysis bars',),
        )
        resolved = UniverseResolveResult((SYMBOL,), 'explicit', 'explicit_symbols')
        return universe, report, resolved

    monkeypatch.setattr(
        'backtesting.run.backtest_run.load_prepared_universe',
        _fake_load_prepared_universe,
    )
    monkeypatch.setattr(
        'backtesting.run.backtest_run.ColdBarSource',
        lambda start, end, **kw: source,
    )

    result = run_backtest(
        strategy_id_or_path=STRATEGY_ID,
        start=SESSION_DATE,
        end=SESSION_DATE,
        explicit_symbols=(SYMBOL,),
        warmup_bars=0,
    )

    trade_count = result.sim_result.trade_count
    loaded = result.load_report.loaded_count
    has_entry_event = bool(frame.bars[ENTRY_EVENT_COLUMN].iloc[0])

    print(
        '**summary for run_backtest:**\n'
        f'loaded = {loaded} | trade_count = {trade_count} | entry_event = {has_entry_event}'
    )

    assert loaded == 1
    assert has_entry_event
    assert trade_count >= 1
    assert result.timings.total_seconds >= 0.0


def test_format_backtest_summary_includes_pnl(tmp_path: Path) -> None:
    strategy = load_strategy(STRATEGY_ID)
    trade = Trade(
        symbol=SYMBOL,
        trading_date=TRADING_DATE,
        entry_timestamp_utc=pd.Timestamp('2026-05-15 14:30:00', tz='UTC'),
        entry_price=100.0,
        exit_timestamp_utc=pd.Timestamp('2026-05-15 14:31:00', tz='UTC'),
        exit_price=98.0,
        exit_reason='stop_loss',
        shares=50.0,
        pnl=-100.0,
        pnl_pct=-0.02,
    )
    empty_frame = SymbolBarFrame(
        symbol=SYMBOL,
        interval_minutes=1,
        bars=minimal_strategy_bars(strategy, SYMBOL).iloc[0:0],
    )
    result = BacktestRunResult(
        strategy=strategy,
        indicator_ids=('trading_date',),
        resolve=UniverseResolveResult((SYMBOL,), 'explicit', 'test'),
        load_report=UniverseLoadReport(
            requested_symbols=(SYMBOL,),
            loaded_symbols=(SYMBOL,),
            skipped_no_parquet=(),
            skipped_empty_window=(),
            skipped_errors=(),
            messages=(),
        ),
        universe=UniverseBarFrames({SYMBOL: empty_frame}),
        sim_result=PortfolioSimResult(trades=(trade,)),
        timings=BacktestTimings(load_seconds=1.2, sim_seconds=0.05),
    )
    text = format_backtest_summary(
        result,
        p_cold_root=tmp_path,
        et_start=SESSION_DATE,
        et_end=SESSION_DATE,
        p_strategy_config=resolve_strategy_config_path(STRATEGY_ID),
    )

    has_total_pnl = 'total_pnl = -100.00' in text
    has_symbol_pnl = 'pnl_by_symbol' in text
    has_elapsed = 'elapsed = 1.2s (load=1.2s, sim=50ms)' in text

    print(f'**summary for format_backtest_summary:**\nhas_total_pnl = {has_total_pnl}')

    assert has_total_pnl
    assert has_symbol_pnl
    assert has_elapsed


def test_format_backtest_summary_summary_only_omits_trade_lines(tmp_path: Path) -> None:
    strategy = load_strategy(STRATEGY_ID)
    trade = Trade(
        symbol=SYMBOL,
        trading_date=TRADING_DATE,
        entry_timestamp_utc=pd.Timestamp('2026-05-15 14:30:00', tz='UTC'),
        entry_price=100.0,
        exit_timestamp_utc=pd.Timestamp('2026-05-15 14:31:00', tz='UTC'),
        exit_price=98.0,
        exit_reason='stop_loss',
        shares=50.0,
        pnl=-100.0,
        pnl_pct=-0.02,
    )
    empty_frame = SymbolBarFrame(
        symbol=SYMBOL,
        interval_minutes=1,
        bars=minimal_strategy_bars(strategy, SYMBOL).iloc[0:0],
    )
    result = BacktestRunResult(
        strategy=strategy,
        indicator_ids=('trading_date',),
        resolve=UniverseResolveResult((SYMBOL,), 'explicit', 'test'),
        load_report=UniverseLoadReport(
            requested_symbols=(SYMBOL,),
            loaded_symbols=(SYMBOL,),
            skipped_no_parquet=(),
            skipped_empty_window=(),
            skipped_errors=(),
            messages=(),
        ),
        universe=UniverseBarFrames({SYMBOL: empty_frame}),
        sim_result=PortfolioSimResult(trades=(trade,)),
        timings=BacktestTimings(load_seconds=1.0, sim_seconds=0.01),
    )
    text = format_backtest_summary(
        result,
        p_cold_root=tmp_path,
        et_start=SESSION_DATE,
        et_end=SESSION_DATE,
        p_strategy_config=resolve_strategy_config_path(STRATEGY_ID),
        summary_only=True,
    )

    assert 'trades_detail:' not in text
    assert 'total_pnl = -100.00' in text
    assert 'pnl_by_symbol:' in text
