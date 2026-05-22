"""Declarative strategy condition metadata for the condition registry."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from backtesting.frames.symbol_bar_frame import SymbolBarFrame

ConditionComputeFn = Callable[[SymbolBarFrame], SymbolBarFrame]


class ConditionKind(StrEnum):
    """Kind of boolean column a strategy condition produces."""

    FILTER = 'filter'
    TRIGGER = 'trigger'


@dataclass(frozen=True)
class ConditionSpec:
    """One registry entry: id, metadata, and a picklable compute function."""

    id: str
    kind: ConditionKind
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    description: str
    version: str
    compute_fn: ConditionComputeFn

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
