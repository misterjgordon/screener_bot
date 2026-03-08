"""IB order execution (place and cancel orders).

Single entry point for IB trading: placing and cancelling orders (bracket,
scaling, market). Account and position queries live in trade_data. All
functions take an IB connection; connection lifecycle is managed by the
screener or check_trade.
"""

import traceback
from dataclasses import dataclass

from ib_async import IB
from ib_async import LimitOrder
from ib_async import MarketOrder
from ib_async import Stock
from ib_async import StopOrder

from trading.config import ACCOUNT_CURRENCY
from trading.config import ACTIVE_ORDER_STATUSES
from trading.config import DAILY_STOP
from trading.trade_data import find_orders_for_symbol_trader
from trading.trade_data import get_available_funds
from trading.trade_data import order_tag


@dataclass
class OrderEntry:
    """Result of placing an entry order; used for CSV logging."""

    order_id: str | None
    num_shares: int | None
    total_risk: float | None
    risk_per_share: float | None


def calculate_num_shares_from_risk(
    trade_stop_amount: float,
    entry_price: float,
    stop_loss_price: float,
    is_long: bool,
    available_funds: float,
) -> int:
    """
    Calculate number of shares from risk, capped by available capital.

    Uses magnitude * daily_stop (trade_stop_amount) and entry/stop to get a
    risk-based share count. When there are not enough available funds for that
    size, the script designates available_funds to the trade and sizes down:
    same entry and stop price, fewer shares. Effectively replaces the
    risk-based size with an affordable size when capital is insufficient.

    Args:
        trade_stop_amount: Maximum dollar amount to risk (magnitude * daily stop).
        entry_price: Entry price.
        stop_loss_price: Stop loss price.
        is_long: True for long positions, False for short.
        available_funds: Available funds in account (used as notional cap when insufficient).

    Returns:
        int: Number of shares (floored), or 0 if calculation not possible.
    """
    if is_long:
        risk_per_share = entry_price - stop_loss_price
    else:
        risk_per_share = stop_loss_price - entry_price

    if risk_per_share <= 0:
        return 0

    shares_from_risk = int(trade_stop_amount / risk_per_share)
    shares_from_funds = int(available_funds / entry_price)
    num_shares = min(shares_from_risk, shares_from_funds)

    return max(0, num_shares)


def cancel_all_orders_for_position(ib: IB, symbol: str, trader: str = '') -> int:
    """
    Cancel all open orders for a specific symbol and trader.

    Args:
        ib: IB connection
        symbol: Symbol to cancel orders for
        trader: Trader name (optional)

    Returns:
        int: Number of orders cancelled
    """
    cancelled_count = 0
    try:
        matching_trades = find_orders_for_symbol_trader(ib, symbol, trader)

        for trade in matching_trades:
            order = trade.order
            status = trade.orderStatus

            if status.status in ACTIVE_ORDER_STATUSES:
                try:
                    ib.cancelOrder(order)
                    cancelled_count += 1
                    print(f'   ✓ Cancelled order {order.orderId} ({order.action} {order.totalQuantity} {symbol})')
                except Exception as e:
                    print(f'Error cancelling order {order.orderId}: {e}')

        if cancelled_count > 0:
            print(f'   ✓ Cancelled {cancelled_count} order(s) for {symbol}')

        return cancelled_count
    except Exception as e:
        print(f'Error cancelling orders for {symbol} ({trader}): {e}')
        return 0


def update_child_orders_for_position(ib: IB, symbol: str, trader: str, share_delta: int) -> bool:
    """
    Update existing child orders (stop loss and take profit) for a position.

    Args:
        ib: IB connection
        symbol: Symbol to update orders for
        trader: Trader name
        share_delta: Change in shares (positive for ADD, negative for TRIM)

    Returns:
        bool: True if orders were updated, False if no child orders found (fallback to current behavior)
    """
    try:
        matching_trades = find_orders_for_symbol_trader(ib, symbol, trader)

        child_orders = []
        for trade in matching_trades:
            order = trade.order
            if order.parentId > 0:
                child_orders.append(trade)

        if not child_orders:
            return False

        updated_count = 0
        for trade in child_orders:
            order = trade.order
            status = trade.orderStatus

            if status.status in ACTIVE_ORDER_STATUSES:
                current_quantity = order.totalQuantity
                new_quantity = current_quantity + share_delta

                if new_quantity <= 0:
                    try:
                        ib.cancelOrder(order)
                        print(f'   ✓ Cancelled child order {order.orderId} (would be {new_quantity} shares)')
                    except Exception as e:
                        print(f'Error cancelling child order {order.orderId}: {e}')
                else:
                    try:
                        order.totalQuantity = new_quantity
                        ib.qualifyContracts(trade.contract)
                        ib.placeOrder(trade.contract, order)
                        ib.sleep(0.2)
                        updated_count += 1
                        print(f'   ✓ Updated child order {order.orderId}: {current_quantity} -> {new_quantity} shares')
                    except Exception as e:
                        print(f'Error updating child order {order.orderId}: {e}')

        if updated_count > 0:
            print(f'   ✓ Updated {updated_count} child order(s) for {symbol}')
            return True

        return False
    except Exception as e:
        print(f'Error updating child orders for {symbol} ({trader}): {e}')
        traceback.print_exc()
        return False


