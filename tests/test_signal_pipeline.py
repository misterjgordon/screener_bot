"""Trigger edge and filter level columns from StrategyConfig (step 7) — any strategy YAML."""

import pandas as pd
import pytest

from backtesting.signals.filter_evaluator import filter_level_series
from backtesting.signals.signal_columns import ALL_FILTERS_OK_COLUMN
from backtesting.signals.signal_columns import SignalColumnError
from backtesting.signals.signal_columns import filter_column_name
from backtesting.signals.signal_columns import trigger_column_name
from backtesting.signals.signal_pipeline import SignalPipeline
from backtesting.signals.trigger_evaluator import trigger_edge_series
from backtesting.strategy.strategy_config import FilterRule
from backtesting.strategy.strategy_config import TriggerRule
from tests.strategy_signal_test_support import build_cross_above_series
from tests.strategy_signal_test_support import build_cross_below_series
from tests.strategy_signal_test_support import build_filter_level_series
from tests.strategy_signal_test_support import discover_strategy_ids
from tests.strategy_signal_test_support import filter_column_names
from tests.strategy_signal_test_support import first_cross_above_trigger
from tests.strategy_signal_test_support import frame_with_session
from tests.strategy_signal_test_support import load_strategy
from tests.strategy_signal_test_support import trigger_column_names

STRATEGY_IDS = discover_strategy_ids()


def test_trigger_cross_above_edge() -> None:
    rule = TriggerRule(
        id='fast_cross_slow',
        column='fast_line',
        op='cross_above',
        ref_column='slow_line',
    )
    frame = frame_with_session(
        load_strategy(STRATEGY_IDS[0]),
        **build_cross_above_series(rule, 3, 2),
    )
    edge = trigger_edge_series(frame, rule)

    edge_at_0 = bool(edge.iloc[0])
    edge_at_1 = bool(edge.iloc[1])
    edge_at_2 = bool(edge.iloc[2])

    print(
        '**summary for cross_above edge:**\n'
        f'edge_at_0 = {edge_at_0} | edge_at_1 = {edge_at_1} | edge_at_2 = {edge_at_2}'
    )

    assert not edge_at_0
    assert not edge_at_1
    assert edge_at_2


def test_trigger_cross_below_edge() -> None:
    rule = TriggerRule(
        id='fast_cross_slow_down',
        column='fast_line',
        op='cross_below',
        ref_column='slow_line',
    )
    frame = frame_with_session(
        load_strategy(STRATEGY_IDS[0]),
        **build_cross_below_series(rule, 3, 2),
    )
    edge = trigger_edge_series(frame, rule)

    edge_at_2 = bool(edge.iloc[2])

    print(f'**summary for cross_below edge:**\nedge_at_2 = {edge_at_2}')

    assert edge_at_2


def test_filter_ge_threshold() -> None:
    rule = FilterRule(id='metric_ge', column='metric', op='>=', value=1.5)
    strategy = load_strategy(STRATEGY_IDS[0])
    frame = frame_with_session(
        strategy,
        metric=build_filter_level_series(rule, 3, pass_bars={1, 2}),
    )
    levels = filter_level_series(frame, rule)

    ok_0 = bool(levels.iloc[0])
    ok_1 = bool(levels.iloc[1])
    ok_2 = bool(levels.iloc[2])

    print(
        '**summary for >= filter:**\n'
        f'threshold = {rule.value}\n'
        f'ok_0 = {ok_0} | ok_1 = {ok_1} | ok_2 = {ok_2}'
    )

    assert not ok_0
    assert ok_1
    assert ok_2


@pytest.mark.parametrize('strategy_id', STRATEGY_IDS)
def test_signal_pipeline_adds_rule_columns(strategy_id: str) -> None:
    strategy = load_strategy(strategy_id)
    cross_trigger = first_cross_above_trigger(strategy)
    if cross_trigger is None:
        pytest.skip(f'{strategy_id}: no cross_above trigger')
    if not strategy.filters:
        pytest.skip(f'{strategy_id}: no filters')

    trigger_rule = cross_trigger
    filter_rule = strategy.filters[0]
    cross_bar = 2
    bar_count = 4
    series_kw = build_cross_above_series(trigger_rule, bar_count, cross_bar)
    series_kw[filter_rule.column] = build_filter_level_series(
        filter_rule,
        bar_count,
        pass_bars={cross_bar},
    )
    frame = frame_with_session(strategy, **series_kw)
    out = SignalPipeline(strategy).run(frame)

    expected_triggers = trigger_column_names(strategy)
    expected_filters = filter_column_names(strategy)
    trigger_col = trigger_column_name(trigger_rule.id)
    filter_col = filter_column_name(filter_rule.id)

    triggers_present = all(col in out.column_names for col in expected_triggers)
    filters_present = all(col in out.column_names for col in expected_filters)
    trigger_on_cross = bool(out.bars[trigger_col].iloc[cross_bar])
    filter_on_cross = bool(out.bars[filter_col].iloc[cross_bar])
    all_ok_on_cross = bool(out.bars[ALL_FILTERS_OK_COLUMN].iloc[cross_bar])
    filter_off_later = not bool(out.bars[filter_col].iloc[3])

    print(
        f'**summary for SignalPipeline ({strategy_id}):**\n'
        f'triggers_present = {triggers_present} | filters_present = {filters_present}\n'
        f'trigger_on_cross = {trigger_on_cross} | filter_on_cross = {filter_on_cross}\n'
        f'all_filters_ok_on_cross = {all_ok_on_cross} | filter_off_bar_3 = {filter_off_later}'
    )

    assert triggers_present
    assert filters_present
    assert ALL_FILTERS_OK_COLUMN in out.column_names
    assert trigger_on_cross
    assert filter_on_cross
    assert all_ok_on_cross
    assert filter_off_later


def test_trigger_missing_column_raises() -> None:
    rule = TriggerRule(
        id='bad',
        column='missing_col',
        op='cross_above',
        ref_column='slow_line',
    )
    strategy = load_strategy(STRATEGY_IDS[0])
    frame = frame_with_session(strategy, slow_line=pd.Series([1.0, 2.0]))

    with pytest.raises(SignalColumnError, match='missing_col'):
        trigger_edge_series(frame, rule)
