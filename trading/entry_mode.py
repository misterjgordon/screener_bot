"""Entry mode logic: order type (limit, market, stop, etc.) or skip based on bar patterns."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from strategies.bar_patterns.breakout import BREAK_OUT_LOOKBACK_BARS
from strategies.bar_patterns.breakout import break_out_bar_stats
from strategies.bar_patterns.breakout import breakout_limit_entry_price
from trading.bar_loader import get_bars
from trading.entry_price_policy import resolve_new_order_default_limit_price
from trading.market_data import get_ticker_quote
from trading.models import BarSeries

if TYPE_CHECKING:
    from ib_async import IB

    from trading.models import TickerQuote


@dataclass
class EntryMode:
    """Determined entry order type and price for a new trade."""

    order_type: str  # 'limit' | 'market' | ...
    entry_price: float
    skip: bool


def get_entry_mode(
    ib: 'IB | None',
    trader: str | None,
    change_type: str,
    symbol: str,
    market_entry_price: float,
    is_long: bool,
    bundle: 'BarSeries | None' = None,
    quote: 'TickerQuote | None' = None,
) -> EntryMode:
    """Determine entry order type and price from bar patterns.

    When break_out_bar pattern is detected (bar > 4x avg), use limit at
    min(midpoint, ask) long / max(midpoint, bid) short from the same quote as non-breakout.
    Otherwise use limit at ask (long) or bid (short).

    Args:
        ib: IB connection
        trader: Trader name used for NEW-order entry policy
        change_type: Position change type (NEW, ADD, ...)
        symbol: Stock symbol
        market_entry_price: Fallback price when bid/ask unavailable
        is_long: True for long, False for short (used to choose ask vs bid)
        bundle: Optional pre-fetched bar bundle; when provided, skips IB fetch
        quote: Optional pre-fetched quote; when None and IB is connected, fetches once for
            both default limit and breakout-adjusted limit (no duplicate reqMktData).

    Returns:
        EntryMode with order_type 'limit', entry_price, and skip flag.
    """
    market_entry_price = round(float(market_entry_price), 2)
    if ib is None:
        return EntryMode(
            order_type='limit',
            entry_price=resolve_new_order_default_limit_price(
                trader=trader if change_type == 'NEW' else None,
                is_long=is_long,
                market_entry_price=market_entry_price,
                quote=quote,
                bundle=bundle,
            ),
            skip=False,
        )
    if not ib.isConnected():
        return EntryMode(
            order_type='limit',
            entry_price=resolve_new_order_default_limit_price(
                trader=trader if change_type == 'NEW' else None,
                is_long=is_long,
                market_entry_price=market_entry_price,
                quote=quote,
                bundle=bundle,
            ),
            skip=False,
        )

    resolved_quote = quote if quote is not None else get_ticker_quote(ib, symbol)

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
    limit_price = resolve_new_order_default_limit_price(
        trader=trader if change_type == 'NEW' else None,
        is_long=is_long,
        market_entry_price=market_entry_price,
        quote=resolved_quote,
        bundle=bar_series,
    )
    if len(bar_series.bars_2min_rth) < BREAK_OUT_LOOKBACK_BARS:
        return EntryMode(order_type='limit', entry_price=limit_price, skip=False)

    stats = break_out_bar_stats(
        bar_series,
        lookback_bars=BREAK_OUT_LOOKBACK_BARS,
        ib=ib,
        symbol=symbol,
    )
    if stats.breakout and stats.midpoint_of_breakout_bar is not None:
        breakout_entry = breakout_limit_entry_price(
            stats.midpoint_of_breakout_bar,
            is_long,
            resolved_quote,
        )
        print(
            f'Breakout bar TRUE for {symbol}: midpoint ${stats.midpoint_of_breakout_bar:.2f} '
            f'-> limit ${breakout_entry:.2f} (quote bid/ask)'
        )
        return EntryMode(
            order_type='limit',
            entry_price=breakout_entry,
            skip=False,
        )
    return EntryMode(order_type='limit', entry_price=limit_price, skip=False)