def send_scaling_order(
    ib: IB,
    symbol: str,
    is_long: bool,
    entry_price: float,
    num_shares: int,
    trader: str = '',
) -> str | None:
    """
    Send a stop order to scale into an existing position (for ADD changes).
    Uses a stop entry order to add shares to an existing position.

    Args:
        ib: IB connection instance
        symbol: Stock symbol
        is_long: True for long, False for short
        entry_price: Entry/stop trigger price
        num_shares: Number of shares to add

    Returns:
        str | None: Order ID string if successful, None otherwise
    """
    try:
        if num_shares <= 0:
            print(f'Error: Invalid share quantity {num_shares} for {symbol}')
            return None

        action = 'BUY' if is_long else 'SELL'

        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)

        ref = order_tag(trader)

        order = StopOrder(action, num_shares, entry_price)
        order.tif = 'GTC'
        order.orderRef = ref

        trade = ib.placeOrder(contract, order)
        ib.sleep(0.2)

        order_id = str(order.orderId) if order.orderId else 'pending'
        print(f'Scaling order placed for {symbol}: {action} {num_shares} shares @ ${entry_price:.2f} (STOP-ENTRY)')
        return order_id

    except Exception as e:
        print(f'Error placing scaling order for {symbol}: {e}')
        traceback.print_exc()
        return None


def send_bracket_order(
    ib: IB,
    symbol: str,
    is_long: bool,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    magnitude: float,
    trader: str = '',
    entry_order_type: str = 'stop',
) -> OrderEntry:
    """
    Send a bracket order to IB for NEW/ADD positions.

    Args:
        ib: IB connection instance
        symbol: Stock symbol
        is_long: True for long, False for short
        entry_price: Entry trigger price (stop trigger or limit price)
        stop_price: Stop loss price
        take_profit_price: Take profit price
        magnitude: Position magnitude (used to calculate trade_stop_percent)
        entry_order_type: 'stop' for StopOrder at entry_price, 'limit' for LimitOrder at entry_price

    Returns:
        OrderEntry: order_id and sizing (num_shares, total_risk, risk_per_share) for CSV logging.
    """
    order_result = OrderEntry(None, None, None, None)
    try:
        trade_stop_percent = magnitude / 100.0
        trade_stop_amount = DAILY_STOP * trade_stop_percent

        available_funds = get_available_funds(ib)
        if available_funds <= 0:
            print(f'Error: Insufficient available funds: ${available_funds:.2f}')
            return order_result

        num_shares = calculate_num_shares_from_risk(
            trade_stop_amount=trade_stop_amount,
            entry_price=entry_price,
            stop_loss_price=stop_price,
            is_long=is_long,
            available_funds=available_funds,
        )

        if num_shares == 0:
            print(f'Error: Calculated share quantity is zero for {symbol}')
            return order_result

        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)

        if is_long:
            entry_action = 'BUY'
            stop_action = 'SELL'
            take_profit_action = 'SELL'
        else:
            entry_action = 'SELL'
            stop_action = 'BUY'
            take_profit_action = 'BUY'

        ref = order_tag(trader)

        if entry_order_type == 'limit':
            parent_order = LimitOrder(entry_action, num_shares, entry_price)
        else:
            parent_order = StopOrder(entry_action, num_shares, entry_price)
        parent_order.tif = 'GTC'
        parent_order.transmit = False
        parent_order.orderRef = ref

        parent_trade = ib.placeOrder(contract, parent_order)
        ib.sleep(0.5)

        parent_order_id = parent_order.orderId
        if parent_order_id is None:
            parent_order_id = parent_trade.order.orderId

        if parent_order_id is None:
            print(f'Error: Could not obtain parent order ID for {symbol}')
            return order_result

        take_profit_order = LimitOrder(take_profit_action, num_shares, take_profit_price)
        take_profit_order.tif = 'GTC'
        take_profit_order.parentId = parent_order_id
        take_profit_order.transmit = False
        take_profit_order.orderRef = ref

        stop_order = StopOrder(stop_action, num_shares, stop_price)
        stop_order.tif = 'GTC'
        stop_order.parentId = parent_order_id
        stop_order.transmit = True
        stop_order.orderRef = ref

        ib.placeOrder(contract, take_profit_order)
        ib.placeOrder(contract, stop_order)
        ib.sleep(0.5)

        order_id = str(parent_order_id)
        risk_per_share = trade_stop_amount / num_shares if num_shares else None
        entry_type_str = 'LIMIT' if entry_order_type == 'limit' else 'STOP-ENTRY'
        print(
            f'Bracket order placed for {symbol}: {entry_action} {num_shares} @ ${
                entry_price:.2f} ({entry_type_str}), Stop Loss @ ${
                stop_price:.2f}, TP @ ${
                take_profit_price:.2f}')
        return OrderEntry(order_id, num_shares, trade_stop_amount, risk_per_share)

    except Exception as e:
        print(f'Error placing bracket order for {symbol}: {e}')
        traceback.print_exc()
        return order_result


