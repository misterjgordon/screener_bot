"""Entry mode logic: order type (limit, market, stop, etc.) or skip based on bar patterns."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from strategies.bar_patterns.bars_2min import BREAK_OUT_LOOKBACK_BARS
from strategies.bar_patterns.bars_2min import break_out_bar_stats
from strategies.utils import is_session_bar
from strategies.utils import last_trading_day
from trading.bar_loader import get_bars

if TYPE_CHECKING:
    from ib_async import IB
    from trading.bar_loader import BarSeries


@dataclass
class EntryMode:
    """Determined entry order type and price for a new trade."""

    order_type: str  # 'stop' | 'limit' | 'market' | ...
    entry_price: float
    skip: bool


def get_entry_mode(
    ib: 'IB | None',
    symbol: str,
    market_entry_price: float,
    bundle: 'BarSeries | None' = None,
) -> EntryMode:
    """Determine entry order type and price from bar patterns.

    When break_out_bar pattern is detected (bar > 4x avg), use limit at midpoint
    instead of stop at market price. Otherwise use default stop at market price.

    Args:
        ib: IB connection
        symbol: Stock symbol
        market_entry_price: Current best/market price (used when not breakout)
        bundle: Optional pre-fetched bar bundle; when provided, skips IB fetch.

    Returns:
        EntryMode with order_type, entry_price, and skip flag.
    """
    if ib is None:
        return EntryMode(order_type='stop', entry_price=market_entry_price, skip=False)
    if not ib.isConnected():
        return EntryMode(order_type='stop', entry_price=market_entry_price, skip=False)

    if bundle is not None and bundle.bars_2min:
        bars_2min = bundle.bars_2min
    else:
        bars_2min = get_bars(
            ib,
            symbol,
            duration_str='1 D',
            bar_size='2 mins',
            use_rth=True,
        )
    if not bars_2min:
        return EntryMode(order_type='stop', entry_price=market_entry_price, skip=False)

    session_date = last_trading_day()
    session_bars = [b for b in bars_2min if is_session_bar(b.date, session_date)]
    if len(session_bars) < BREAK_OUT_LOOKBACK_BARS:
        return EntryMode(order_type='stop', entry_price=market_entry_price, skip=False)

    stats = break_out_bar_stats(session_bars, lookback_bars=BREAK_OUT_LOOKBACK_BARS)
    if stats.breakout and stats.midpoint_of_breakout_bar is not None:
        return EntryMode(
            order_type='limit',
            entry_price=stats.midpoint_of_breakout_bar,
            skip=False,
        )
    return EntryMode(order_type='stop', entry_price=market_entry_price, skip=False)
