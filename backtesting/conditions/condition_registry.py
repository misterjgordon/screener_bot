"""Condition registry: strategy-specific boolean columns (filters, triggers)."""

import json
from typing import TYPE_CHECKING

from backtesting.conditions.condition_spec import ConditionKind
from backtesting.conditions.condition_spec import ConditionSpec

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame


class ConditionRegistry:
    """Single source of registered strategy conditions (not on bars unless requested)."""

    def __init__(self) -> None:
        self._entries: dict[str, ConditionSpec] = {}

    def register(self, spec: ConditionSpec) -> None:
        """Register a condition; ``compute_fn`` must be a module-level function (not a lambda)."""
        if spec.id in self._entries:
            msg = f'Condition {spec.id!r} already registered'
            raise ValueError(msg)
        fn_name = getattr(spec.compute_fn, '__name__', '')
        if fn_name == '<lambda>':
            msg = f'Condition {spec.id!r} compute_fn must not be a lambda'
            raise ValueError(msg)
        self._entries[spec.id] = spec

    def spec(self, condition_id: str) -> ConditionSpec:
        """Return the spec for ``condition_id``."""
        try:
            return self._entries[condition_id]
        except KeyError as exc:
            msg = f'Unknown condition id: {condition_id!r}'
            raise KeyError(msg) from exc

    def ids(self) -> tuple[str, ...]:
        """Sorted registered condition ids."""
        return tuple(sorted(self._entries))

    def validate_ids(self, condition_ids: tuple[str, ...]) -> None:
        """Raise ``ValueError`` if any id is not registered."""
        unknown = [cid for cid in condition_ids if cid not in self._entries]
        if unknown:
            msg = f'Unknown condition ids: {unknown}'
            raise ValueError(msg)

    def output_columns_for(self, condition_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Output column names for ``condition_ids`` in order (deduped)."""
        seen: set[str] = set()
        out: list[str] = []
        for condition_id in condition_ids:
            for col in self.spec(condition_id).outputs:
                if col in seen:
                    continue
                out.append(col)
                seen.add(col)
        return tuple(out)

    def catalog_json(self) -> str:
        """Serialize registry metadata for CLI / future UI."""
        rows = [self._entries[cid].to_catalog_dict() for cid in self.ids()]
        return json.dumps(rows, indent=2)


CONDITION_REGISTRY = ConditionRegistry()


def _compute_vwap_conditions(frame: 'SymbolBarFrame') -> 'SymbolBarFrame':
    """Assign VWAP level filters and cross triggers (requires ``close`` and ``vwap``)."""
    if 'close_above_vwap' in frame.column_names:
        return frame
    close = frame.bars.close
    vwap_col = frame.bars.vwap
    prev_close = close.shift(1)
    prev_vwap = vwap_col.shift(1)
    return frame.with_columns(
        close_above_vwap=close > vwap_col,
        close_below_vwap=close < vwap_col,
        trigger_vwap_cross_up=(close > vwap_col) & (prev_close <= prev_vwap),
        trigger_vwap_cross_down=(close < vwap_col) & (prev_close >= prev_vwap),
    )


def _register_defaults() -> None:
    condition_meta: tuple[tuple[str, ConditionKind, str], ...] = (
        ('close_above_vwap', ConditionKind.FILTER, 'close > vwap'),
        ('close_below_vwap', ConditionKind.FILTER, 'close < vwap'),
        ('trigger_vwap_cross_up', ConditionKind.TRIGGER, 'close crosses above vwap'),
        ('trigger_vwap_cross_down', ConditionKind.TRIGGER, 'close crosses below vwap'),
    )
    for condition_id, kind, desc in condition_meta:
        CONDITION_REGISTRY.register(
            ConditionSpec(
                id=condition_id,
                kind=kind,
                inputs=('close', 'vwap'),
                outputs=(condition_id,),
                description=desc,
                version='1',
                compute_fn=_compute_vwap_conditions,
            ),
        )


_register_defaults()
