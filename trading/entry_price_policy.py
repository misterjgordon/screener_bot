"""Resolve default NEW-order entry price policy by trader."""

from typing import TYPE_CHECKING

from strategies.indicators.ema import ema9
from trading.config import NEW_ORDER_ENTRY_POLICY_BY_TRADER
from trading.config import NewOrderEntryPolicy

if TYPE_CHECKING:
    from trading.models import BarSeries
    from trading.models import TickerQuote


def quote_limit_entry_price(
    is_long: bool,
    market_entry_price: float,
    quote: 'TickerQuote | None',
) -> float:
    """Quote-based default limit price: ask for long, bid for short."""
    if quote is not None:
        quote_side_price = quote.ask if is_long else quote.bid
        if quote_side_price is not None and quote_side_price > 0:
            return float(quote_side_price)
    return market_entry_price


def _policy_for_trader(trader: str | None) -> NewOrderEntryPolicy | None:
    if not trader:
        return None
    return NEW_ORDER_ENTRY_POLICY_BY_TRADER.get(trader)


def resolve_new_order_default_limit_price(
    *,
    trader: str | None,
    is_long: bool,
    market_entry_price: float,
    quote: 'TickerQuote | None',
    bundle: 'BarSeries | None',
) -> float:
    """Resolve default NEW-order limit when no pattern-specific override exists.

    ema9_fallback behavior:
    - use EMA9 from bundle bars_2min when available
    - clamp for fillability:
      - long: min(ema9, ask) when ask exists
      - short: max(ema9, bid) when bid exists
    - fallback to quote-based pricing if EMA unavailable
    """
    default_quote_limit = quote_limit_entry_price(is_long, market_entry_price, quote)
    policy = _policy_for_trader(trader)
    if policy is None or not policy.enabled:
        return default_quote_limit
    if policy.mode != 'ema9_fallback':
        return default_quote_limit
    if bundle is None or not bundle.bars_2min:
        return default_quote_limit

    ema9_price = ema9(bundle)
    if ema9_price is None or ema9_price <= 0:
        return default_quote_limit

    if is_long:
        ask = quote.ask if quote is not None else None
        if ask is not None and ask > 0:
            return min(ema9_price, ask)
    else:
        bid = quote.bid if quote is not None else None
        if bid is not None and bid > 0:
            return max(ema9_price, bid)
    return float(ema9_price)
