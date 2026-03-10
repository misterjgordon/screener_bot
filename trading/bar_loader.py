"""Centralized bar loading for gap, ADR, day_3_gap, break_out_bar, trailing stop, today's range.

Single source for bar fetching: get_bars (raw IB request) and load_bars (daily + 2-min bundle).
Consumers (market_data, entry_mode) use these and pass bundle when available to avoid duplicate IB calls.
"""

from ib_async import IB
from ib_async import Stock

from trading.config import ACCOUNT_CURRENCY
from trading.models import BarSeries

TRAILING_STOP_BARS_2MIN = 7  # 14 min trailing stop (7 * 2 min)
ADR_DAYS = 20
DAILY_DURATION = f'{ADR_DAYS} D'


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


def load_bars(ib: IB | None, symbol: str) -> BarSeries | None:
    """Fetch daily and 2-min bars for symbol. Returns None if either fetch fails."""
    if ib is None or not ib.isConnected():
        return None

    bars_1d = get_bars(
        ib,
        symbol,
        duration_str=DAILY_DURATION,
        bar_size='1 day',
    )
    if not bars_1d:
        return None

    # use_rth=False so bars include PM; VWAP and session-based logic use PM start, RTH views filter as needed
    bars_2min = get_bars(
        ib,
        symbol,
        duration_str='1 D',
        bar_size='2 mins',
        use_rth=False,
    )
    if not bars_2min:
        return None

    return BarSeries(bars_1d=bars_1d, bars_2min=bars_2min)
