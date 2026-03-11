"""Process position changes and execute orders (NEW, ADD, TRIM, CLOSE, FLIP)."""

import traceback
from typing import TYPE_CHECKING

from trading.bar_loader import load_bars
from trading.config import ACTIVE_TRADING
from trading.config import DAILY_STOP
from trading.config import STOP_OFFSET
from trading.config import TRADER_ENABLED
from trading.entry_mode import get_entry_mode
from trading.ib_trading import calculate_num_shares_from_risk
from trading.ib_trading import cancel_all_orders_for_position
from trading.ib_trading import send_bracket_order
from trading.ib_trading import send_entry_only_order
from trading.ib_trading import send_market_order
from trading.ib_trading import send_scaling_order
from trading.ib_trading import update_child_orders_for_position
from trading.market_data import calculate_adr
from trading.market_data import calculate_gap_percentage
from trading.market_data import calculate_trailing_stop
from trading.market_data import diagnose_market_price
from trading.market_data import get_market_price
from trading.market_data import get_todays_range
from trading.record_executions import format_timestamp
from trading.record_executions import save_execution_to_csv
from trading.trade_data import get_available_funds
from trading.trade_data import get_position_size
from trading.trade_data import has_open_orders
from trading.trade_data import has_open_orders_for_trader

if TYPE_CHECKING:
    from ib_async import IB

    from trading.models import PositionSummary


