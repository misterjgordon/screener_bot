"""Column names and combinators for strategy trigger/filter diagnostics on ``SymbolBarFrame.bars``."""

import pandas as pd

from backtesting.signals.entry_columns import ARMED_COLUMN
from backtesting.signals.entry_columns import ENTRY_EVENT_COLUMN
from backtesting.signals.entry_columns import ENTRY_SIGNAL_COLUMN
from backtesting.signals.entry_columns import STRATEGY_FIRED_TODAY_COLUMN


class SignalColumnError(ValueError):
    """Raised when trigger/filter rules reference columns missing from ``bars``."""


ALL_FILTERS_OK_COLUMN = 'all_filters_ok'
ALL_TRIGGERS_OK_COLUMN = 'all_triggers_ok'
SIGNAL_EXIT_HIT_COLUMN = 'signal_exit_hit'


def trigger_column_name(trigger_id: str) -> str:
    """Edge boolean column for one :class:`~backtesting.strategy.strategy_config.TriggerRule`."""
    return f'trigger_{trigger_id}'


def filter_column_name(filter_id: str) -> str:
    """Level boolean column for one :class:`~backtesting.strategy.strategy_config.FilterRule`."""
    return f'filter_{filter_id}'


def exit_trigger_column_name(trigger_id: str) -> str:
    """Edge boolean column for one ``exit_triggers`` rule."""
    return f'exit_trigger_{trigger_id}'


def exit_filter_column_name(filter_id: str) -> str:
    """Level boolean column for one ``exit_filters`` rule."""
    return f'exit_filter_{filter_id}'


def any_true_series(
    columns: dict[str, 'pd.Series'],
    *,
    bar_index: 'pd.Index',
) -> 'pd.Series':
    """OR across a dict of boolean columns; all False when the dict is empty."""
    if not columns:
        return pd.Series(False, index=bar_index, dtype='bool')
    combined = columns[sorted(columns)[0]].copy()
    for name in sorted(columns)[1:]:
        combined = combined | columns[name]
    return combined.astype('bool')


def signal_diagnostic_column_names(
    trigger_ids: tuple[str, ...],
    filter_ids: tuple[str, ...],
    *,
    include_entry_columns: bool = False,
) -> tuple[str, ...]:
    """Trigger, filter, aggregate, and optional entry column names from :class:`SignalPipeline`."""
    cols: list[str] = [trigger_column_name(tid) for tid in trigger_ids]
    cols.extend(filter_column_name(fid) for fid in filter_ids)
    if filter_ids:
        cols.append(ALL_FILTERS_OK_COLUMN)
    if len(trigger_ids) > 1:
        cols.append(ALL_TRIGGERS_OK_COLUMN)
    if include_entry_columns:
        cols.extend(
            (
                ARMED_COLUMN,
                ENTRY_SIGNAL_COLUMN,
                STRATEGY_FIRED_TODAY_COLUMN,
                ENTRY_EVENT_COLUMN,
            ),
        )
    return tuple(cols)
