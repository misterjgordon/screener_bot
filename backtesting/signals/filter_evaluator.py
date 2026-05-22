"""Level filter columns from :class:`~backtesting.strategy.strategy_config.FilterRule`."""

from typing import TYPE_CHECKING

import pandas as pd

from backtesting.signals.signal_columns import SignalColumnError
from backtesting.signals.signal_columns import filter_column_name

if TYPE_CHECKING:
    from backtesting.strategy.strategy_config import FilterOp
    from backtesting.strategy.strategy_config import FilterRule
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame


def _require_bar_columns(frame: 'SymbolBarFrame', column_names: tuple[str, ...]) -> None:
    missing = [name for name in column_names if name not in frame.column_names]
    if missing:
        msg = f'{frame.symbol}: missing bar columns for filters: {missing}'
        raise SignalColumnError(msg)


def _compare_level(series: 'pd.Series', op: 'FilterOp', threshold: float | int | bool) -> 'pd.Series':
    if op == '>=':
        result = series >= threshold
    elif op == '<=':
        result = series <= threshold
    elif op == '>':
        result = series > threshold
    elif op == '<':
        result = series < threshold
    elif op == '==':
        result = series == threshold
    else:
        result = series != threshold
    return result.fillna(False).astype('bool')


def filter_level_series(
    frame: 'SymbolBarFrame',
    rule: 'FilterRule',
) -> 'pd.Series':
    """Evaluate one filter rule as a level boolean series (read ``bars[rule.column]`` only)."""
    _require_bar_columns(frame, (rule.column,))
    return _compare_level(frame.bars[rule.column], rule.op, rule.value)


def evaluate_filter_columns(
    frame: 'SymbolBarFrame',
    filters: tuple['FilterRule', ...],
) -> dict[str, 'pd.Series']:
    """Map ``filter_<id>`` column names to level boolean series."""
    columns: dict[str, pd.Series] = {}
    for rule in filters:
        columns[filter_column_name(rule.id)] = filter_level_series(frame, rule)
    return columns


def all_filters_ok_series(
    filter_columns: dict[str, 'pd.Series'],
    *,
    bar_index: 'pd.Index',
) -> 'pd.Series':
    """AND of all filter columns; all True when there are no filters."""
    if not filter_columns:
        return pd.Series(True, index=bar_index, dtype='bool')
    combined = filter_columns[sorted(filter_columns)[0]].copy()
    for name in sorted(filter_columns)[1:]:
        combined = combined & filter_columns[name]
    return combined.astype('bool')
