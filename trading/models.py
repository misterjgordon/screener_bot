"""Data models for trading - structured types for IB market data and SMB positions.

IB models: Bar, BarSeries, TickerQuote, etc. represent ib_async data in typed form.
SMB models: NormalizedRecord, PositionSummary represent SMB API positions (not dicts).
"""

from dataclasses import asdict, dataclass
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


# -----------------------------------------------------------------------------
# SMB Position models (from SMB external-positions API, not IB)
# -----------------------------------------------------------------------------


@dataclass
class NormalizedRecord:
    """One SMB external-positions record after normalization.

    Represents a single position line from the SMB API, parsed and typed.
    """

    trader: str              # e.g. "Jeff Holden"
    is_long_term: bool       # True for LT accounts
    symbol_raw: str          # as given by API
    side: str                # "long" "short" "flat"
    magnitude: float          # position size / weight
    last_updated: str
    created_at: str
    instrument_type: str     # equity/option
    underlying: str          # equity ticker or option underlying
    expiry: str | None       # option expiry as string, or None
    strike: float | None     # option strike as float, or None
    option_type: str | None  # "C" or "P" for options


@dataclass
class PositionSummary:
    """Aggregated position per (trader, symbol) - the position table row.

    Produced by summarize_group, optionally enriched with prev_magnitude,
    delta_magnitude, change_type. Used for save/load snapshot, print table,
    and execution logic.
    """

    trader: str              # e.g. "Jeff Holden"
    is_long_term: bool       # True for LT accounts
    symbol: str              # as given by API
    instrument_type: str     # equity/option
    underlying: str          # equity ticker or option underlying
    expiry: str | None       # option expiry as string, or None
    strike: float | None     # option strike as float, or None
    option_type: str | None  # "C" or "P" for options
    net_side: str            # long, short, flat, conflict
    conflict: bool
    total_magnitude: float
    prev_magnitude: float | None = None
    delta_magnitude: float | None = None
    change_type: str | None = None
    order_placed: bool | None = None

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'PositionSummary':
        """Build from dict (e.g. from load_snapshot JSON)."""
        return cls(
            trader=d['trader'],
            is_long_term=d['is_long_term'],
            symbol=d['symbol'],
            instrument_type=d.get('instrument_type', 'equity'),
            underlying=d.get('underlying') or d['symbol'],
            expiry=d.get('expiry'),
            strike=d.get('strike'),
            option_type=d.get('option_type'),
            net_side=d['net_side'],
            conflict=d.get('conflict', False),
            total_magnitude=d['total_magnitude'],
            prev_magnitude=d.get('prev_magnitude'),
            delta_magnitude=d.get('delta_magnitude'),
            change_type=d.get('change_type'),
            order_placed=d.get('order_placed'),
        )
