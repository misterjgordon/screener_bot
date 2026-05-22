"""Inspect context notes for daily/history-dependent indicators."""

import pandas as pd

from backtesting.frames.symbol_bar_frame import SymbolBarFrame
from backtesting.indicators.indicator_catalog_load import default_indicator_ids
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY
from backtesting.inspect.indicator_context import display_window_nan_columns
from backtesting.inspect.indicator_context import indicator_context_notes
from backtesting.strategy.pipeline_ids import resolve_pipeline_indicator_ids
from tests.strategy_signal_test_support import load_strategy

STRATEGY_ID = 'ema_cross'


def test_indicator_context_notes_ok_when_daily_and_history_present() -> None:
    indicator_ids = ('trading_date', 'adr', 'rvol')
    daily = pd.DataFrame({'trading_date': [f'2026-05-{d:02d}' for d in range(1, 20)]})
    history_ts = pd.date_range('2026-03-01', periods=30 * 24 * 60, freq='1min', tz='UTC')
    history = pd.DataFrame({'timestamp': history_ts, 'volume': 100})
    frame = SymbolBarFrame(
        symbol='AAPL',
        interval_minutes=1,
        bars=pd.DataFrame(),
        daily_bars=daily,
        history_bars=history,
    )
    notes = indicator_context_notes(frame, indicator_ids)

    print(f'**summary for context notes ok:**\nnotes = {list(notes)}')

    assert len(notes) == 2
    assert all('may be NaN' not in note for note in notes)


def test_indicator_context_notes_warn_when_daily_history_thin() -> None:
    indicator_ids = ('adr', 'rvol')
    frame = SymbolBarFrame(
        symbol='AAPL',
        interval_minutes=1,
        bars=pd.DataFrame(),
        daily_bars=pd.DataFrame({'trading_date': ['2026-05-15']}),
        history_bars=pd.DataFrame(
            {
                'timestamp': pd.date_range('2026-05-15', periods=3, freq='1min', tz='UTC'),
                'volume': [100, 100, 100],
            },
        ),
    )
    notes = indicator_context_notes(frame, indicator_ids)

    print(f'**summary for thin context:**\nnotes = {list(notes)}')

    assert any('may be NaN' in note for note in notes)


def test_display_window_nan_columns_lists_all_nan_outputs() -> None:
    df = pd.DataFrame({'adr': [float('nan'), float('nan')], 'rvol': [1.0, 2.0]})
    nan_cols = display_window_nan_columns(df, ('adr', 'rvol'))

    print(f'**summary for display nan cols:**\nnan_cols = {list(nan_cols)}')

    assert nan_cols == ('adr',)


def test_resolve_pipeline_indicator_ids_matches_strategy_merge() -> None:
    strategy = load_strategy(STRATEGY_ID)
    resolved = resolve_pipeline_indicator_ids(strategy)
    expected = strategy.indicator_ids_for_pipeline(
        default_indicator_ids(),
        frozenset(INDICATOR_REGISTRY.ids()),
    )

    print(f'**summary for pipeline ids:**\nresolved = {list(resolved)}')

    assert resolved == expected
    assert 'rvol' in resolved
    assert 'ema21' in resolved
