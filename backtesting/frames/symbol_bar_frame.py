"""Typed per-symbol OHLCV table for the backtest pipeline."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True)
class SymbolBarFrame:
    """One symbol's working bars: UTC timestamps, cold-store OHLCV, and derived columns.

    ``bars`` is a sorted DataFrame whose ``timestamp`` column is UTC tz-aware.
    OHLC and ``vwap`` are cent-aligned ``float64`` after cold load (see
    :mod:`backtesting.frames.bar_price_round`); Parquet on disk stays ``float32``.
    ``daily_bars`` holds RTH-only daily OHLCV for indicators that need prior sessions
    (ADR, ATR). ``history_bars`` is additional 1m history (prior session days) for RVOL
    at elapsed session time. Pipelines return a new ``SymbolBarFrame`` via ``with_columns``
    rather than mutating the frame in place.
    """

    symbol: str
    interval_minutes: int
    bars: 'pd.DataFrame'
    daily_bars: 'pd.DataFrame | None' = None
    history_bars: 'pd.DataFrame | None' = None

    @property
    def column_names(self) -> list[str]:
        """Current column names on ``bars``."""
        return list(self.bars.columns)

    def with_columns(self, **assign_kw: 'pd.Series') -> 'SymbolBarFrame':
        """Return a new frame with extra or replaced columns assigned to ``bars``."""
        return SymbolBarFrame(
            symbol=self.symbol,
            interval_minutes=self.interval_minutes,
            bars=self.bars.assign(**assign_kw),
            daily_bars=self.daily_bars,
            history_bars=self.history_bars,
        )

    def with_daily_bars(self, daily_bars: 'pd.DataFrame') -> 'SymbolBarFrame':
        """Return a new frame with ``daily_bars`` replaced."""
        return SymbolBarFrame(
            symbol=self.symbol,
            interval_minutes=self.interval_minutes,
            bars=self.bars,
            daily_bars=daily_bars,
            history_bars=self.history_bars,
        )

    def to_parquet(self, p_out: Path) -> None:
        """Write ``bars`` to Parquet at ``p_out`` (parent directory must exist)."""
        self.bars.to_parquet(p_out, index=False)
