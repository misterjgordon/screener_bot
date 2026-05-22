"""Declarative indicator metadata for the backtest indicator registry."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from backtesting.frames.symbol_bar_frame import SymbolBarFrame

IndicatorComputeFn = Callable[[SymbolBarFrame], SymbolBarFrame]


class IndicatorKind(StrEnum):
    """Kind of column an indicator produces on ``SymbolBarFrame.bars``."""

    INDICATOR = 'indicator'
    SESSION_COLUMN = 'session_column'


@dataclass(frozen=True)
class IndicatorSpec:
    """One registry entry: id, metadata, and a picklable compute function."""

    id: str
    kind: IndicatorKind
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    description: str
    version: str
    compute_fn: IndicatorComputeFn

    def to_catalog_dict(self) -> dict[str, str | tuple[str, ...]]:
        """JSON-serializable metadata (no ``compute_fn``)."""
        return {
            'id': self.id,
            'kind': self.kind.value,
            'inputs': self.inputs,
            'outputs': self.outputs,
            'description': self.description,
            'version': self.version,
        }
