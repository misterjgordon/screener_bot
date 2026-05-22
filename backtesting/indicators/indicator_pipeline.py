"""Run registered indicators on a ``SymbolBarFrame``."""

from typing import TYPE_CHECKING

from backtesting.frames.bar_price_round import normalize_symbol_bar_frame_prices
from backtesting.indicators.indicator_catalog_load import default_indicator_ids
from backtesting.indicators.indicator_catalog_load import topological_indicator_order
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame


class IndicatorPipeline:
    """Apply catalog indicators to one symbol's bars in dependency order."""

    def __init__(self, indicator_ids: tuple[str, ...] | None = None) -> None:
        raw_ids = indicator_ids if indicator_ids is not None else default_indicator_ids()
        self._indicator_ids = topological_indicator_order(raw_ids)
        INDICATOR_REGISTRY.validate_ids(self._indicator_ids)

    @property
    def indicator_ids(self) -> tuple[str, ...]:
        return self._indicator_ids

    def run(self, frame: 'SymbolBarFrame') -> 'SymbolBarFrame':
        """Return a new frame with all configured indicator columns assigned."""
        result = frame
        for indicator_id in self._indicator_ids:
            spec = INDICATOR_REGISTRY.spec(indicator_id)
            result = spec.compute_fn(result)
        return normalize_symbol_bar_frame_prices(result)