def process_execution_change(
    ib: 'IB | None',
    row: 'PositionSummary',
    change_type: str,
    shares_override: int | None = None,
) -> None:
    """
    Process a position change and execute orders if active_trading is enabled.

    Args:
        ib: IB connection (None if not connected)
        row: Position summary row with change annotations
        change_type: Change type (NEW, ADD, TRIM, CLOSE)
        shares_override: If set, use this share count instead of calculating from magnitude (NEW/ADD)
            or position (TRIM/CLOSE). If None, use normal calculation.
    """
    trader = row.trader
    symbol = row.symbol
    net_side = row.net_side
    delta_magnitude = row.delta_magnitude or 0

    if not trader or not symbol or not net_side:
        return

    # Skip if there's no magnitude change (delta_magnitude == 0)
    # EXCEPT for CLOSE - we need to process CLOSE even if delta is 0 because we need to exit the position
    if delta_magnitude == 0 and change_type != 'CLOSE':
        return

    # Check if trader is enabled
    if not TRADER_ENABLED.get(trader, False):
        return

    # Only process equity instruments
    if row.instrument_type != 'equity':
        return

    # Extract underlying symbol (should be same as symbol for equity)
    underlying = row.underlying or symbol

    timestamp = format_timestamp()
    entry_price = None
    stop_price = None
    take_profit_price = None
    order_id = None
    no_place_reason: str | None = None  # Set when we skip or fail so we can log why order wasn't placed
    csv_shares: int | None = None
    csv_total_risk: float | None = None
    csv_risk_per_share: float | None = None
    if change_type in ['NEW', 'ADD']:
        row.order_placed = False

    # Process based on change type
    if change_type in ['NEW', 'ADD']:
        if net_side == 'long' or net_side == 'short':
            is_long = (net_side == 'long')

            # Get market data if IB is connected
            if ib is not None and ib.isConnected():
                # For NEW changes, run diagnostic first (before trying to get price)
                if change_type == 'NEW':
                    diagnose_market_price(ib, underlying)

                entry_price = get_market_price(ib, underlying)
                if entry_price:
                    entry_price = round(entry_price, 2)
                    stop_price = None
                    take_profit_price = None

                    bundle = load_bars(ib, underlying)
                    adjusted_magnitude = abs(delta_magnitude)

                    if change_type == 'NEW':
                        gap_percentage = calculate_gap_percentage(
                            ib, underlying, entry_price, bundle=bundle
                        )
                        if gap_percentage and gap_percentage > 99:
                            adjusted_magnitude = abs(delta_magnitude) / 10
                            print(
                                f'⚠️  WARNING: {underlying} gapped up {gap_percentage:.2f}% (>99%) - reducing position size from {abs(delta_magnitude)} to {adjusted_magnitude:.2f}')

                    adr = calculate_adr(ib, underlying, bundle=bundle)

                    if not adr:
                        # ADR not available - cannot use trailing stop, will send entry-only order
                        print(
                            f'⚠️  WARNING: ADR not available for {underlying} - cannot calculate take profit, will send entry-only order')
                        stop_price = None
                        take_profit_price = None
                    else:
                        position_side_str = 'long' if is_long else 'short'
                        trailing_stop = calculate_trailing_stop(
                            ib, underlying,
                            prior_bars=7,
                            position_side=position_side_str,
                            bundle=bundle,
                        )

                        if trailing_stop:
                            stop_price = round(trailing_stop, 2)
                            print(f'✓ Using trailing stop for {underlying}: ${stop_price:.2f}')
                        else:
                            todays_range = get_todays_range(
                                ib, underlying, bundle=bundle
                            )
                            if todays_range:
                                day_low, day_high = todays_range.low, todays_range.high
                                if is_long:
                                    stop_price = round(day_low - STOP_OFFSET, 2)
                                else:
                                    stop_price = round(day_high + STOP_OFFSET, 2)
                                print(
                                    f'✓ Using day range stop for {underlying}: ${stop_price:.2f} (day low ${day_low:.2f} / high ${day_high:.2f}, ${STOP_OFFSET:.2f} offset)')
                            else:
                                # FALLBACK: Use ADR for stop if day range also unavailable
                                if is_long:
                                    stop_price = entry_price - (0.5 * adr)
                                else:
                                    stop_price = entry_price + (0.5 * adr)
                                stop_price = round(stop_price, 2)
                                print(f'✓ Using ADR stop for {underlying}: ${stop_price:.2f} (ADR: ${adr:.2f})')

                        # Calculate take profit using ADR (required)
                        if stop_price:
                            if is_long:
                                take_profit_price = entry_price + (0.6 * adr)
                            else:
                                take_profit_price = entry_price - (0.6 * adr)
                            take_profit_price = round(take_profit_price, 2)

                    # Send orders if active trading is enabled
                    if ACTIVE_TRADING:
                        # For NEW changes, check if there's already an order or position
                        if change_type == 'NEW':
                            # Check for existing position (account-level; one position per symbol)
                            current_position = get_position_size(ib, underlying)
                            has_position = current_position != 0
                            # Only skip if this trader already has an open order (not another trader's)
                            has_open_order = has_open_orders_for_trader(ib, underlying, is_long, trader)

                            if has_position or has_open_order:
                                no_place_reason = f'existing_position_or_order (position={current_position}, open_order={has_open_order})'
                                print(f'Skipping NEW order for {underlying} ({trader}): {no_place_reason}')
                            else:
                                entry_mode = get_entry_mode(
                                    ib, underlying, entry_price, is_long, bundle=bundle
                                )
                                if entry_mode.skip:
                                    no_place_reason = 'entry_mode_skip'
                                    print(f'Skipping NEW order for {underlying} ({trader}): {no_place_reason}')
                                elif stop_price and take_profit_price:
                                    result = send_bracket_order(
                                        ib, underlying, is_long, entry_mode.entry_price,
                                        stop_price, take_profit_price, adjusted_magnitude, trader,
                                        entry_order_type=entry_mode.order_type,
                                        num_shares=shares_override,
                                    )
                                    order_id = result.order_id
                                    csv_shares = result.num_shares
                                    csv_total_risk = result.total_risk
                                    csv_risk_per_share = result.risk_per_share
                                else:
                                    if not stop_price:
                                        print(
                                            f'⚠️  WARNING: No stop loss available for {underlying} (trailing stop and ADR both failed)')
                                    else:
                                        print(
                                            f'WARNING: ADR not available for {underlying} - cannot calculate take profit, sending entry-only order')
                                    result = send_entry_only_order(
                                        ib, underlying, is_long, entry_mode.entry_price,
                                        adjusted_magnitude, trader,
                                        entry_order_type=entry_mode.order_type,
                                        num_shares=shares_override,
                                    )
                                    order_id = result.order_id
                                    csv_shares = result.num_shares
                                    csv_total_risk = result.total_risk
                                    csv_risk_per_share = result.risk_per_share
                        elif change_type == 'ADD':
                            current_position = get_position_size(ib, underlying)
                            # Check if position exists and is in the same direction
                            has_existing_position = (
                                (is_long and current_position > 0) or
                                (not is_long and current_position < 0)
                            )

                            if has_existing_position:
                                # First, try to update existing child orders (stop loss and take profit)
                                # Calculate shares to add based on delta_magnitude
                                trade_stop_percent = abs(delta_magnitude) / 100.0
                                trade_stop_amount = DAILY_STOP * trade_stop_percent
                                available_funds = get_available_funds(ib)

                                # For scaling, we need a stop price for sizing calculation
                                # Use stop_price if available, otherwise calculate from ADR
                                scaling_stop_price = stop_price
                                if not scaling_stop_price:
                                    scaling_adr = calculate_adr(
                                        ib, underlying, bundle=bundle
                                    )
                                    if scaling_adr:
                                        if is_long:
                                            scaling_stop_price = entry_price - (0.5 * scaling_adr)
                                        else:
                                            scaling_stop_price = entry_price + (0.5 * scaling_adr)
                                    else:
                                        # No ADR available - use conservative 2% stop for sizing
                                        scaling_stop_price = entry_price * (0.98 if is_long else 1.02)
                                        print(
                                            f'Using assumed 2% stop for scaling order sizing: ${scaling_stop_price:.2f}')

                                if available_funds <= 0:
                                    no_place_reason = 'no_available_funds'
                                if available_funds > 0:
                                    if shares_override is not None:
                                        num_shares_to_add = shares_override
                                    else:
                                        num_shares_to_add = calculate_num_shares_from_risk(
                                            trade_stop_amount=trade_stop_amount,
                                            entry_price=entry_price,
                                            stop_loss_price=scaling_stop_price,
                                            is_long=is_long,
                                            available_funds=available_funds
                                        )
                                    csv_shares = num_shares_to_add
                                    csv_total_risk = trade_stop_amount
                                    csv_risk_per_share = (
                                        trade_stop_amount / num_shares_to_add
                                        if num_shares_to_add else None
                                    )
                                    if num_shares_to_add > 0:
                                        # Try to update existing child orders first
                                        child_orders_updated = update_child_orders_for_position(
                                            ib, underlying, trader, num_shares_to_add
                                        )

                                        if not child_orders_updated:
                                            # No child orders found - fall back to current behavior (scaling order)
                                            # Check if there's already an open order (to avoid duplicate scaling orders)
                                            has_open_order = has_open_orders(ib, underlying, is_long)
                                            if has_open_order:
                                                no_place_reason = 'open_order_already_exists (ADD scaling)'
                                                print(f'Skipping ADD scaling order for {underlying}: {no_place_reason}')
                                            else:
                                                # Scale into existing position with a simple stop order
                                                order_id = send_scaling_order(
                                                    ib, underlying, is_long, entry_price, num_shares_to_add, trader
                                                )
                                        else:
                                            # Child orders were updated - no need to create new order
                                            no_place_reason = 'add_child_orders_updated_no_new_order'
                                    else:
                                        no_place_reason = 'num_shares_to_add_zero'
                            else:
                                # No existing position, check for open orders before creating new bracket
                                has_open_order = has_open_orders(ib, underlying, is_long)
                                if has_open_order:
                                    no_place_reason = 'open_order_already_exists (ADD bracket)'
                                    print(f'Skipping ADD bracket order for {underlying}: {no_place_reason}')
                                else:
                                    entry_mode = get_entry_mode(
                                        ib, underlying, entry_price, is_long, bundle=bundle
                                    )
                                    if entry_mode.skip:
                                        no_place_reason = 'entry_mode_skip'
                                        print(
                                            f'Skipping ADD bracket order for {underlying} ({trader}): {no_place_reason}')
                                    elif stop_price and take_profit_price:
                                        result = send_bracket_order(
                                            ib, underlying, is_long, entry_mode.entry_price,
                                            stop_price, take_profit_price, abs(delta_magnitude), trader,
                                            entry_order_type=entry_mode.order_type,
                                            num_shares=shares_override,
                                        )
                                        order_id = result.order_id
                                        csv_shares = result.num_shares
                                        csv_total_risk = result.total_risk
                                        csv_risk_per_share = result.risk_per_share
                                    else:
                                        if not stop_price:
                                            print(
                                                f' WARNING: No stop loss available for {underlying} (trailing stop and ADR both failed)')
                                        else:
                                            print(
                                                f'WARNING: ADR not available for {underlying} - cannot calculate take profit, sending entry-only order')
                                        result = send_entry_only_order(
                                            ib, underlying, is_long, entry_mode.entry_price,
                                            abs(delta_magnitude), trader,
                                            entry_order_type=entry_mode.order_type,
                                            num_shares=shares_override,
                                        )
                                        order_id = result.order_id
                                        csv_shares = result.num_shares
                                        csv_total_risk = result.total_risk
                                        csv_risk_per_share = result.risk_per_share
                else:
                    # No entry price available
                    no_place_reason = 'no_market_price'
                    print(f'Warning: Could not get market price for {underlying} - order not placed')
            else:
                no_place_reason = 'ib_not_connected'
                print(f'IB not connected - skipping market data for {underlying}')

            if change_type in ['NEW', 'ADD'] and order_id is not None:
                row.order_placed = True
            if change_type in ['NEW', 'ADD'] and order_id is None:
                if not no_place_reason:
                    no_place_reason = 'order_placement_failed_or_returned_none'
                print(f'Order not placed for {underlying} ({trader}): {no_place_reason}')
            # Save to CSV (always save, even if order failed)
            if change_type in ['NEW', 'ADD'] and order_id is None:
                print(
                    f'Recording {change_type} for {underlying} ({trader}) with no order_id (skipped or order placement failed)')
            save_execution_to_csv(
                trader=trader,
                symbol=underlying,
                change_type=change_type,
                net_side=net_side,
                delta_magnitude=delta_magnitude,
                entry_price=entry_price,
                stop_price=stop_price,
                take_profit_price=take_profit_price,
                order_id=order_id,
                timestamp=timestamp,
                shares=csv_shares,
                total_risk=csv_total_risk,
                risk_per_share=csv_risk_per_share,
            )

    elif change_type == 'TRIM':
        if net_side in ['long', 'short']:
            is_long = (net_side == 'long')

            if ib is not None and ib.isConnected():
                # Get current position size from IB
                try:
                    current_position = get_position_size(ib, underlying)

                    if current_position != 0:
                        # Calculate shares to trim based on delta_magnitude
                        # This is approximate - real implementation would track position sizes
                        if shares_override is not None:
                            exit_size = shares_override
                        else:
                            exit_size = abs(int(current_position * (abs(delta_magnitude) / 100.0)))

                        if exit_size > 0 and ACTIVE_TRADING:
                            # Check if the TRIM would result in closing the position
                            # If exit_size >= abs(current_position), we should CLOSE instead
                            if exit_size >= abs(current_position):
                                print(
                                    f'⚠️  TRIM ({exit_size} shares) >= position size ({abs(current_position)} shares) - converting to CLOSE')
                                # Cancel all orders and exit entire position (same as CLOSE)
                                cancel_all_orders_for_position(ib, underlying, trader)
                                order_id = send_market_order(ib, underlying, is_long, abs(current_position), trader)
                                # Update change_type for CSV logging
                                change_type = 'CLOSE'
                            else:
                                # TRIM is less than position size - reduce position AND update child orders
                                # Always send market order to reduce the actual position
                                order_id = send_market_order(ib, underlying, is_long, exit_size, trader)
                                print(f'   ✓ Market order placed to trim {exit_size} shares: order_id={order_id}')

                                # Also update child orders to match the new position size
                                # This keeps stop loss and take profit in sync with the reduced position
                                child_orders_updated = update_child_orders_for_position(
                                    ib, underlying, trader, -exit_size
                                )
                                if child_orders_updated:
                                    print('   ✓ Updated child orders to match reduced position size')
                    else:
                        print(f'⚠️  No position found in IB for {underlying} - nothing to trim')
                except Exception as e:
                    print(f'Error getting position for {underlying}: {e}')
                    traceback.print_exc()

            # Save to CSV
            save_execution_to_csv(
                trader=trader,
                symbol=underlying,
                change_type=change_type,
                net_side=net_side,
                delta_magnitude=delta_magnitude,
                order_id=order_id,
                timestamp=timestamp
            )

    elif change_type == 'CLOSE':
        # CLOSE: Position went flat - exit the entire position
        print(f'🔄 CLOSE detected for {underlying} ({trader})')
        if ib is not None and ib.isConnected():
            try:
                # First, cancel all open orders (stop loss, take profit, entry orders) for this position
                print(f'   Cancelling all open orders for {underlying}...')
                cancel_all_orders_for_position(ib, underlying, trader)

                # Get current position size from IB
                current_position = get_position_size(ib, underlying)
                print(f'   Current position in IB: {current_position} shares')
                if current_position != 0:
                    # Exit entire position
                    is_long = current_position > 0
                    exit_size = shares_override if shares_override is not None else abs(current_position)
                    print(f'   Exiting {exit_size} shares ({"long" if is_long else "short"})')
                    if ACTIVE_TRADING and exit_size > 0:
                        order_id = send_market_order(ib, underlying, is_long, exit_size, trader)
                        print(f'   ✓ Market order placed: order_id={order_id}')
                    else:
                        print(f'   ⚠️  ACTIVE_TRADING is {ACTIVE_TRADING} or exit_size={exit_size}')
                else:
                    print(f'   ⚠️  No position found in IB for {underlying} - nothing to close')
            except Exception as e:
                print(f'   ❌ Error closing position for {underlying}: {e}')
                traceback.print_exc()
        else:
            print('   ❌ IB not connected - cannot check position or place order')

        # Save to CSV
        save_execution_to_csv(
            trader=trader,
            symbol=underlying,
            change_type=change_type,
            net_side=net_side,
            delta_magnitude=delta_magnitude,
            order_id=order_id,
            timestamp=timestamp
        )

    elif change_type == 'FLIP':
        # FLIP: exit old position and enter new position
        # This is complex - for now, just track it
        save_execution_to_csv(
            trader=trader,
            symbol=underlying,
            change_type=change_type,
            net_side=net_side,
            delta_magnitude=delta_magnitude,
            timestamp=timestamp
        )
