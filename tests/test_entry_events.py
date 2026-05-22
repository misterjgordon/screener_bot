"""Arming window and per-day entry_event latch (step 8) — any strategy YAML."""

import pandas as pd
import pytest

from backtesting.signals.arming import apply_entry_columns
from backtesting.signals.arming import armed_series
from backtesting.signals.arming import entry_event_series
from backtesting.signals.entry_columns import ARMED_COLUMN
from backtesting.signals.entry_columns import ENTRY_EVENT_COLUMN
from backtesting.signals.entry_columns import STRATEGY_FIRED_TODAY_COLUMN
from backtesting.signals.signal_columns import filter_column_name
from backtesting.signals.signal_columns import trigger_column_name
from backtesting.signals.signal_pipeline import SignalPipeline
from tests.strategy_signal_test_support import TRADING_DATE
from tests.strategy_signal_test_support import build_cross_above_series
from tests.strategy_signal_test_support import build_cross_above_series_at_bars
from tests.strategy_signal_test_support import build_filter_level_series
from tests.strategy_signal_test_support import discover_strategy_ids
from tests.strategy_signal_test_support import filter_pass_value
from tests.strategy_signal_test_support import first_cross_above_trigger
from tests.strategy_signal_test_support import frame_with_session
from tests.strategy_signal_test_support import load_strategy

ARMING_WINDOW = 3
STRATEGY_IDS = discover_strategy_ids()


def test_armed_window_after_trigger() -> None:
    trigger = pd.Series([False, True, False, False, False])
    trading_date = pd.Series([TRADING_DATE] * 5)
    armed = armed_series(trigger, trading_date, ARMING_WINDOW)

    armed_1 = bool(armed.iloc[1])
    armed_2 = bool(armed.iloc[2])
    armed_3 = bool(armed.iloc[3])
    armed_4 = bool(armed.iloc[4])

    print(
        '**summary for armed window:**\n'
        f'arming_window = {ARMING_WINDOW}\n'
        f'armed_1 = {armed_1} | armed_2 = {armed_2} | armed_3 = {armed_3} | armed_4 = {armed_4}'
    )

    assert armed_1
    assert armed_2
    assert armed_3
    assert not armed_4


def test_entry_event_first_per_trading_date() -> None:
    entry_signal = pd.Series([False, True, False, True])
    trading_date = pd.Series([TRADING_DATE] * 4)
    events = entry_event_series(entry_signal, trading_date, entry_rule='first')

    event_1 = bool(events.iloc[1])
    event_3 = bool(events.iloc[3])

    print(
        '**summary for entry_event first:**\n'
        f'event_bar_1 = {event_1} | event_bar_3 = {event_3}'
    )

    assert event_1
    assert not event_3


@pytest.mark.parametrize('strategy_id', STRATEGY_IDS)
def test_filter_after_trigger_within_armed_window(strategy_id: str) -> None:
    """First ``cross_above`` trigger + first filter: entry when filter passes on trigger bar."""
    strategy = load_strategy(strategy_id)
    trigger_rule = first_cross_above_trigger(strategy)
    if trigger_rule is None:
        pytest.skip(f'{strategy_id}: no cross_above trigger')
    if not strategy.filters:
        pytest.skip(f'{strategy_id}: no filters')

    filter_rule = strategy.filters[0]
    cross_bar = 2
    bar_count = 4
    strategy = strategy.model_copy(update={'arming_window': ARMING_WINDOW})
    series_kw = build_cross_above_series(trigger_rule, bar_count, cross_bar)
    series_kw[filter_rule.column] = build_filter_level_series(
        filter_rule,
        bar_count,
        pass_bars={cross_bar},
    )
    frame = frame_with_session(strategy, **series_kw)
    out = SignalPipeline(strategy).run(frame)

    trigger_col = trigger_column_name(trigger_rule.id)
    filter_col = filter_column_name(filter_rule.id)
    armed_at_cross = bool(out.bars[ARMED_COLUMN].iloc[cross_bar])
    entry_at_cross = bool(out.bars[ENTRY_EVENT_COLUMN].iloc[cross_bar])

    print(
        f'**summary for filter after trigger ({strategy_id}):**\n'
        f'trigger_col = {trigger_col} | filter_col = {filter_col}\n'
        f'trigger_at_cross = {bool(out.bars[trigger_col].iloc[cross_bar])}\n'
        f'armed_at_cross = {armed_at_cross} | entry_at_cross = {entry_at_cross}'
    )

    assert bool(out.bars[trigger_col].iloc[cross_bar])
    assert armed_at_cross
    assert entry_at_cross


@pytest.mark.parametrize('strategy_id', STRATEGY_IDS)
def test_second_entry_same_day_blocked(strategy_id: str) -> None:
    """Two trigger edges same day with filters always passing → one ``entry_event``."""
    strategy = load_strategy(strategy_id)
    trigger_rule = first_cross_above_trigger(strategy)
    if trigger_rule is None:
        pytest.skip(f'{strategy_id}: no cross_above trigger')
    if not strategy.filters:
        pytest.skip(f'{strategy_id}: no filters')

    filter_rule = strategy.filters[0]
    cross_bars = (2, 5)
    bar_count = 6
    series_kw = build_cross_above_series_at_bars(trigger_rule, bar_count, cross_bars)
    pass_val = filter_pass_value(filter_rule)
    series_kw[filter_rule.column] = pd.Series([pass_val] * bar_count)
    frame = frame_with_session(strategy, **series_kw)
    out = SignalPipeline(strategy).run(frame)

    event_count = int(out.bars[ENTRY_EVENT_COLUMN].sum())
    fired_today_last = bool(out.bars[STRATEGY_FIRED_TODAY_COLUMN].iloc[-1])

    print(
        f'**summary for one entry per day ({strategy_id}):**\n'
        f'entry_event_count = {event_count} | strategy_fired_today_last = {fired_today_last}'
    )

    assert event_count == 1
    assert fired_today_last


@pytest.mark.parametrize('strategy_id', STRATEGY_IDS)
def test_entry_requires_signal_eligible(strategy_id: str) -> None:
    strategy = load_strategy(strategy_id)
    trigger_rule = first_cross_above_trigger(strategy)
    if trigger_rule is None:
        pytest.skip(f'{strategy_id}: no cross_above trigger')
    trigger_col = trigger_column_name(trigger_rule.id)

    frame = frame_with_session(
        strategy,
        **build_cross_above_series(trigger_rule, 3, 1),
    )
    frame = frame.with_columns(signal_eligible=pd.Series([True, False, True]))
    trigger_cols = {trigger_col: pd.Series([False, True, False])}
    filters_ok = pd.Series([True, True, True], index=frame.bars.index)

    out = apply_entry_columns(
        frame,
        strategy,
        trigger_cols,
        all_filters_ok=filters_ok,
    )
    entry_1 = bool(out.bars[ENTRY_EVENT_COLUMN].iloc[1])
    entry_2 = bool(out.bars[ENTRY_EVENT_COLUMN].iloc[2])

    print(
        f'**summary for signal_eligible gate ({strategy_id}):**\n'
        f'entry_bar_1 = {entry_1} | entry_bar_2 = {entry_2}'
    )

    assert not entry_1
    assert entry_2
