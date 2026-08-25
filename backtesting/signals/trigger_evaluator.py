"""Edge trigger columns from :class:`~backtesting.strategy.strategy_config.TriggerRule`."""

from typing import TYPE_CHECKING

import pandas as pd

from backtesting.signals.signal_columns import SignalColumnError
from backtesting.signals.signal_columns import trigger_column_name

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import TriggerOp
    from backtesting.strategy.strategy_config import TriggerRule


def _require_bar_columns(frame: 'SymbolBarFrame', column_names: tuple[str, ...]) -> None:
    missing = [name for name in column_names if name not in frame.column_names]
    if missing:
        msg = f'{frame.symbol}: missing bar columns for triggers: {missing}'
        raise SignalColumnError(msg)


def _cross_edge_series(
    left: 'pd.Series',
    right: 'pd.Series',
    op: 'TriggerOp',
) -> 'pd.Series':
    """One-bar edge: ``cross_above`` or ``cross_below`` (NaN rows → False)."""
    prev_left = left.shift(1)
    prev_right = right.shift(1)
    if op == 'cross_above':
        edge = (left > right) & (prev_left <= prev_right)
    else:
        edge = (left < right) & (prev_left >= prev_right)
    return edge.fillna(False).astype('bool')


def trigger_edge_series(
    frame: 'SymbolBarFrame',
    rule: 'TriggerRule',
) -> 'pd.Series':
    """Evaluate one trigger rule as a boolean series aligned to ``frame.bars``."""
    _require_bar_columns(frame, (rule.column,))
    left = frame.bars[rule.column]

    if rule.ref_column is not None:
        _require_bar_columns(frame, (rule.ref_column,))
        right = frame.bars[rule.ref_column]
    elif rule.ref_value is not None:
        right = pd.Series(rule.ref_value, index=left.index)
    else:
        msg = f'Trigger {rule.id!r} op={rule.op!r} requires ref_column or ref_value'
        raise SignalColumnError(msg)

    return _cross_edge_series(left, right, rule.op)


def evaluate_trigger_columns(
    frame: 'SymbolBarFrame',
    triggers: tuple['TriggerRule', ...],
) -> dict[str, 'pd.Series']:
    """Map ``trigger_<id>`` column names to edge boolean series."""
    columns: dict[str, pd.Series] = {}
    for rule in triggers:
        columns[trigger_column_name(rule.id)] = trigger_edge_series(frame, rule)
    return columns
