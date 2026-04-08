"""Data models for trading - structured types for IB market data and SMB positions.

IB models: Bar, BarSeries, TickerQuote, etc. represent ib_async data in typed form.
SMB models: NormalizedRecord, PositionSummary represent SMB API positions (not dicts).
Execution: one row of the executions CSV (order/execution log).
SEC: :class:`SecTickers` / :class:`SecTickerRow` for ``all_tickers.json`` (SEC company_tickers_exchange format).
Watchlist: :class:`TickerSummary` for unioned symbols per source and desk day (technicals optional:
``atr_14``, ``percent_of_avg_volume``, ``gap_percent``, ``gap_atr``).
"""

import json
from dataclasses import asdict
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Self
from typing import overload

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
        """Best available price: last, then close, then midpoint, then bid/ask average.

        Returns 2-decimal dollars; bid/ask average is rounded once here.
        """
        p: float | None = None
        if self.last is not None and self.last > 0:
            p = float(self.last)
        elif self.close is not None and self.close > 0:
            p = float(self.close)
        elif self.midpoint is not None and self.midpoint > 0:
            p = float(self.midpoint)
        elif (
            self.bid is not None
            and self.ask is not None
            and self.bid > 0
            and self.ask > 0
        ):
            p = (float(self.bid) + float(self.ask)) / 2.0
        if p is None:
            return None
        return round(p, 2)


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
    risk_percent: float | None = None

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        d = asdict(self)
        # Keep backward-compatible key naming for analysis tooling.
        d['risk_%'] = d.pop('risk_percent')
        return d

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
            risk_percent=d.get('risk_%', d.get('risk_percent')),
        )


@overload
def round_money_2(value: None) -> None: ...


@overload
def round_money_2(value: float) -> float: ...


def round_money_2(value: float | None) -> float | None:
    """Round dollar or percent fields for execution CSV/DB (2 decimal places).

    Used for ``total_risk``, ``risk_per_share``, ``market_value``, and ``risk_%`` so
    live logging, PostgreSQL, and CSV import share one quantization.
    """
    if value is None:
        return None
    return round(value, 2)


@dataclass
class Execution:
    """One execution log row (CSV schema for executions_YYYY-MM-DD.csv).

    For NEW/ADD, shares come from risk-based sizing (or override); total_risk is
    trade_stop_amount; risk_per_share = total_risk / shares when applicable.
    For TRIM/CLOSE, shares are the number of shares exited; risk columns are usually empty.
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
    filled_price: float | None = None
    shares: int | None = None
    total_risk: float | None = None
    risk_per_share: float | None = None
    risk_percent: float | None = None

    @property
    def market_value(self) -> float | None:
        """Notional: shares * entry_price, or shares * filled_price if entry absent (exits)."""
        if self.shares is None:
            return None
        price = self.entry_price if self.entry_price is not None else self.filled_price
        if price is None:
            return None
        return self.shares * price

    @classmethod
    def csv_fieldnames(cls) -> list[str]:
        """Column names for csv.DictWriter (single source of truth for CSV schema)."""
        return [
            'timestamp', 'trader', 'symbol', 'change_type', 'net_side', 'delta_magnitude',
            'entry_price', 'stop_price', 'take_profit_price', 'order_id', 'filled_price',
            'shares', 'total_risk', 'risk_per_share', 'market_value', 'risk_%',
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
            'filled_price': self.filled_price if self.filled_price is not None else '',
            'shares': self.shares if self.shares is not None else '',
            'total_risk': round_money_2(self.total_risk) if self.total_risk is not None else '',
            'risk_per_share': round_money_2(self.risk_per_share) if self.risk_per_share is not None else '',
            'market_value': round_money_2(val) if val is not None else '',
            'risk_%': round_money_2(self.risk_percent) if self.risk_percent is not None else '',
        }


# --- SEC all-tickers reference (same schema as SEC company_tickers_exchange.json) ---

SEC_TICKER_JSON_FIELDS = ('cik', 'name', 'ticker', 'exchange')
ALL_TICKERS_JSON = 'all_tickers.json'


@dataclass(frozen=True)
class SecTickerRow:
    """One row of SEC-style ``fields`` + ``data`` ticker listing."""

    cik: int
    name: str
    ticker: str
    exchange: str


@dataclass(frozen=True)
class SecTickers:
    """Parsed ``all_tickers.json``: ``{"fields": [...], "data": [[...], ...]}``."""

    fields: tuple[str, str, str, str]
    rows: tuple[SecTickerRow, ...]

    def ticker_set_upper(self) -> frozenset[str]:
        """Uppercased tickers for O(1) validation of parsed candidates."""
        return frozenset(row.ticker.upper() for row in self.rows)

    @classmethod
    def from_json_dict(cls, obj: dict[str, object]) -> Self:
        """Parse decoded JSON object."""
        fields_raw = obj.get('fields')
        data_raw = obj.get('data')
        if not isinstance(fields_raw, list) or not isinstance(data_raw, list):
            raise TypeError('ticker JSON must contain list fields and list data')

        fields = tuple(str(x) for x in fields_raw)
        expected = SEC_TICKER_JSON_FIELDS
        if fields != expected:
            raise ValueError(f'ticker fields mismatch: got {fields!r}, expected {expected!r}')
        fields_named: tuple[str, str, str, str] = SEC_TICKER_JSON_FIELDS

        rows: list[SecTickerRow] = []
        for i, row in enumerate(data_raw):
            if not isinstance(row, list) or len(row) != 4:
                raise TypeError(f'ticker data row {i}: expected 4 columns, got {row!r}')
            cik_v, name_v, ticker_v, exchange_v = row
            if not isinstance(cik_v, int):
                raise TypeError(f'ticker data row {i}: cik must be int, got {type(cik_v).__name__}')
            rows.append(
                SecTickerRow(
                    cik=cik_v,
                    name=str(name_v),
                    ticker=str(ticker_v),
                    exchange=str(exchange_v),
                )
            )

        return cls(fields=fields_named, rows=tuple(rows))


def p_all_tickers_json_path() -> Path:
    """Default: ``trading/data/symbols/all_tickers.json``."""
    return Path(__file__).resolve().parent / 'data' / 'symbols' / ALL_TICKERS_JSON


def load_all_tickers(p_json: Path | None = None) -> SecTickers:
    """Load ``all_tickers.json`` from disk (defaults to :func:`p_all_tickers_json_path`)."""
    p_path = p_json if p_json is not None else p_all_tickers_json_path()
    text = p_path.read_text(encoding='utf-8')
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise TypeError('ticker JSON root must be an object')
    return SecTickers.from_json_dict(obj)


@dataclass(frozen=True)
class TickerSummary:
    """One symbol mention from an ingested watchlist source for a desk day.

    ``source_id`` matches the ingest module basename (e.g. ``smb_gameplan``,
    ``market_rundown``). ``atr_14`` is 14-period ATR on daily bars when filled
    from historical data; otherwise ``None``.
    ``percent_of_avg_volume`` is rounded percent vs 30D prior volume mean using the last
    available 2m bar through ET session close on the desk day (premarket vs RTH volume
    sections per :func:`strategies.indicators.percent_of_avg_volume.percent_of_avg_volume`);
    ``None`` when not computed or insufficient data.
    ``gap_percent`` and ``gap_atr`` are from :func:`strategies.indicators.gap.gap` at the same
    evaluation instant as percent-of-average volume; ``None`` when not computed or insufficient data.
    """

    symbol: str
    source_id: str
    trade_date: date
    atr_14: float | None = None
    percent_of_avg_volume: int | None = None
    gap_percent: float | None = None
    gap_atr: float | None = None
