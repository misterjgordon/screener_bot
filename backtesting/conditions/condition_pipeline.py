"""Condition columns on ``SymbolBarFrame``: signal_eligible gate + optional registry conditions."""

from typing import TYPE_CHECKING

from backtesting.conditions.condition_registry import CONDITION_REGISTRY
from backtesting.conditions.condition_registry import _compute_vwap_conditions
from backtesting.conditions.condition_registry import vwap_condition_column_series
from backtesting.conditions.session_regime import SESSION_COLUMN
from backtesting.conditions.session_regime import SIGNAL_ELIGIBLE_COLUMN
from backtesting.conditions.session_regime import signal_eligible_series

if TYPE_CHECKING:
    import pandas as pd
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import SessionConfig


def _series_from_condition_compute(
    frame: 'SymbolBarFrame',
    compute_fn: object,
) -> dict[str, 'pd.Series']:
    """Return new columns from a registered condition ``compute_fn``."""
    if compute_fn is _compute_vwap_conditions:
        return vwap_condition_column_series(frame)
    before = set(frame.column_names)
    result = compute_fn(frame)
    return {
        col: result.bars[col]
        for col in result.column_names
        if col not in before
    }


class ConditionPipeline:
    """Apply multi-bar regime columns: structural session gates, then optional registry ids.

    Session columns (``session``, ``signal_eligible``) are **conditions** in the mental
    model — true across many bars — but are always derived from ``SessionConfig`` when
    a strategy runs, not from ``CONDITION_REGISTRY``.
    """

    def __init__(
        self,
        condition_ids: tuple[str, ...] = (),
        *,
        session_config: 'SessionConfig | None' = None,
    ) -> None:
        if not condition_ids and session_config is None:
            msg = 'Provide condition_ids and/or session_config'
            raise ValueError(msg)
        self._condition_ids = condition_ids
        self._session_config = session_config
        if condition_ids:
            CONDITION_REGISTRY.validate_ids(condition_ids)

    @property
    def condition_ids(self) -> tuple[str, ...]:
        return self._condition_ids

    @property
    def session_config(self) -> 'SessionConfig | None':
        return self._session_config

    def run(self, frame: 'SymbolBarFrame') -> 'SymbolBarFrame':
        """Return a new frame with signal_eligible and/or registry condition columns.

        ``session`` is expected to already be present from the indicator pipeline.
        """
        assign_kw: dict[str, pd.Series] = {}
        if self._session_config is not None:
            ts = frame.bars.timestamp
            if SESSION_COLUMN not in frame.bars.columns:
                msg = (
                    f'bars missing {SESSION_COLUMN!r}; ensure session is in the indicator pipeline '
                    f'(default_pipeline_ids includes it)'
                )
                raise ValueError(msg)
            assign_kw[SIGNAL_ELIGIBLE_COLUMN] = signal_eligible_series(
                frame.bars[SESSION_COLUMN],
                ts,
                self._session_config,
            )

        seen_compute: set[int] = set()
        for condition_id in self._condition_ids:
            spec = CONDITION_REGISTRY.spec(condition_id)
            fn_key = id(spec.compute_fn)
            if fn_key in seen_compute:
                continue
            seen_compute.add(fn_key)
            assign_kw.update(_series_from_condition_compute(frame, spec.compute_fn))

        if not assign_kw:
            return frame
        return frame.with_columns(**assign_kw)
