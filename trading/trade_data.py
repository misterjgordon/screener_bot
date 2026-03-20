"""IB account, position, and order query functions.

Single entry point for read-only IB data: available funds, position size, and
open order status. All functions take an IB connection; connection lifecycle
is managed by the screener.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trading.config import ACCOUNT_CURRENCY
from trading.config import ACTIVE_ORDER_STATUSES
from trading.config import ORDER_TAG

if TYPE_CHECKING:
    from ib_async import IB


def order_tag(trader: str = '') -> str:
    """Return order ref string for tagging IB orders (trader name optional)."""
    return f'{ORDER_TAG}-{trader}' if trader else ORDER_TAG


def get_available_funds(ib: IB, currency: str = ACCOUNT_CURRENCY) -> float:
    """Get available funds for the specified currency."""
    try:
        for av in ib.accountValues():
            if av.tag == 'AvailableFunds' and av.currency == currency:
                try:
                    return float(av.value)
                except ValueError:
                    pass
    except Exception:
        pass
    return 0.0


def get_position_size(ib: IB, symbol: str) -> int:
    """
    Get current position size for a symbol.

    Returns:
        int: Positive number for long positions, negative for short positions, 0 if no position
    """
    try:
        positions = ib.positions()
        for pos in positions:
            if pos.contract.symbol == symbol and pos.contract.secType == 'STK':
                return int(pos.position)
        return 0
    except Exception:
        return 0


def has_open_orders(ib: IB, symbol: str, is_long: bool | None = None) -> bool:
    """
    Check if there are open orders for a symbol.

    Args:
        ib: IB connection
        symbol: Symbol to check
        is_long: If provided, only check for orders in this direction (True for BUY, False for SELL)
                 If None, check for any open orders

    Returns:
        bool: True if there are open orders, False otherwise
    """
    try:
        open_trades = ib.openTrades()
        for trade in open_trades:
            contract = trade.contract
            order = trade.order
            status = trade.orderStatus

            if (
                contract.symbol == symbol
                and contract.secType == 'STK'
                and status.status in ACTIVE_ORDER_STATUSES
                and (is_long is None or (order.action.upper() == 'BUY') == is_long)
            ):
                return True
        return False
    except Exception:
        return False


def has_open_orders_for_trader(ib: IB, symbol: str, is_long: bool, trader: str) -> bool:
    """
    Check if this trader already has an open order for the symbol in the given direction.
    Used for NEW orders so we do not skip when another trader has an order for the same symbol.
    """
    matching = find_orders_for_symbol_trader(ib, symbol, trader)
    for trade in matching:
        order = trade.order
        order_is_buy = order.action.upper() == 'BUY'
        if order_is_buy == is_long:
            return True
    return False


def find_orders_for_symbol_trader(
    ib: IB,
    symbol: str,
    trader: str = '',
    debug: bool = False,
) -> list:
    """
    Find all open orders for a specific symbol and trader.

    Uses ib.trades() and filters by active status so bracket child orders
    (stop/TP) are included; openTrades() is known to sometimes omit them.

    Args:
        ib: IB connection
        symbol: Symbol to find orders for
        trader: Trader name (optional)
        debug: If True, print intermediate counts and orderRef/parentId for symbol (no-op in production)

    Returns:
        list: List of Trade objects matching the criteria
    """
    matching_trades = []
    try:
        # Request open orders; use returned list. If empty (e.g. readonly or
        # different client), try all clients (client 0 only).
        all_trades = ib.reqOpenOrders()
        if not all_trades:
            try:
                all_trades = ib.reqAllOpenOrders()
            except Exception:
                all_trades = []
        ref = order_tag(trader)

        by_symbol = [t for t in all_trades if t.contract.symbol == symbol and t.contract.secType == 'STK']
        by_symbol_active = [t for t in by_symbol if t.orderStatus.status in ACTIVE_ORDER_STATUSES]
        ref_order_ids = {t.order.orderId for t in by_symbol if t.order.orderRef == ref and t.order.orderId is not None}
        ref_matched = [t for t in by_symbol_active if t.order.orderRef == ref]
        children = [t for t in by_symbol_active if t.order.parentId > 0 and t.order.parentId in ref_order_ids]
        seen_ids: set[int] = set()
        for t in ref_matched + children:
            oid = t.order.orderId
            if oid is not None and oid not in seen_ids:
                seen_ids.add(oid)
                matching_trades.append(t)

        if debug and (len(matching_trades) == 0 or len(by_symbol) > 0):
            print(f'[find_orders debug] ref={ref!r}')
            print(
                f'  all_trades={
                    len(all_trades)}  by_symbol({symbol})={
                    len(by_symbol)}  by_symbol_active={
                    len(by_symbol_active)}')
            print(f'  ref_order_ids (parent IDs with orderRef==ref)={ref_order_ids}')
            print(f'  ref_matched={len(ref_matched)}  children={len(children)}')
            for t in by_symbol:
                o = t.order
                print(
                    f'  order id={
                        o.orderId} parentId={
                        o.parentId} ref={
                        o.orderRef!r} status={
                        t.orderStatus.status} action={
                        o.action}')

        return matching_trades
    except Exception as e:
        print(f'Error finding orders for {symbol} ({trader}): {e}')
        return []
