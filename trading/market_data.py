"""Single entry point for IB market data.

Provides get_* functions for ticker quotes, bars, market price, trailing stop,
today's range, ADR, and gap percentage. Can be run and tested independently
of the main screener.
"""

import asyncio
from datetime import date, datetime

asyncio.set_event_loop(asyncio.new_event_loop())
from ib_async import IB, Stock  # noqa: E402

from trading.config import (  # noqa: E402
    ACCOUNT_CURRENCY,
    IB_CLIENT_ID_MARKET_DATA,
    IB_HOST,
    IB_PORT,
)
from trading.models import DayRange, TickerQuote  # noqa: E402


def _to_float(val: object) -> float | None:
    """Convert value to float if possible."""
    if val is None or callable(val):
        return None
    if not isinstance(val, (int, float, str)):
        return None
    try:
        f = float(val)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def connect(
    host: str = IB_HOST,
    port: int = IB_PORT,
    client_id: int = IB_CLIENT_ID_MARKET_DATA,
    readonly: bool = True,
) -> IB | None:
    """Create and return IB connection. Use disconnect(ib) when done."""
    try:
        ib = IB()
        ib.connect(host, port, clientId=client_id, readonly=readonly)
        return ib if ib.isConnected() else None
    except Exception:
        return None


def disconnect(ib: IB | None) -> None:
    """Close IB connection if connected."""
    if ib is None:
        return
    try:
        if ib.isConnected():
            ib.disconnect()
    except Exception:
        pass


def get_ticker_quote(ib: IB | None, symbol: str) -> TickerQuote | None:
    """Get market quote (midpoint, last, close, bid, ask) for symbol."""
    if ib is None or not ib.isConnected():
        return None
    try:
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        ticker = ib.reqMktData(contract, '', False, False)
        ib.sleep(0.1)
        return TickerQuote(
            midpoint=_to_float(ticker.midpoint()),
            last=_to_float(ticker.last),
            close=_to_float(ticker.close),
            bid=_to_float(ticker.bid),
            ask=_to_float(ticker.ask),
        )
    except Exception:
        return None


def get_market_price(ib: IB | None, symbol: str) -> float | None:
    """Get best available price for symbol (last, then close, then midpoint, bid/ask)."""
    quote = get_ticker_quote(ib, symbol)
    return quote.best_price() if quote else None


def get_bars(
    ib: IB | None,
    symbol: str,
    duration_str: str,
    bar_size: str,
    what_to_show: str = 'TRADES',
    use_rth: bool = True,
) -> list | None:
    """Get historical bars for symbol. Returns raw ib_async BarData objects."""
    if ib is None or not ib.isConnected():
        return None
    try:
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        return ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr=duration_str,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
        )
    except Exception:
        return None


def calculate_trailing_stop(
    ib: IB | None,
    symbol: str,
    prior_bars: int = 3,
    position_side: str = 'long',
) -> float | None:
    """Trailing stop from last N 15-min RTH bars (min low for long, max high for short)."""
    duration_seconds = (prior_bars * 15 * 60) + (15 * 60)
    bars = get_bars(
        ib,
        symbol,
        duration_str=f'{duration_seconds} S',
        bar_size='15 mins',
    )
    if not bars:
        return None
    today = date.today()
    bar_date = lambda b: b.date.date() if isinstance(b.date, datetime) else b.date
    session_bars = [b for b in bars if bar_date(b) == today]
    if not session_bars:
        return None
    bars = session_bars[-prior_bars:] if len(session_bars) >= prior_bars else session_bars
    if not bars:
        return None
    if position_side.lower() == 'long':
        return float(min(b.low for b in bars))
    return float(max(b.high for b in bars))


def get_todays_range(ib: IB | None, symbol: str) -> DayRange | None:
    """Today's RTH low and high from 1-min bars."""
    bars = get_bars(
        ib,
        symbol,
        duration_str='1 D',
        bar_size='1 min',
    )
    if not bars:
        return None
    today = date.today()
    bar_date = lambda b: b.date.date() if isinstance(b.date, datetime) else b.date
    session = [b for b in bars if bar_date(b) == today and b.low is not None and b.high is not None]
    if not session:
        return None
    return DayRange(
        low=float(min(b.low for b in session)),
        high=float(max(b.high for b in session)),
    )


def calculate_adr(ib: IB | None, symbol: str, days: int = 20) -> float | None:
    """Average Daily Range over specified days."""
    bars = get_bars(
        ib,
        symbol,
        duration_str=f'{days} D',
        bar_size='1 day',
    )
    if not bars:
        return None
    ranges = [b.high - b.low for b in bars if b.high is not None and b.low is not None]
    if not ranges:
        return None
    return round(float(sum(ranges) / len(ranges)), 2)


def calculate_gap_percentage(
    ib: IB | None,
    symbol: str,
    current_price: float,
) -> float | None:
    """Gap up % from yesterday's close. Returns None for negative or no gap."""
    bars = get_bars(
        ib,
        symbol,
        duration_str='2 D',
        bar_size='1 day',
    )
    if not bars or len(bars) < 2:
        return None
    yesterday_close = bars[-2].close
    if yesterday_close is None or yesterday_close <= 0:
        return None
    gap = ((current_price - yesterday_close) / yesterday_close) * 100
    return float(gap) if gap > 0 else None


def diagnose_market_price(ib: IB | None, symbol: str) -> None:
    """Print detailed market price info for debugging."""
    quote = get_ticker_quote(ib, symbol)
    if quote is None:
        print(f'DIAGNOSTIC [{symbol}]: No quote (IB not connected or error)')
        return
    def _fmt(v: float | None) -> str:
        return f'{v:.2f}' if v is not None else 'N/A'
    print(f'DIAGNOSTIC [{symbol}]:')
    print(f'   Midpoint: {_fmt(quote.midpoint)}')
    print(f'   Close: {_fmt(quote.close)}')
    print(f'   Bid: {_fmt(quote.bid)}')
    print(f'   Ask: {_fmt(quote.ask)}')
    print(f'   Last: {_fmt(quote.last)}')
    best = quote.best_price()
    if best:
        print(f'   ✓ Best price: ${best:.2f}')
    else:
        print('   ❌ No valid price')


if __name__ == '__main__':
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else None
    if not symbol:
        print('Usage: python -m trading.market_data <SYMBOL>')
        sys.exit(1)
    ib = connect(readonly=True)
    if ib:
        get_market_price(ib, symbol)
        diagnose_market_price(ib, symbol)
        disconnect(ib)
    else:
        print('Failed to connect to IB. Is TWS/Gateway running?')
