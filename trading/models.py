"""Data models for trading - structured types for IB market data and SMB positions.

IB models: Bar, BarSeries, TickerQuote, etc. represent ib_async data in typed form.
SMB models: NormalizedRecord, PositionSummary represent SMB API positions (not dicts).
Execution: one row of the executions CSV (order/execution log).
"""

from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime

from strategies.utils import bar_session


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
    """Pre-fetched 1D and 2-min bars for a symbol.

    bars_1d: RTH daily bars only (no session tagging).
    bars_2min: Intraday bars; session (PM|RTH|AH) computed from bar.date.
    Views: bars_2min_rth, bars_2min_pm, bars_2min_ah for filtered retrieval.
    """

    bars_1d: list
    bars_2min: list

    @property
    def bars_2min_rth(self) -> list:
        """2-min bars in regular trading hours (9:30-16:00 ET)."""
        return [b for b in self.bars_2min if bar_session(b.date) == 'RTH']

    @property
    def bars_2min_pm(self) -> list:
        """2-min bars in pre-market (< 9:30 ET)."""
        return [b for b in self.bars_2min if bar_session(b.date) == 'PM']

    @property
    def bars_2min_ah(self) -> list:
        """2-min bars in after-hours (>= 16:00 ET)."""
        return [b for b in self.bars_2min if bar_session(b.date) == 'AH']


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


@dataclass
class Execution:
    """One execution log row (CSV schema for executions_YYYY-MM-DD.csv).

    Shares come from calculate_num_shares_from_risk; total_risk is trade_stop_amount
    (magnitude * daily stop); risk_per_share = total_risk / shares when applicable.
    """

    timestamp: str
    trader: str
    symbol: str
    change_type: str
    net_side: str
    delta_magnitude: float
    entry_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    order_id: str | None = None
    shares: int | None = None
    total_risk: float | None = None
    risk_per_share: float | None = None

    @property
    def market_value(self) -> float | None:
        """Notional value at entry: shares * entry_price."""
        if self.shares is not None and self.entry_price is not None:
            return self.shares * self.entry_price
        return None

    @classmethod
    def csv_fieldnames(cls) -> list[str]:
        """Column names for csv.DictWriter (single source of truth for CSV schema)."""
        return [
            'timestamp', 'trader', 'symbol', 'change_type', 'net_side', 'delta_magnitude',
            'entry_price', 'stop_price', 'take_profit_price', 'order_id',
            'shares', 'total_risk', 'risk_per_share', 'market_value',
        ]

    def to_csv_row(self) -> dict[str, str | int | float]:
        """Dict for csv.DictWriter.writerow; None values become empty string."""
        val = self.market_value
        return {
            'timestamp': self.timestamp,
            'trader': self.trader,
            'symbol': self.symbol,
            'change_type': self.change_type,
            'net_side': self.net_side,
            'delta_magnitude': self.delta_magnitude,
            'entry_price': self.entry_price if self.entry_price is not None else '',
            'stop_price': self.stop_price if self.stop_price is not None else '',
            'take_profit_price': self.take_profit_price if self.take_profit_price is not None else '',
            'order_id': self.order_id if self.order_id is not None else '',
            'shares': self.shares if self.shares is not None else '',
            'total_risk': self.total_risk if self.total_risk is not None else '',
            'risk_per_share': round(self.risk_per_share, 2) if self.risk_per_share is not None else '',
            'market_value': round(val, 2) if val is not None else '',
        }
