"""Load ``strategies/configs/ema_cross.yaml`` into :class:`StrategyConfig`."""

import pytest

from backtesting.indicators.indicator_catalog_load import default_indicator_ids
from backtesting.indicators.indicator_catalog_load import topological_indicator_order
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY
from backtesting.strategy.strategy_config import StrategyConfig
from backtesting.strategy.strategy_loader import StrategyConfigLoadError
from backtesting.strategy.strategy_loader import load_strategy_config
from backtesting.strategy.strategy_loader import resolve_strategy_config_path

STRATEGY_ID = 'ema_cross'


def test_resolve_strategy_config_path_by_id() -> None:
    p_config = resolve_strategy_config_path(STRATEGY_ID)
    path_ok = p_config.name == f'{STRATEGY_ID}.yaml'
    is_file = p_config.is_file()

    print(
        '**summary for resolve_strategy_config_path:**\n'
        f'strategy_id = {STRATEGY_ID}\n'
        f'path = {p_config}\n'
        f'path_ok = {path_ok} | is_file = {is_file}'
    )

    assert path_ok
    assert is_file


def test_load_ema_cross_strategy_config() -> None:
    config = load_strategy_config(STRATEGY_ID)
    trigger_id = config.triggers[0].id
    filter_col = config.filters[0].column
    session_tz = config.session_config.timezone
    indicator_ids = config.indicator_ids_for_pipeline(
        default_indicator_ids(),
        frozenset(INDICATOR_REGISTRY.ids()),
    )
    has_ema21 = 'ema21' in indicator_ids
    has_rvol_in_pipeline = 'rvol' in indicator_ids
    has_rvol = 'rvol' in config.referenced_bar_columns()
    ordered = topological_indicator_order(indicator_ids)
    vwap_after_td = ordered.index('vwap') > ordered.index('trading_date')

    print(
        '**summary for load ema_cross StrategyConfig:**\n'
        f'id = {config.id} | version = {config.version}\n'
        f'trigger_id = {trigger_id} | filter_column = {filter_col}\n'
        f'session_timezone = {session_tz}\n'
        f'indicator_ids = {list(indicator_ids)}\n'
        f'has_ema21_in_pipeline = {has_ema21} | has_rvol_in_pipeline = {has_rvol_in_pipeline}\n'
        f'references_rvol = {has_rvol} | vwap_after_trading_date = {vwap_after_td}'
    )

    assert isinstance(config, StrategyConfig)
    assert config.id == STRATEGY_ID
    assert config.version == '1.0'
    assert config.signal_timeframe_minutes == 1
    assert config.session_config.allowed_sessions == ('RTH',)
    assert config.session_config.intraday_start == '09:30'
    assert config.session_config.intraday_end == '11:30'
    assert trigger_id == 'ema9_cross_above_ema21'
    assert config.triggers[0].op == 'cross_above'
    assert config.triggers[0].ref_column == 'ema21'
    assert filter_col == 'rvol'
    assert config.arming_window == 30
    assert config.entry_rule == 'first'
    assert config.stop_loss.type == 'pct_from_entry'
    assert config.stop_loss.pct == pytest.approx(0.02)
    assert config.take_profit.type == 'pct_from_entry'
    assert config.take_profit.pct == pytest.approx(0.04)
    assert len(config.other_exits) == 1
    assert config.other_exits[0].id == 'end_of_session'
    assert config.other_exits[0].type == 'end_of_session'
    assert config.sizing.method == 'fixed_dollars'
    assert config.sizing.amount == 5000
    assert has_ema21
    assert has_rvol
    assert has_rvol_in_pipeline
    assert vwap_after_td


def test_load_strategy_config_missing_file() -> None:
    with pytest.raises(StrategyConfigLoadError, match='not found'):
        load_strategy_config('no_such_strategy_xyz')
