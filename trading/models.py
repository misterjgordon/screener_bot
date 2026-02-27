"""Data models for trading - structured types for IB market data and related info.

These models represent IB (ib_async) data in typed form. Market data functions
in smb_screener transform raw IB Ticker/Bar responses into these models.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Bar:
    """Single bar (OHLCV) with timestamp.

    Represents one bar from ib.reqHistoricalData (Bar from ib_async).
    """
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def range(self) -> float:
        """High - low."""
        return self.high - self.low

    @property
    def body(self) -> float:
        """Absolute body size (|close - open|)."""
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        """True if close > open."""
        return self.close > self.open


@dataclass
class BarSeries:
    """Collection of bars with calculation methods.

    Wraps bars from ib.reqHistoricalData for ADR, EMA, etc.
    """
    bars: list[Bar]
    symbol: str
    bar_size: str

    @property
    def closes(self) -> list[float]:
        """List of close prices."""
        return [b.close for b in self.bars]

    @property
    def ranges(self) -> list[float]:
        """List of daily ranges (high - low)."""
        return [b.range for b in self.bars]

    def adr(self, days: int | None = None) -> float | None:
        """Average Daily Range over specified days (default: all bars)."""
        bars_to_use = self.bars[-days:] if days else self.bars
        if not bars_to_use:
            return None
        ranges = [b.range for b in bars_to_use]
        return round(sum(ranges) / len(ranges), 2)

    def ema(self, period: int) -> float | None:
        """Exponential Moving Average."""
        if len(self.bars) < period:
            return None
        closes = self.closes
        ema = sum(closes[:period]) / period
        k = 2.0 / (1.0 + period)
        for c in closes[period:]:
            ema = (c - ema) * k + ema
        return ema

    def last_bar(self) -> Bar | None:
        """Most recent bar."""
        return self.bars[-1] if self.bars else None


@dataclass
class TickerQuote:
    """Market quote from ib.reqMktData (Ticker).

    Holds price fields from a Ticker: midpoint, last, close, bid, ask.
    """

    midpoint: float | None = None
    last: float | None = None
    close: float | None = None
    bid: float | None = None
    ask: float | None = None

    def best_price(self) -> float | None:
        """Best available price: last, then close, then midpoint, then bid/ask average."""
        if self.last is not None and self.last > 0:
            return self.last
        if self.close is not None and self.close > 0:
            return self.close
        if self.midpoint is not None and self.midpoint > 0:
            return self.midpoint
        if (
            self.bid is not None
            and self.ask is not None
            and self.bid > 0
            and self.ask > 0
        ):
            return (self.bid + self.ask) / 2.0
        return None


@dataclass
class DayRange:
    """Today's intraday range (low, high) from 1-min RTH bars."""

    low: float
    high: float

    @property
    def range(self) -> float:
        """High - low."""
        return self.high - self.low


@dataclass
class AccountValue:
    """Account value from IB with tag, value, and currency."""

    tag: str
    value: str
    currency: str

    def as_float(self) -> float | None:
        """Convert value to float if possible, None otherwise."""
        try:
            return float(self.value)
        except (ValueError, TypeError):
            return None