def send_entry_only_order(
    ib: IB,
    symbol: str,
    is_long: bool,
    entry_price: float,
    magnitude: float,
    trader: str = '',
    entry_order_type: str = 'stop',
) -> OrderEntry:
    """
    Send an entry order without stop loss (when trailing stop and ADR both fail).

    Args:
        ib: IB connection instance
        symbol: Stock symbol
        is_long: True for long, False for short
        entry_price: Entry trigger price (stop trigger or limit price)
        magnitude: Position magnitude (used to calculate trade_stop_percent)
        entry_order_type: 'stop' for StopOrder, 'limit' for LimitOrder at entry_price

    Returns:
        OrderEntry: order_id and sizing (num_shares, total_risk, risk_per_share) for CSV logging.
    """
    order_result = OrderEntry(None, None, None, None)
    try:
        trade_stop_percent = magnitude / 100.0
        trade_stop_amount = DAILY_STOP * trade_stop_percent

        available_funds = get_available_funds(ib)
        if available_funds <= 0:
            print(f'Error: Insufficient available funds: ${available_funds:.2f}')
            return order_result

        assumed_risk_percent = 0.02
        num_shares = int((available_funds * trade_stop_percent) / (entry_price * assumed_risk_percent))

        if num_shares == 0:
            print(f'Error: Calculated share quantity is zero for {symbol}')
            return order_result

        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)

        action = 'BUY' if is_long else 'SELL'

        ref = order_tag(trader)

        if entry_order_type == 'limit':
            order = LimitOrder(action, num_shares, entry_price)
        else:
            order = StopOrder(action, num_shares, entry_price)
        order.tif = 'GTC'
        order.orderRef = ref

        trade = ib.placeOrder(contract, order)
        ib.sleep(1)

        order_id = str(order.orderId) if order.orderId else 'pending'
        risk_per_share = trade_stop_amount / num_shares if num_shares else None
        entry_type_str = 'LIMIT' if entry_order_type == 'limit' else 'STOP'
        print(
            f'WARNING: Entry-only order placed for {symbol}: {action} {num_shares} @ ${entry_price:.2f} ({entry_type_str}, NO STOP LOSS)')
        return OrderEntry(order_id, num_shares, trade_stop_amount, risk_per_share)

    except Exception as e:
        print(f'Error placing entry-only order for {symbol}: {e}')
        traceback.print_exc()
        return order_result


def send_market_order(ib: IB, symbol: str, is_long: bool, position_size: int, trader: str = '') -> str | None:
    """
    Send a market order to IB for TRIM positions (exit).

    Args:
        ib: IB connection instance
        symbol: Stock symbol
        is_long: True if currently long (so we SELL), False if short (so we BUY)
        position_size: Number of shares to exit
        trader: Trader name for order tagging (optional)

    Returns:
        str | None: Order ID string if successful, None otherwise
    """
    try:
        if position_size <= 0:
            print(f'Error: Invalid position size {position_size} for {symbol}')
            return None

        action = 'SELL' if is_long else 'BUY'

        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)

        ref = order_tag(trader)

        order = MarketOrder(action, position_size)
        order.tif = 'GTC'
        order.orderRef = ref

        trade = ib.placeOrder(contract, order)
        ib.sleep(0.5)

        order_id = str(order.orderId) if order.orderId else 'pending'
        print(f'Market order placed for {symbol}: {action} {position_size} shares (TRIM/exit)')
        return order_id

    except Exception as e:
        print(f'Error placing market order for {symbol}: {e}')
        traceback.print_exc()
        return None
