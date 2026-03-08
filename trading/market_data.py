"""Single entry point for IB market data.

Provides get_* functions for ticker quotes, bars, market price, trailing stop,
today's range, ADR, and gap percentage. When used by the screener, all calls
receive the screener's IB connection (single entry point to IB). Can be run
and tested independently via python -m trading.market_data <SYMBOL>.

When bundle is provided (from bar_loader.load_bars), uses pre-fetched bars
to avoid duplicate IB requests. When bundle is None, fetches via bar_loader.get_bars.
"""

from typing import TYPE_CHECKING

from ib_async import IB, Stock

from strategies.utils import is_rth_session_bar
from strategies.utils import last_trading_day
from trading.bar_loader import get_bars
from trading.config import (  # noqa: E402
    ACCOUNT_CURRENCY,
    IB_CLIENT_ID_MARKET_DATA,
    IB_HOST,
    IB_PORT,
)
from trading.models import DayRange, TickerQuote  # noqa: E402

if TYPE_CHECKING:
    from trading.bar_loader import BarSeries


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


def calculate_trailing_stop(
    ib: IB | None,
    symbol: str,
    prior_bars: int = 7,
    position_side: str = 'long',
    bundle: 'BarSeries | None' = None,
) -> float | None:
    """Trailing stop from last N 2-min RTH bars (min low for long, max high for short)."""
    if bundle is not None and bundle.bars_2min:
        bars = bundle.bars_2min
    else:
        bars = get_bars(ib, symbol, duration_str='1 D', bar_size='2 mins', use_rth=True)
    if not bars:
        return None
    session_date = last_trading_day()
    session_bars = [b for b in bars if is_rth_session_bar(b.date, session_date)]
    if not session_bars:
        return None
    bars = session_bars[-prior_bars:] if len(session_bars) >= prior_bars else session_bars
    if not bars:
        return None
    if position_side.lower() == 'long':
        return float(min(b.low for b in bars if b.low is not None))
    return float(max(b.high for b in bars if b.high is not None))


def get_todays_range(
    ib: IB | None,
    symbol: str,
    bundle: 'BarSeries | None' = None,
) -> DayRange | None:
    """Today's RTH low and high from 2-min bars."""
    if bundle is not None and bundle.bars_2min:
        bars = bundle.bars_2min
    else:
        bars = get_bars(ib, symbol, duration_str='1 D', bar_size='2 mins', use_rth=True)
    if not bars:
        return None
    session_date = last_trading_day()
    session = [b for b in bars if is_rth_session_bar(b.date, session_date) and b.low is not None and b.high is not None]
    if not session:
        return None
    return DayRange(
        low=float(min(b.low for b in session)),
        high=float(max(b.high for b in session)),
    )


def calculate_adr(
    ib: IB | None,
    symbol: str,
    days: int = 20,
    bundle: 'BarSeries | None' = None,
) -> float | None:
    """Average Daily Range over specified days."""
    if bundle is not None and bundle.bars_1d:
        bars = bundle.bars_1d[-days:] if len(bundle.bars_1d) >= days else bundle.bars_1d
    else:
        bars = get_bars(ib, symbol, duration_str=f'{days} D', bar_size='1 day')
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
    bundle: 'BarSeries | None' = None,
) -> float | None:
    """Gap up % from yesterday's close. Returns None for negative or no gap."""
    if bundle is not None and len(bundle.bars_1d) >= 2:
        bars = bundle.bars_1d
    else:
        bars = get_bars(ib, symbol, duration_str='2 D', bar_size='1 day')
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
        print('   ✖ No valid price')


if __name__ == '__main__':
    import asyncio
    import sys

    asyncio.set_event_loop(asyncio.new_event_loop())
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print('Usage: python -m trading.market_data <SYMBOL>')
        sys.exit(1)
    symbol = sys.argv[1].strip()
    ib = connect(readonly=True)
    if ib:
        get_market_price(ib, symbol)
        diagnose_market_price(ib, symbol)
        disconnect(ib)
    else:
        print('Failed to connect to IB. Is TWS/Gateway running?')
