"""Cross-symbol capital allocation (task 4, Lap 3): candidate merge + single-position mode."""

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from backtesting.frames.universe_bar_frames import UniverseBarFrames
from backtesting.portfolio.candidate_merge import collect_entry_candidates
from backtesting.portfolio.capital_allocator import simulate_single_position
from backtesting.signals.entry_columns import ENTRY_EVENT_COLUMN
from backtesting.signals.signal_columns import SIGNAL_EXIT_HIT_COLUMN
from backtesting.strategy.strategy_config import SizingFullAllocation
from backtesting.strategy.strategy_config import StrategyConfig
from tests.strategy_signal_test_support import frame_with_session
from tests.strategy_signal_test_support import load_strategy

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame

STRATEGY_ID = 'ema_cross'
CAPITAL = 10_000.0


def _strategy_full_allocation() -> StrategyConfig:
    strategy = load_strategy(STRATEGY_ID).model_copy(update={'other_exits': ()})
    return strategy.model_copy(update={'sizing': SizingFullAllocation(method='full_allocation')})


def _candidate_frame(
    strategy: StrategyConfig,
    symbol: str,
    *,
    start: str,
    bar_count: int,
    entry_pos: int,
    signal_exit_pos: int | None,
) -> 'SymbolBarFrame':
    """Flat-price bars (never hit stop/target) with ``entry_event`` and an optional forced exit."""
    close = 100.0
    timestamps = pd.Series(pd.date_range(start, periods=bar_count, freq='1min', tz='UTC'))
    frame = frame_with_session(
        strategy,
        timestamp=timestamps,
        open=pd.Series([close] * bar_count),
        high=pd.Series([close] * bar_count),
        low=pd.Series([close] * bar_count),
        close=pd.Series([close] * bar_count),
    )
    entry_event = pd.Series(False, index=frame.bars.index)
    entry_event.iloc[entry_pos] = True
    assign_kw = {'symbol': pd.Series([symbol] * bar_count), ENTRY_EVENT_COLUMN: entry_event}
    if signal_exit_pos is not None:
        signal_exit_hit = pd.Series(False, index=frame.bars.index)
        signal_exit_hit.iloc[signal_exit_pos] = True
        assign_kw[SIGNAL_EXIT_HIT_COLUMN] = signal_exit_hit
    return frame.with_columns(**assign_kw)


def test_collect_entry_candidates_sorted_by_time_not_symbol() -> None:
    strategy = _strategy_full_allocation()
    # BBB's entry is earlier in wall time than AAA's, despite sorting after it alphabetically.
    frame_aaa = _candidate_frame(
        strategy, 'AAA', start='2026-05-15 14:05', bar_count=2, entry_pos=0, signal_exit_pos=1,
    )
    frame_bbb = _candidate_frame(
        strategy, 'BBB', start='2026-05-15 14:00', bar_count=2, entry_pos=0, signal_exit_pos=1,
    )
    universe = UniverseBarFrames({'AAA': frame_aaa, 'BBB': frame_bbb})

    candidates = collect_entry_candidates(universe, strategy)
    symbols_in_order = [c.symbol for c in candidates]

    print(f'**summary for candidate merge order:**\nsymbols_in_order = {symbols_in_order}')

    assert symbols_in_order == ['BBB', 'AAA']


def test_single_position_skips_candidate_while_position_open() -> None:
    strategy = _strategy_full_allocation()
    # AAA: entry 14:00, exit 14:01 (forced via signal_exit_hit).
    frame_aaa = _candidate_frame(
        strategy, 'AAA', start='2026-05-15 14:00', bar_count=2, entry_pos=0, signal_exit_pos=1,
    )
    # BBB: entry 14:00 too (tied with AAA, but AAA resolves first — sorted symbol order).
    frame_bbb = _candidate_frame(
        strategy, 'BBB', start='2026-05-15 14:00', bar_count=2, entry_pos=0, signal_exit_pos=None,
    )
    universe = UniverseBarFrames({'AAA': frame_aaa, 'BBB': frame_bbb})

    result = simulate_single_position(universe, strategy, capital=CAPITAL)

    trade_symbols = [t.symbol for t in result.trades]
    skipped_symbols = [c.symbol for c in result.skipped]

    print(
        '**summary for single_position skip:**\n'
        f'trade_symbols = {trade_symbols} | skipped_symbols = {skipped_symbols}'
    )

    assert trade_symbols == ['AAA']
    assert skipped_symbols == ['BBB']


def test_single_position_accepts_candidate_at_prior_exit_boundary() -> None:
    strategy = _strategy_full_allocation()
    # AAA: entry 14:00, exit 14:01.
    frame_aaa = _candidate_frame(
        strategy, 'AAA', start='2026-05-15 14:00', bar_count=2, entry_pos=0, signal_exit_pos=1,
    )
    # CCC: entry exactly at AAA's exit timestamp (14:01) — boundary is inclusive (accepted, not skipped).
    frame_ccc = _candidate_frame(
        strategy, 'CCC', start='2026-05-15 14:01', bar_count=2, entry_pos=0, signal_exit_pos=1,
    )
    universe = UniverseBarFrames({'AAA': frame_aaa, 'CCC': frame_ccc})

    result = simulate_single_position(universe, strategy, capital=CAPITAL)

    trade_symbols = [t.symbol for t in result.trades]

    print(f'**summary for single_position boundary accept:**\ntrade_symbols = {trade_symbols}')

    assert trade_symbols == ['AAA', 'CCC']
    assert result.skipped == ()


def test_single_position_sizes_trades_from_capital() -> None:
    strategy = _strategy_full_allocation()
    frame_aaa = _candidate_frame(
        strategy, 'AAA', start='2026-05-15 14:00', bar_count=2, entry_pos=0, signal_exit_pos=1,
    )
    universe = UniverseBarFrames({'AAA': frame_aaa})

    result = simulate_single_position(universe, strategy, capital=CAPITAL)

    shares = result.trades[0].shares
    expected_shares = CAPITAL / result.trades[0].entry_price

    print(f'**summary for single_position sizing:**\nshares = {shares} | expected = {expected_shares}')

    assert shares == pytest.approx(expected_shares)
