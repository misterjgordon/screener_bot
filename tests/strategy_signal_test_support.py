"""Strategy-agnostic helpers for signal/entry tests (any ``strategies/configs/*.yaml``)."""

from datetime import date

import pandas as pd

from backtesting.conditions.condition_pipeline import ConditionPipeline
from backtesting.frames.symbol_bar_frame import SymbolBarFrame
from backtesting.signals.signal_columns import filter_column_name
from backtesting.signals.signal_columns import trigger_column_name
from backtesting.strategy.strategy_loader import P_STRATEGY_CONFIGS_DIR
from backtesting.strategy.strategy_loader import load_strategy_config
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtesting.strategy.strategy_config import TriggerRule
    from backtesting.strategy.strategy_config import StrategyConfig
    from backtesting.strategy.strategy_config import SessionConfig
    from backtesting.strategy.strategy_config import FilterRule

TRADING_DATE = date(2026, 5, 15)
TEST_SYMBOL = 'TEST'


def discover_strategy_ids() -> tuple[str, ...]:
    """Sorted strategy ids from ``strategies/configs/*.yaml``."""
    return tuple(sorted(p.stem for p in P_STRATEGY_CONFIGS_DIR.glob('*.yaml')))


def load_strategy(strategy_id: str) -> 'StrategyConfig':
    """Load one strategy config by id (stem of YAML filename)."""
    return load_strategy_config(strategy_id)


def trigger_column_names(strategy: 'StrategyConfig') -> tuple[str, ...]:
    """Output column names for all YAML triggers."""
    return tuple(trigger_column_name(rule.id) for rule in strategy.triggers)


def filter_column_names(strategy: 'StrategyConfig') -> tuple[str, ...]:
    """Output column names for all YAML filters."""
    return tuple(filter_column_name(rule.id) for rule in strategy.filters)


def test_session_config(strategy: 'StrategyConfig') -> 'SessionConfig':
    """Session config with full-day clock so synthetic bars stay ``signal_eligible``."""
    return strategy.session_config.model_copy(
        update={'intraday_start': '00:00', 'intraday_end': '23:59'},
    )


def frame_with_session(
    strategy: 'StrategyConfig',
    **series_kw: pd.Series,
) -> SymbolBarFrame:
    """Build a frame with ``trading_date``, optional columns, and session regime applied."""
    n = len(next(iter(series_kw.values())))
    base: dict[str, pd.Series] = {
        'timestamp': pd.date_range('2026-05-15 14:00', periods=n, freq='1min', tz='UTC'),
        'open': pd.Series([100.0] * n),
        'high': pd.Series([101.0] * n),
        'low': pd.Series([99.0] * n),
        'close': pd.Series([100.0] * n),
        'volume': pd.Series([1000.0] * n),
        'vwap': pd.Series([100.0] * n),
        'symbol': pd.Series([TEST_SYMBOL] * n),
        'trading_date': pd.Series([TRADING_DATE] * n),
    }
    for col in strategy.referenced_bar_columns():
        if col not in base and col not in series_kw:
            base[col] = pd.Series([0.0] * n)
    base.update(series_kw)
    frame = SymbolBarFrame(symbol=TEST_SYMBOL, interval_minutes=1, bars=pd.DataFrame(base))
    return ConditionPipeline(session_config=test_session_config(strategy)).run(frame)


def first_cross_above_trigger(strategy: 'StrategyConfig') -> 'TriggerRule | None':
    """First ``cross_above`` trigger in the strategy, if any."""
    for rule in strategy.triggers:
        if rule.op == 'cross_above' and rule.ref_column is not None:
            return rule
    return None


def build_cross_above_series(
    rule: 'TriggerRule',
    bar_count: int,
    cross_bar: int,
) -> dict[str, pd.Series]:
    """Column series where ``cross_above`` fires exactly on ``cross_bar``."""
    return build_cross_above_series_at_bars(rule, bar_count, (cross_bar,))


