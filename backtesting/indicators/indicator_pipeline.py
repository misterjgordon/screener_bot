"""Run registered indicators on a ``SymbolBarFrame``."""


from backtesting.frames.bar_price_round import normalize_symbol_bar_frame_prices
from backtesting.frames.symbol_bar_frame import SymbolBarFrame
from backtesting.indicators.indicator_catalog_load import catalog_entry_by_id
from backtesting.indicators.indicator_catalog_load import default_indicator_ids
from backtesting.indicators.indicator_catalog_load import topological_indicator_order
from backtesting.indicators.indicator_compute import compute_indicator_series
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY


class IndicatorPipeline:
    """Apply catalog indicators to one symbol's bars in dependency order."""

    def __init__(self, indicator_ids: tuple[str, ...] | None = None) -> None:
        raw_ids = indicator_ids if indicator_ids is not None else default_indicator_ids()
        self._indicator_ids = topological_indicator_order(raw_ids)
        INDICATOR_REGISTRY.validate_ids(self._indicator_ids)

    @property
    def indicator_ids(self) -> tuple[str, ...]:
        return self._indicator_ids

    def run(self, frame: SymbolBarFrame) -> SymbolBarFrame:
        """Return a new frame with all configured indicator columns assigned."""
        if not self._indicator_ids:
            return frame

        bars = frame.bars.copy()
        working = SymbolBarFrame(
            symbol=frame.symbol,
            interval_minutes=frame.interval_minutes,
            bars=bars,
            daily_bars=frame.daily_bars,
            history_bars=frame.history_bars,
        )
        entries = catalog_entry_by_id()
        for indicator_id in self._indicator_ids:
            entry = entries[indicator_id]
            output_col = entry.outputs[0]
            bars[output_col] = compute_indicator_series(working, entry)
        result = SymbolBarFrame(
            symbol=frame.symbol,
            interval_minutes=frame.interval_minutes,
            bars=bars,
            daily_bars=frame.daily_bars,
            history_bars=frame.history_bars,
        )
        return normalize_symbol_bar_frame_prices(result)
