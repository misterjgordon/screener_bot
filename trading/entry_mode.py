"""Entry mode logic: order type (limit, market, stop, etc.) or skip based on bar patterns."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from strategies.bar_patterns.breakout import BREAK_OUT_LOOKBACK_BARS
from strategies.bar_patterns.breakout import break_out_bar_stats
from trading.bar_loader import get_bars
from trading.market_data import get_ticker_quote
from trading.models import BarSeries

if TYPE_CHECKING:
    from ib_async import IB

    from trading.models import TickerQuote


def _limit_entry_price(
    ib: 'IB | None',
    symbol: str,
    is_long: bool,
    market_entry_price: float,
    quote: 'TickerQuote | None' = None,
) -> float:
    """Limit order price: ask if long, bid if short. Falls back to market_entry_price if quote missing."""
    if quote is None and ib is not None and ib.isConnected():
        quote = get_ticker_quote(ib, symbol)
    if quote is not None:
        p = quote.ask if is_long else quote.bid
        if p is not None and p > 0:
            return p
    return market_entry_price


@dataclass
class EntryMode:
    """Determined entry order type and price for a new trade."""

    order_type: str  # 'limit' | 'market' | ...
    entry_price: float
    skip: bool


def get_entry_mode(
    ib: 'IB | None',
    symbol: str,
    market_entry_price: float,
    is_long: bool,
    bundle: 'BarSeries | None' = None,
    quote: 'TickerQuote | None' = None,
) -> EntryMode:
    """Determine entry order type and price from bar patterns.

    When break_out_bar pattern is detected (bar > 4x avg), use limit at midpoint.
    Otherwise use limit at ask (long) or bid (short).

    Args:
        ib: IB connection
        symbol: Stock symbol
        market_entry_price: Fallback price when bid/ask unavailable
        is_long: True for long, False for short (used to choose ask vs bid)
        bundle: Optional pre-fetched bar bundle; when provided, skips IB fetch
        quote: Optional pre-fetched quote; when provided, skips ticker fetch for limit price

    Returns:
        EntryMode with order_type 'limit', entry_price, and skip flag.
    """
    limit_price = _limit_entry_price(ib, symbol, is_long, market_entry_price, quote)
    if ib is None:
        return EntryMode(order_type='limit', entry_price=limit_price, skip=False)
    if not ib.isConnected():
        return EntryMode(order_type='limit', entry_price=limit_price, skip=False)

    if bundle is not None and bundle.bars_2min:
        bar_series = bundle
    else:
        bars_2min = get_bars(
            ib,
            symbol,
            duration_str='1 D',
            bar_size='2 mins',
            use_rth=True,
        )
        bar_series = BarSeries(bars_1d=[], bars_2min=bars_2min or [])
    if len(bar_series.bars_2min_rth) < BREAK_OUT_LOOKBACK_BARS:
        return EntryMode(order_type='limit', entry_price=limit_price, skip=False)

    stats = break_out_bar_stats(bar_series, lookback_bars=BREAK_OUT_LOOKBACK_BARS)
    if stats.breakout and stats.midpoint_of_breakout_bar is not None:
        print(f'Breakout bar TRUE for {symbol}: using limit at midpoint ${stats.midpoint_of_breakout_bar:.2f}')
        return EntryMode(
            order_type='limit',
            entry_price=stats.midpoint_of_breakout_bar,
            skip=False,
        )
    return EntryMode(order_type='limit', entry_price=limit_price, skip=False)