def build_cross_above_series_at_bars(
    rule: 'TriggerRule',
    bar_count: int,
    cross_bars: tuple[int, ...],
) -> dict[str, pd.Series]:
    """Column series with ``cross_above`` edges on each index in ``cross_bars``."""
    if rule.ref_column is None:
        msg = f'Trigger {rule.id!r} requires ref_column for cross_above fixture'
        raise ValueError(msg)

    left = pd.Series([10.0] * bar_count)
    right = pd.Series([12.0] * bar_count)
    for cross_bar in cross_bars:
        if cross_bar < 1 or cross_bar >= bar_count:
            msg = f'cross_bar must be in [1, {bar_count - 1}], got {cross_bar}'
            raise ValueError(msg)
        left.iloc[cross_bar - 1] = 11.0
        right.iloc[cross_bar - 1] = 11.5
        left.iloc[cross_bar] = 12.0
        right.iloc[cross_bar] = 11.0
    return {rule.column: left, rule.ref_column: right}


def build_cross_below_series(
    rule: 'TriggerRule',
    bar_count: int,
    cross_bar: int,
) -> dict[str, pd.Series]:
    """Column series where ``cross_below`` fires exactly on ``cross_bar``."""
    if rule.ref_column is None:
        msg = f'Trigger {rule.id!r} requires ref_column for cross_below fixture'
        raise ValueError(msg)
    if cross_bar < 1 or cross_bar >= bar_count:
        msg = f'cross_bar must be in [1, {bar_count - 1}], got {cross_bar}'
        raise ValueError(msg)

    left = pd.Series([12.0] * bar_count)
    right = pd.Series([11.0] * bar_count)
    left.iloc[cross_bar] = 10.0
    right.iloc[cross_bar] = 12.0
    return {rule.column: left, rule.ref_column: right}


def filter_pass_value(rule: 'FilterRule') -> float:
    """One numeric value that satisfies the filter op."""
    threshold = float(rule.value)
    if rule.op == '>=':
        return threshold + 0.1
    if rule.op == '>':
        return threshold + 0.1
    if rule.op == '<=':
        return threshold - 0.1
    if rule.op == '<':
        return threshold - 0.1
    if rule.op == '==':
        return threshold
    return threshold + 0.1


def filter_fail_value(rule: 'FilterRule') -> float:
    """One numeric value that fails the filter op."""
    threshold = float(rule.value)
    if rule.op == '>=':
        return threshold - 0.1
    if rule.op == '>':
        return threshold - 0.1
    if rule.op == '<=':
        return threshold + 0.1
    if rule.op == '<':
        return threshold + 0.1
    if rule.op == '==':
        return threshold + 1.0
    return threshold - 0.1


def build_filter_level_series(
    rule: 'FilterRule',
    bar_count: int,
    pass_bars: set[int],
) -> pd.Series:
    """Per-bar values for ``rule.column`` that pass only on ``pass_bars``."""
    values = [
        filter_pass_value(rule) if idx in pass_bars else filter_fail_value(rule)
        for idx in range(bar_count)
    ]
    return pd.Series(values)


def minimal_ohlcv_bars(symbol: str) -> pd.DataFrame:
    """Single-row OHLCV suitable for universe loader stubs."""
    ts = pd.Timestamp('2026-05-15 14:30:00', tz='UTC')
    return pd.DataFrame(
        {
            'timestamp': [ts],
            'open': [100.0],
            'high': [101.0],
            'low': [99.0],
            'close': [100.5],
            'volume': [1000.0],
            'vwap': [100.25],
            'symbol': [symbol],
        },
    )


def minimal_strategy_bars(strategy: 'StrategyConfig', symbol: str) -> pd.DataFrame:
    """OHLCV plus every column referenced by the strategy's triggers and filters."""
    df = minimal_ohlcv_bars(symbol)
    assign: dict[str, object] = {'trading_date': TRADING_DATE}
    for col in strategy.referenced_bar_columns():
        assign[col] = 10.0
    for rule in strategy.filters:
        if isinstance(rule.value, (int, float)):
            assign[rule.column] = filter_pass_value(rule)
    return df.assign(**assign)
