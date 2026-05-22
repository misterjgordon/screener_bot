"""Regime columns on ``SymbolBarFrame``: session gates + optional registry conditions."""

from typing import TYPE_CHECKING

from backtesting.conditions.condition_registry import CONDITION_REGISTRY
from backtesting.conditions.session_regime import apply_session_columns

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import SessionConfig


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
        """Return a new frame with session and/or registry condition columns."""
        result = frame
        if self._session_config is not None:
            result = apply_session_columns(result, self._session_config)
        seen_compute: set[int] = set()
        for condition_id in self._condition_ids:
            spec = CONDITION_REGISTRY.spec(condition_id)
            fn_key = id(spec.compute_fn)
            if fn_key in seen_compute:
                continue
            seen_compute.add(fn_key)
            result = spec.compute_fn(result)
        return result
