"""Single entry point for IB market data.

Provides get_* functions for ticker quotes, bars, market price, trailing stop,
today's range, and gap percentage. When used by the screener, all calls
receive the screener's IB connection (single entry point to IB). Can be run
and tested independently via python -m trading.market_data <SYMBOL>.

When bundle is provided (from bar_loader.load_bars), uses pre-fetched bars
to avoid duplicate IB requests. When bundle is None, fetches via bar_loader.get_bars.
"""

from typing import TYPE_CHECKING

from ib_async import IB
from ib_async import Stock

from strategies.utils import is_rth_session_bar
from strategies.utils import last_trading_day
from trading.bar_loader import get_bars
from trading.config import ACCOUNT_CURRENCY  # noqa: E402
from trading.config import IB_CLIENT_ID_MARKET_DATA  # noqa: E402
from trading.config import IB_HOST  # noqa: E402
from trading.config import IB_PORT  # noqa: E402
from trading.config import IB_PORT_LIVE  # noqa: E402
from trading.config import IB_PORT_PAPER  # noqa: E402
from trading.models import DayRange  # noqa: E402
from trading.models import TickerQuote  # noqa: E402

if TYPE_CHECKING:
    from trading.models import BarSeries


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
    candidate_hosts: list[str] = []
    for h in (host, 'localhost', '127.0.0.1'):
        if h not in candidate_hosts:
            candidate_hosts.append(h)

    candidate_ports: list[int] = []
    if readonly:
        for p in (port, IB_PORT_PAPER, IB_PORT_LIVE, 4001):
            if p not in candidate_ports:
                candidate_ports.append(p)
    else:
        candidate_ports = [port]

    try:
        for h in candidate_hosts:
            for p in candidate_ports:
                try:
                    ib = IB()
                    ib.connect(h, p, clientId=client_id, readonly=readonly)
                    if ib.isConnected():
                        return ib
                except Exception:
                    pass

        # If we got here, all ports/hosts failed.
        return None
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


def get_realtime_bar(ib: IB | None, symbol: str) -> object | None:
    """Subscribe to 5-sec realtime bars, cancel and return latest bar if any, else None.

    reqRealTimeBars requires a qualified contract; Stock + qualifyContracts provide that.
    Returns the latest 5-sec bar (ib_async realtime bar object) or None if none received.
    Caller should read OHLC via .open/.high/.low/.close or .open_/.high_/.low_/.close_.
    """
    if ib is None or not ib.isConnected():
        return None
    try:
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        bars = ib.reqRealTimeBars(
            contract=contract,
            barSize=5,
            whatToShow='TRADES',
            useRTH=True,
        )
        if bars:
            ib.cancelRealTimeBars(bars)
            return bars[-1]
        return None
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
