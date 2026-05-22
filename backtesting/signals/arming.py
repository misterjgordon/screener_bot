"""Arming window and per-day entry latch after triggers and filters."""

from typing import TYPE_CHECKING

import pandas as pd

from backtesting.conditions.session_regime import SIGNAL_ELIGIBLE_COLUMN
from backtesting.signals.entry_columns import ARMED_COLUMN
from backtesting.signals.entry_columns import ENTRY_EVENT_COLUMN
from backtesting.signals.entry_columns import ENTRY_SIGNAL_COLUMN
from backtesting.signals.entry_columns import STRATEGY_FIRED_TODAY_COLUMN
from backtesting.signals.entry_columns import TRADING_DATE_COLUMN
from backtesting.signals.signal_columns import SignalColumnError

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import StrategyConfig


def any_trigger_fired_series(trigger_columns: dict[str, 'pd.Series']) -> 'pd.Series':
    """OR of all ``trigger_*`` columns (False when there are no triggers)."""
    if not trigger_columns:
        msg = 'entry logic requires at least one trigger column'
        raise SignalColumnError(msg)
    names = sorted(trigger_columns)
    combined = trigger_columns[names[0]].astype('bool').copy()
    for name in names[1:]:
        combined = combined | trigger_columns[name].astype('bool')
    return combined.fillna(False).astype('bool')


def armed_series(
    trigger_fired: 'pd.Series',
    trading_date: 'pd.Series',
    arming_window: int,
) -> 'pd.Series':
    """True for ``arming_window`` bars after any trigger edge, reset each ``trading_date``.

    Includes the trigger bar and the next ``arming_window - 1`` bars (within the same day).
    """
    if arming_window < 1:
        msg = f'arming_window must be >= 1, got {arming_window}'
        raise ValueError(msg)

    def _armed_for_day(day_triggers: 'pd.Series') -> 'pd.Series':
        armed = day_triggers.astype('bool').copy()
        for offset in range(1, arming_window):
            armed = armed | day_triggers.shift(offset, fill_value=False).astype('bool')
        return armed.fillna(False).astype('bool')

    return trigger_fired.groupby(trading_date, sort=False).transform(_armed_for_day)


def entry_signal_series(
    armed: 'pd.Series',
    all_filters_ok: 'pd.Series',
    signal_eligible: 'pd.Series',
) -> 'pd.Series':
    """Raw entry candidate: armed setup, filters OK on this bar, session regime allows."""
    return (armed & all_filters_ok & signal_eligible).astype('bool')


def strategy_fired_today_series(
    entry_signal: 'pd.Series',
    trading_date: 'pd.Series',
) -> 'pd.Series':
    """True from the first ``entry_signal`` on each ``trading_date`` through end of that day."""
    return entry_signal.groupby(trading_date, sort=False).transform('cummax').astype('bool')


def entry_event_series(
    entry_signal: 'pd.Series',
    trading_date: 'pd.Series',
    *,
    entry_rule: str,
) -> 'pd.Series':
    """Rows emitted to the portfolio sim (``entry_rule: first`` per ``trading_date``)."""
    if entry_rule != 'first':
        msg = f'Unsupported entry_rule: {entry_rule!r}'
        raise ValueError(msg)

    prior_fired = (
        entry_signal.groupby(trading_date, sort=False)
        .cumsum()
        .shift(1, fill_value=0)
        .gt(0)
    )
    return (entry_signal & ~prior_fired).astype('bool')


def apply_entry_columns(
    frame: 'SymbolBarFrame',
    strategy: 'StrategyConfig',
    trigger_columns: dict[str, 'pd.Series'],
    *,
    all_filters_ok: 'pd.Series | None' = None,
) -> 'SymbolBarFrame':
    """Add ``armed``, ``entry_signal``, ``strategy_fired_today``, and ``entry_event``."""
    if TRADING_DATE_COLUMN not in frame.column_names:
        msg = f'{frame.symbol}: missing {TRADING_DATE_COLUMN!r} for day_boundary'
        raise SignalColumnError(msg)
    if SIGNAL_ELIGIBLE_COLUMN not in frame.column_names:
        msg = (
            f'{frame.symbol}: missing {SIGNAL_ELIGIBLE_COLUMN!r}; '
            'run ConditionPipeline(session_config=...) first'
        )
        raise SignalColumnError(msg)

    if strategy.day_boundary != 'session':
        msg = f'Unsupported day_boundary: {strategy.day_boundary!r}'
        raise ValueError(msg)

    trading_date = frame.bars[TRADING_DATE_COLUMN]
    trigger_fired = any_trigger_fired_series(trigger_columns)
    armed = armed_series(trigger_fired, trading_date, strategy.arming_window)

    if all_filters_ok is None:
        filters_ok = pd.Series(True, index=frame.bars.index, dtype='bool')
    else:
        filters_ok = all_filters_ok.astype('bool')

    eligible = frame.bars[SIGNAL_ELIGIBLE_COLUMN].astype('bool')
    entry_signal = entry_signal_series(armed, filters_ok, eligible)
    fired_today = strategy_fired_today_series(entry_signal, trading_date)
    entry_event = entry_event_series(
        entry_signal,
        trading_date,
        entry_rule=strategy.entry_rule,
    )

    return frame.with_columns(
        **{
            ARMED_COLUMN: armed,
            ENTRY_SIGNAL_COLUMN: entry_signal,
            STRATEGY_FIRED_TODAY_COLUMN: fired_today,
            ENTRY_EVENT_COLUMN: entry_event,
        },
    )
