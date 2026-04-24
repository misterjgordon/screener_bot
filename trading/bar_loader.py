"""Centralized bar loading for gap, ADR, day_3_gap, break_out_bar, trailing stop, today's range.

Single source for bar fetching: get_bars (raw IB request) and load_bars (daily + 2-min bundle).
Consumers (market_data, entry_mode) use these and pass bundle when available to avoid duplicate IB calls.
"""

from datetime import date
from datetime import datetime
from datetime import time

from ib_async import IB
from ib_async import Stock

from trading.config import ACCOUNT_CURRENCY
from trading.models import BarSeries

TRAILING_STOP_BARS_2MIN = 22  # 44 min trailing window (22 * 2 min) for move filter
ADR_DAYS = 20
# Calendar span for daily load: need enough sessions for 30D volume SMA (percent_of_avg_volume) + margin.
DAILY_HISTORY_CALENDAR_DAYS = 45
DAILY_DURATION = f'{DAILY_HISTORY_CALENDAR_DAYS} D'


def get_bars(
    ib: IB | None,
    symbol: str,
    duration_str: str,
    bar_size: str,
    what_to_show: str = 'TRADES',
    use_rth: bool = True,
    end_date: date | None = None,
) -> list | None:
    """Get historical bars for symbol. Returns raw ib_async BarData objects.

    If end_date is set, request ends at 4:00 PM ET on that date; otherwise most recent data.
    """
    if ib is None or not ib.isConnected():
        return None
    end_dt = datetime.combine(end_date, time(16, 0)) if end_date else ''
    try:
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        return ib.reqHistoricalData(
            contract,
            endDateTime=end_dt,
            durationStr=duration_str,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
        )
    except Exception:
        return None


def load_bars(
    ib: IB | None,
    symbol: str,
    end_date: date | None = None,
    *,
    duration_str_2min: str = '1 D',
) -> BarSeries | None:
    """Fetch daily and 2-min bars for symbol. Returns None if either fetch fails.

    If end_date is set, bars are requested through 4:00 PM ET on that date (for backtesting a specific day).
    Otherwise returns most recent data.

    Parameters
    ----------
    duration_str_2min
        IB ``durationStr`` for the 2-minute request. Default ``1 D``. For desk
        session windows that include prior-day after-hours, pass
        ``strategies.indicators.session_range.BARS_2MIN_DURATION_FOR_DESK_SESSION_RANGES``
        (``'2 D'``).
    """
    if ib is None or not ib.isConnected():
        return None

    bars_1d = get_bars(
        ib,
        symbol,
        duration_str=DAILY_DURATION,
        bar_size='1 day',
        end_date=end_date,
    )
    if not bars_1d:
        return None

    # use_rth=False so bars include PM; VWAP and session-based logic use PM start, RTH views filter as needed
    bars_2min = get_bars(
        ib,
        symbol,
        duration_str=duration_str_2min,
        bar_size='2 mins',
        use_rth=False,
        end_date=end_date,
    )
    if not bars_2min:
        return None

    return BarSeries(bars_1d=bars_1d, bars_2min=bars_2min)
