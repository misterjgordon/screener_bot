"""Apply strategy trigger and filter columns to a prepped ``SymbolBarFrame``."""

from typing import TYPE_CHECKING


from backtesting.signals.arming import apply_entry_columns
from backtesting.signals.filter_evaluator import all_filters_ok_series
from backtesting.signals.filter_evaluator import evaluate_filter_columns
from backtesting.signals.signal_columns import ALL_FILTERS_OK_COLUMN
from backtesting.signals.signal_columns import ALL_TRIGGERS_OK_COLUMN
from backtesting.signals.trigger_evaluator import evaluate_trigger_columns

if TYPE_CHECKING:
    import pandas as pd
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import StrategyConfig


class SignalPipeline:
    """Add trigger/filter columns and entry latch columns from :class:`StrategyConfig`.

    Reads existing indicator and condition columns only (no indicator math). Run
    after :class:`~backtesting.conditions.condition_pipeline.ConditionPipeline` so
    ``signal_eligible`` is present for entry composition.
    """

    def __init__(self, strategy: 'StrategyConfig') -> None:
        self._strategy = strategy

    @property
    def strategy(self) -> 'StrategyConfig':
        return self._strategy

    def run(self, frame: 'SymbolBarFrame') -> 'SymbolBarFrame':
        """Return a new frame with trigger, filter, arming, and entry event columns."""
        if frame.bars.empty:
            return frame

        assign_kw: dict[str, pd.Series] = {}
        trigger_cols = evaluate_trigger_columns(frame, self._strategy.triggers)
        assign_kw.update(trigger_cols)

        filter_cols = evaluate_filter_columns(frame, self._strategy.filters)
        assign_kw.update(filter_cols)

        filters_ok = all_filters_ok_series(filter_cols, bar_index=frame.bars.index)
        if filter_cols:
            assign_kw[ALL_FILTERS_OK_COLUMN] = filters_ok

        if len(trigger_cols) > 1:
            names = sorted(trigger_cols)
            combined = trigger_cols[names[0]].copy()
            for name in names[1:]:
                combined = combined & trigger_cols[name]
            assign_kw[ALL_TRIGGERS_OK_COLUMN] = combined.astype('bool')

        result = frame.with_columns(**assign_kw) if assign_kw else frame
        return apply_entry_columns(
            result,
            self._strategy,
            trigger_cols,
            all_filters_ok=filters_ok,
        )
