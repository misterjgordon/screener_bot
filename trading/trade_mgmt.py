"""Process position changes and execute orders (NEW, ADD, TRIM, CLOSE, FLIP)."""

import traceback
from dataclasses import dataclass
from decimal import ROUND_CEILING
from decimal import ROUND_HALF_UP
from decimal import Decimal
from typing import TYPE_CHECKING

from strategies.indicators.adr import calculate_adr
from trading.bar_loader import load_bars
from trading.config import ACTIVE_TRADING
from trading.config import DAILY_STOP
from trading.config import DEFAULT_RISK_FRACTION
from trading.config import RISK_FRACTION_DECIMALS
from trading.config import RISK_FRACTION_ROUND_UP_STEP
from trading.config import SCREENER_DAILY_STOP_FRACTION
from trading.config import STOP_OFFSET
from trading.config import TRADER_DAILY_STOP_USD
from trading.config import TRADER_ENABLED
from trading.config import TRADER_MAX_PER_TRADE_VALUE_USD
from trading.entry_mode import get_entry_mode
from trading.execution_db import save_execution_to_db
from trading.ib_trading import calculate_num_shares_from_risk
from trading.ib_trading import cancel_all_orders_for_position
from trading.ib_trading import send_bracket_order
from trading.ib_trading import send_entry_only_order
from trading.ib_trading import send_market_order
from trading.ib_trading import send_scaling_order
from trading.ib_trading import update_child_orders_for_position
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

    from trading.models import BarSeries
    from trading.models import PositionSummary


@dataclass
class EntryStopTakeProfit:
    """Market data and stop/TP for NEW/ADD: entry price, stop, take-profit, adjusted magnitude, bars."""

    entry_price: float
    stop_price: float | None
    take_profit_price: float | None
    adjusted_magnitude: float
    bundle: 'BarSeries | None'


@dataclass
class NewAddPlacementResult:
    """Result of placing a NEW or ADD order; used for CSV and no_place_reason."""

    order_id: str | None
    filled_price: float | None
    csv_shares: int | None
    csv_total_risk: float | None
    csv_risk_per_share: float | None
    no_place_reason: str | None


def get_trader_daily_stop_usd(trader: str) -> float | None:
    """Return trader's max daily loss allowance (in USD)."""
    return TRADER_DAILY_STOP_USD.get(trader)


def get_trader_max_per_trade_value_usd(trader: str) -> float | None:
    """Return trader's max notional per trade at magnitude=100 (in USD)."""
    return TRADER_MAX_PER_TRADE_VALUE_USD.get(trader)


def round_risk_fraction(raw_fraction: float) -> float:
    """Round UP risk fraction to configured step and decimals."""
    step = Decimal(str(RISK_FRACTION_ROUND_UP_STEP))
    decimals = Decimal('1').scaleb(-RISK_FRACTION_DECIMALS)
    v = Decimal(str(raw_fraction))
    rounded_up_to_step = (v / step).to_integral_value(rounding=ROUND_CEILING) * step
    rounded_up_to_decimals = rounded_up_to_step.quantize(decimals, rounding=ROUND_HALF_UP)
    return float(rounded_up_to_decimals)


def compute_risk_percent_and_trade_stop_amount(
    *,
    trader: str,
    magnitude_0_100: float,
    entry_price: float,
    stop_price: float,
    is_long: bool,
) -> tuple[float, float] | None:
    """Compute risk fraction (0-1) and account risk dollars using DAILY_STOP.

    Trader share intent from screener magnitude and max notional; dollar risk at
    stop uses entry/stop distance. Fraction is share of the trader's daily stop,
    then rounded; that fraction is applied to this account's DAILY_STOP for
    sizing and persistence.

        shares_est = ((magnitude/100) * max_per_trade_value) / entry_price
        risk_dollars_trader = shares_est * risk_per_share
        risk_fraction_raw = risk_dollars_trader / trader_daily_stop
        risk_fraction = round_risk_fraction(risk_fraction_raw)
        account_risk_dollars = risk_fraction * DAILY_STOP
    """
    trader_daily_stop_usd = get_trader_daily_stop_usd(trader)
    max_pt_usd = get_trader_max_per_trade_value_usd(trader)
    if (
        trader_daily_stop_usd is None
        or max_pt_usd is None
        or trader_daily_stop_usd <= 0
        or entry_price <= 0
    ):
        return None

    if is_long:
        risk_per_share = entry_price - stop_price
    else:
        risk_per_share = stop_price - entry_price
    if risk_per_share <= 0:
        return None

    shares_est = (abs(magnitude_0_100) / 100.0) * max_pt_usd / entry_price
    risk_dollars_trader = shares_est * risk_per_share
    risk_fraction_raw = risk_dollars_trader / trader_daily_stop_usd
    risk_fraction_rounded = round_risk_fraction(risk_fraction_raw)
    risk_dollars_account = risk_fraction_rounded * DAILY_STOP
    return risk_fraction_rounded, risk_dollars_account


def internal_magnitude_for_trade_stop_amount(trade_stop_amount: float) -> float | None:
    """
    Convert desired trade_stop_amount into the `magnitude` input expected by
    `send_bracket_order`, which internally does:
        trade_stop_amount = (DAILY_STOP * SCREENER_DAILY_STOP_FRACTION) * (magnitude / 100)
    """
    base = DAILY_STOP * SCREENER_DAILY_STOP_FRACTION
    if base <= 0:
        return None
    return (trade_stop_amount / base) * 100.0


def apply_stop_offset(stop_price: float, is_long: bool) -> float:
    """Apply configured stop buffer by side."""
    if is_long:
        return round(stop_price - STOP_OFFSET, 2)
    return round(stop_price + STOP_OFFSET, 2)


def get_entry_stop_take_profit(
    ib: 'IB',
    underlying: str,
    is_long: bool,
    delta_magnitude: float,
    change_type: str,
) -> EntryStopTakeProfit | None:
    """Load bars, compute stop and take-profit; returns None if no entry price."""
    if change_type == 'NEW':
        diagnose_market_price(ib, underlying)
    entry_price = get_market_price(ib, underlying)
    if not entry_price:
        return None
    entry_price = round(entry_price, 2)
    bundle = load_bars(ib, underlying)
    adjusted_magnitude = abs(delta_magnitude)
    if change_type == 'NEW':
        gap_percentage = calculate_gap_percentage(ib, underlying, entry_price, bundle=bundle)
        if gap_percentage and gap_percentage > 99:
            adjusted_magnitude = abs(delta_magnitude) / 10
            print(
                f'⚠️  WARNING: {underlying} gapped up {
                    gap_percentage:.2f}% (>99%) - reducing position size from {
                    abs(delta_magnitude)} to {
                    adjusted_magnitude:.2f}'
            )

    adr = calculate_adr(ib, underlying, bundle=bundle)
    stop_price: float | None = None
    take_profit_price: float | None = None
    if not adr:
        print(
            f'⚠️  WARNING: ADR not available for {underlying} - cannot calculate take profit, will send entry-only order'
        )
    else:
        position_side_str = 'long' if is_long else 'short'
        trailing_stop = calculate_trailing_stop(
            ib,
            underlying,
            prior_bars=7,
            position_side=position_side_str,
            bundle=bundle,
        )
        if trailing_stop:
            stop_price = apply_stop_offset(trailing_stop, is_long)
            print(
                f'✓ Using trailing stop for {underlying}: ${
                    stop_price:.2f} (includes ${STOP_OFFSET:.2f} offset)'
            )
        else:
            todays_range = get_todays_range(ib, underlying, bundle=bundle)
            if todays_range:
                day_low, day_high = todays_range.low, todays_range.high
                if is_long:
                    stop_price = apply_stop_offset(day_low, is_long)
                else:
                    stop_price = apply_stop_offset(day_high, is_long)
                print(
                    f'✓ Using day range stop for {underlying}: ${
                        stop_price:.2f} (day low ${
                        day_low:.2f} / high ${
                        day_high:.2f}, ${
                        STOP_OFFSET:.2f} offset)'
                )
            else:
                if is_long:
                    stop_price = entry_price - (0.5 * adr)
                else:
                    stop_price = entry_price + (0.5 * adr)
                stop_price = apply_stop_offset(stop_price, is_long)
                print(
                    f'✓ Using ADR stop for {underlying}: ${
                        stop_price:.2f} (ADR: ${adr:.2f}, includes ${STOP_OFFSET:.2f} offset)'
                )

        if stop_price:
            if is_long:
                take_profit_price = entry_price + (0.6 * adr)
            else:
                take_profit_price = entry_price - (0.6 * adr)
            take_profit_price = round(take_profit_price, 2)

    return EntryStopTakeProfit(
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        adjusted_magnitude=adjusted_magnitude,
        bundle=bundle,
    )


def place_new_order(
    ib: 'IB',
    row: 'PositionSummary',
    underlying: str,
    is_long: bool,
    market: EntryStopTakeProfit,
    shares_override: int | None,
) -> NewAddPlacementResult:
    """Place NEW order: check position/orders, then bracket or entry-only."""
    trader = row.trader or ''
    current_position = get_position_size(ib, underlying)
    has_position = current_position != 0
    has_open_order = has_open_orders_for_trader(ib, underlying, is_long, trader)
    if has_position or has_open_order:
        no_place_reason = f'existing_position_or_order (position={current_position}, open_order={has_open_order})'
        print(f'Skipping NEW order for {underlying} ({trader}): {no_place_reason}')
        return NewAddPlacementResult(None, None, None, None, None, no_place_reason)

    entry_mode = get_entry_mode(
        ib,
        trader,
        'NEW',
        underlying,
        market.entry_price,
        is_long,
        bundle=market.bundle,
    )
    if entry_mode.skip:
        print(f'Skipping NEW order for {underlying} ({trader}): entry_mode_skip')
        return NewAddPlacementResult(None, None, None, None, None, 'entry_mode_skip')

    if market.stop_price and market.take_profit_price:
        result = send_bracket_order(
            ib,
            underlying,
            is_long,
            entry_mode.entry_price,
            market.stop_price,
            market.take_profit_price,
            market.adjusted_magnitude,
            trader,
            entry_order_type=entry_mode.order_type,
            num_shares=shares_override,
        )
    else:
        if not market.stop_price:
            print(f'⚠️  WARNING: No stop loss available for {underlying} (trailing stop and ADR both failed)')
        else:
            print(
                f'WARNING: ADR not available for {underlying} - cannot calculate take profit, sending entry-only order'
            )
        result = send_entry_only_order(
            ib,
            underlying,
            is_long,
            entry_mode.entry_price,
            market.adjusted_magnitude,
            trader,
            entry_order_type=entry_mode.order_type,
            num_shares=shares_override,
        )
    no_place_reason = result.skip_reason if result.order_id is None else None
    return NewAddPlacementResult(
        result.order_id,
        result.filled_price,
        result.num_shares,
        result.total_risk,
        result.risk_per_share,
        no_place_reason,
    )


def place_add_order(
    ib: 'IB',
    row: 'PositionSummary',
    underlying: str,
    is_long: bool,
    market: EntryStopTakeProfit,
    delta_magnitude: float,
    shares_override: int | None,
) -> NewAddPlacementResult:
    """Place ADD order: scale into existing position or send new bracket/entry-only."""
    trader = row.trader or ''
    current_position = get_position_size(ib, underlying)
    has_existing_position = (is_long and current_position > 0) or (not is_long and current_position < 0)

    if has_existing_position:
        trade_stop_percent = abs(delta_magnitude) / 100.0
        trade_stop_amount = (DAILY_STOP * SCREENER_DAILY_STOP_FRACTION) * trade_stop_percent
        available_funds = get_available_funds(ib)
        if available_funds <= 0:
            return NewAddPlacementResult(None, None, None, None, None, 'no_available_funds')

        scaling_stop_price = market.stop_price
        if not scaling_stop_price:
            scaling_adr = calculate_adr(ib, underlying, bundle=market.bundle)
            if scaling_adr:
                if is_long:
                    scaling_stop_price = market.entry_price - (0.5 * scaling_adr)
                else:
                    scaling_stop_price = market.entry_price + (0.5 * scaling_adr)
            else:
                scaling_stop_price = market.entry_price * (0.98 if is_long else 1.02)
                print(f'Using assumed 2% stop for scaling order sizing: ${scaling_stop_price:.2f}')

        # Prefer estimating the trader's intended risk% using their per-trade
        # max notional and stop distance, then size shares from that.
        risk_calc = compute_risk_percent_and_trade_stop_amount(
            trader=trader,
            magnitude_0_100=abs(delta_magnitude),
            entry_price=market.entry_price,
            stop_price=scaling_stop_price,
            is_long=is_long,
        )
        if risk_calc is not None:
            risk_percent, risk_dollars = risk_calc
            row.risk_percent = risk_percent
            trade_stop_amount = risk_dollars

        num_shares_to_add = (
            shares_override
            if shares_override is not None
            else calculate_num_shares_from_risk(
                trade_stop_amount=trade_stop_amount,
                entry_price=market.entry_price,
                stop_loss_price=scaling_stop_price,
                is_long=is_long,
                available_funds=available_funds,
            )
        )
        csv_risk_per_share = trade_stop_amount / num_shares_to_add if num_shares_to_add else None
        if num_shares_to_add <= 0:
            return NewAddPlacementResult(
                None,
                None,
                num_shares_to_add,
                trade_stop_amount,
                csv_risk_per_share,
                'num_shares_to_add_zero',
            )

        has_open_order = has_open_orders(ib, underlying, is_long)
        if has_open_order:
            print(f'Skipping ADD scaling order for {underlying}: open_order_already_exists (ADD scaling)')
            return NewAddPlacementResult(
                None,
                None,
                num_shares_to_add,
                trade_stop_amount,
                csv_risk_per_share,
                'open_order_already_exists (ADD scaling)',
            )
        result = send_scaling_order(ib, underlying, is_long, market.entry_price, num_shares_to_add, trader)
        no_place_reason = result.skip_reason if result.order_id is None else None
        if result.order_id is not None and update_child_orders_for_position(
            ib, underlying, trader, num_shares_to_add
        ):
            print('   ✓ Updated child orders after ADD scaling order placed')
        return NewAddPlacementResult(
            result.order_id,
            result.filled_price,
            num_shares_to_add,
            trade_stop_amount,
            csv_risk_per_share,
            no_place_reason,
        )

    has_open_order = has_open_orders(ib, underlying, is_long)
    if has_open_order:
        print(f'Skipping ADD bracket order for {underlying}: open_order_already_exists (ADD bracket)')
        return NewAddPlacementResult(None, None, None, None, None, 'open_order_already_exists (ADD bracket)')

    entry_mode = get_entry_mode(
        ib,
        trader,
        'ADD',
        underlying,
        market.entry_price,
        is_long,
        bundle=market.bundle,
    )
    if entry_mode.skip:
        print(f'Skipping ADD bracket order for {underlying} ({trader}): entry_mode_skip')
        return NewAddPlacementResult(None, None, None, None, None, 'entry_mode_skip')

    magnitude = abs(delta_magnitude)
    if market.stop_price and market.take_profit_price:
        risk_calc = compute_risk_percent_and_trade_stop_amount(
            trader=trader,
            magnitude_0_100=magnitude,
            entry_price=entry_mode.entry_price,
            stop_price=market.stop_price,
            is_long=is_long,
        )
        if risk_calc is not None:
            risk_percent, risk_dollars = risk_calc
            row.risk_percent = risk_percent
            internal_mag = internal_magnitude_for_trade_stop_amount(risk_dollars)
            if internal_mag is not None:
                magnitude = internal_mag

        result = send_bracket_order(
            ib,
            underlying,
            is_long,
            entry_mode.entry_price,
            market.stop_price,
            market.take_profit_price,
            magnitude,
            trader,
            entry_order_type=entry_mode.order_type,
            num_shares=shares_override,
        )
    else:
        if not market.stop_price:
            print(f' WARNING: No stop loss available for {underlying} (trailing stop and ADR both failed)')
        else:
            print(
                f'WARNING: ADR not available for {underlying} - cannot calculate take profit, sending entry-only order'
            )
        result = send_entry_only_order(
            ib,
            underlying,
            is_long,
            entry_mode.entry_price,
            magnitude,
            trader,
            entry_order_type=entry_mode.order_type,
            num_shares=shares_override,
        )
    no_place_reason = result.skip_reason if result.order_id is None else None
    return NewAddPlacementResult(
        result.order_id,
        result.filled_price,
        result.num_shares,
        result.total_risk,
        result.risk_per_share,
        no_place_reason,
    )


def record_new_add_execution(
    trader: str,
    symbol: str,
    change_type: str,
    net_side: str,
    delta_magnitude: float,
    entry_price: float | None,
    stop_price: float | None,
    take_profit_price: float | None,
    order_id: str | None,
    filled_price: float | None,
    csv_shares: int | None,
    csv_total_risk: float | None,
    csv_risk_per_share: float | None,
    risk_percent: float | None,
) -> None:
    """Persist NEW/ADD execution to CSV and database."""
    ts = format_timestamp()
    save_execution_to_csv(
        trader=trader,
        symbol=symbol,
        change_type=change_type,
        net_side=net_side,
        delta_magnitude=delta_magnitude,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        order_id=order_id,
        filled_price=filled_price,
        timestamp=ts,
        shares=csv_shares,
        total_risk=csv_total_risk,
        risk_per_share=csv_risk_per_share,
        risk_percent=risk_percent,
    )
    save_execution_to_db(
        trader=trader,
        symbol=symbol,
        change_type=change_type,
        net_side=net_side,
        delta_magnitude=delta_magnitude,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        order_id=order_id,
        filled_price=filled_price,
        timestamp=ts,
        shares=csv_shares,
        total_risk=csv_total_risk,
        risk_per_share=csv_risk_per_share,
        risk_percent=risk_percent,
    )


def get_decision_price_for_recording(ib: 'IB | None', underlying: str) -> float | None:
    """Capture decision-time market price for execution logs."""
    if ib is None or not ib.isConnected():
        return None
    decision_price = get_market_price(ib, underlying)
    if not decision_price:
        print(f'Warning: Could not get market price for {underlying} - recording without decision price')
        return None
    return round(decision_price, 2)


def process_new_or_add(
    ib: 'IB | None',
    row: 'PositionSummary',
    change_type: str,
    shares_override: int | None,
) -> None:
    """Handle NEW or ADD: market data, place order, record to CSV."""
    trader = row.trader or ''
    symbol = row.symbol or ''
    underlying = row.underlying or symbol
    net_side = row.net_side or ''
    delta_magnitude = row.delta_magnitude or 0
    is_long = net_side == 'long'
    row.order_placed = False
    row.risk_percent = DEFAULT_RISK_FRACTION

    if ib is None or not ib.isConnected():
        print(f'IB not connected - skipping market data for {underlying}')
        record_new_add_execution(
            trader,
            underlying,
            change_type,
            net_side,
            delta_magnitude,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            DEFAULT_RISK_FRACTION,
        )
        return

    market = get_entry_stop_take_profit(ib, underlying, is_long, delta_magnitude, change_type)
    if market is None:
        print(f'Warning: Could not get market price for {underlying} - order not placed')
        record_new_add_execution(
            trader,
            underlying,
            change_type,
            net_side,
            delta_magnitude,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            DEFAULT_RISK_FRACTION,
        )
        return

    if market.stop_price is not None:
        risk_calc = compute_risk_percent_and_trade_stop_amount(
            trader=trader,
            magnitude_0_100=market.adjusted_magnitude,
            entry_price=market.entry_price,
            stop_price=market.stop_price,
            is_long=is_long,
        )
        if risk_calc is not None:
            risk_percent, risk_dollars = risk_calc
            row.risk_percent = risk_percent
            if change_type == 'NEW' and ACTIVE_TRADING and market.take_profit_price is not None:
                internal_mag = internal_magnitude_for_trade_stop_amount(risk_dollars)
                if internal_mag is not None:
                    market.adjusted_magnitude = internal_mag

    if not ACTIVE_TRADING:
        record_new_add_execution(
            trader,
            underlying,
            change_type,
            net_side,
            delta_magnitude,
            market.entry_price,
            market.stop_price,
            market.take_profit_price,
            None,
            None,
            None,
            None,
            None,
            row.risk_percent,
        )
        return

    if change_type == 'NEW':
        result = place_new_order(ib, row, underlying, is_long, market, shares_override)
    else:
        result = place_add_order(ib, row, underlying, is_long, market, delta_magnitude, shares_override)

    if result.order_id is not None:
        row.order_placed = True
    else:
        no_place_reason = result.no_place_reason or 'order_placement_failed_or_returned_none'
        print(f'Order not placed for {underlying} ({trader}): {no_place_reason}')
        if change_type in ['NEW', 'ADD']:
            print(
                f'Recording {change_type} for {underlying} ({trader}) with no order_id (skipped or order placement failed)'
            )

    record_new_add_execution(
        trader,
        underlying,
        change_type,
        net_side,
        delta_magnitude,
        market.entry_price,
        market.stop_price,
        market.take_profit_price,
        result.order_id,
        result.filled_price,
        result.csv_shares,
        result.csv_total_risk,
        result.csv_risk_per_share,
        row.risk_percent,
    )


def process_trim(
    ib: 'IB | None',
    row: 'PositionSummary',
    underlying: str,
    shares_override: int | None,
) -> None:
    """Handle TRIM: reduce position and optionally update child orders; record to CSV."""
    trader = row.trader or ''
    net_side = row.net_side or ''
    delta_magnitude = row.delta_magnitude or 0
    is_long = net_side == 'long'
    order_id: str | None = None
    filled_price: float | None = None
    no_place_reason: str | None = None
    change_type = 'TRIM'
    csv_shares: int | None = None
    decision_price = get_decision_price_for_recording(ib, underlying)

    if ib is None or not ib.isConnected():
        no_place_reason = 'IB not connected'
    else:
        try:
            current_position = get_position_size(ib, underlying)
            if current_position == 0:
                no_place_reason = 'no position in IB'
                print(f'⚠️  No position found in IB for {underlying} - nothing to trim')
            else:
                if shares_override is not None:
                    exit_size = shares_override
                else:
                    exit_size = abs(int(current_position * (abs(delta_magnitude) / 100.0)))
                if exit_size >= abs(current_position):
                    csv_shares = abs(current_position)
                else:
                    csv_shares = exit_size
                if exit_size > 0 and ACTIVE_TRADING:
                    if exit_size >= abs(current_position):
                        print(
                            f'⚠️  TRIM ({exit_size} shares) >= position size ({
                                abs(current_position)} shares) - converting to CLOSE'
                        )
                        cancel_all_orders_for_position(ib, underlying, trader)
                        market_result = send_market_order(ib, underlying, is_long, abs(current_position), trader)
                        order_id = market_result.order_id
                        filled_price = market_result.filled_price
                        change_type = 'CLOSE'
                    else:
                        # Exit first, then resize stop/TP children to match (same order as ADD: entry then children).
                        market_result = send_market_order(ib, underlying, is_long, exit_size, trader)
                        order_id = market_result.order_id
                        filled_price = market_result.filled_price
                        print(f'   ✓ Market order placed to trim {exit_size} shares: order_id={order_id}')
                        if update_child_orders_for_position(ib, underlying, trader, -exit_size):
                            print('   ✓ Updated child orders to match reduced position size')
                elif not ACTIVE_TRADING:
                    no_place_reason = 'ACTIVE_TRADING disabled'
                else:
                    no_place_reason = f'exit_size zero (delta_magnitude={delta_magnitude})'
        except Exception as e:
            no_place_reason = f'exception: {e}'
            print(f'Error getting position for {underlying}: {e}')
            traceback.print_exc()

    if order_id is None and no_place_reason:
        print(f'Order not placed for TRIM {underlying} ({trader}): {no_place_reason}')

    ts = format_timestamp()
    save_execution_to_csv(
        trader=trader,
        symbol=underlying,
        change_type=change_type,
        net_side=net_side,
        delta_magnitude=delta_magnitude,
        entry_price=decision_price,
        order_id=order_id,
        filled_price=filled_price,
        timestamp=ts,
        shares=csv_shares,
    )
    save_execution_to_db(
        trader=trader,
        symbol=underlying,
        change_type=change_type,
        net_side=net_side,
        delta_magnitude=delta_magnitude,
        entry_price=decision_price,
        order_id=order_id,
        filled_price=filled_price,
        timestamp=ts,
        shares=csv_shares,
    )


def process_close(
    ib: 'IB | None',
    row: 'PositionSummary',
    underlying: str,
    shares_override: int | None,
) -> None:
    """Handle CLOSE: cancel orders, exit full position, record to CSV."""
    trader = row.trader or ''
    net_side = row.net_side or ''
    delta_magnitude = row.delta_magnitude or 0
    order_id: str | None = None
    filled_price: float | None = None
    csv_shares: int | None = None
    decision_price = get_decision_price_for_recording(ib, underlying)

    print(f'🔄 CLOSE detected for {underlying} ({trader})')
    if ib is not None and ib.isConnected():
        try:
            print(f'   Cancelling all open orders for {underlying}...')
            cancel_all_orders_for_position(ib, underlying, trader)
            current_position = get_position_size(ib, underlying)
            print(f'   Current position in IB: {current_position} shares')
            if current_position != 0:
                is_long = current_position > 0
                exit_size = shares_override if shares_override is not None else abs(current_position)
                csv_shares = exit_size
                print(f'   Exiting {exit_size} shares ({"long" if is_long else "short"})')
                if ACTIVE_TRADING and exit_size > 0:
                    market_result = send_market_order(ib, underlying, is_long, exit_size, trader)
                    order_id = market_result.order_id
                    filled_price = market_result.filled_price
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

    ts = format_timestamp()
    save_execution_to_csv(
        trader=trader,
        symbol=underlying,
        change_type='CLOSE',
        net_side=net_side,
        delta_magnitude=delta_magnitude,
        entry_price=decision_price,
        order_id=order_id,
        filled_price=filled_price,
        timestamp=ts,
        shares=csv_shares,
    )
    save_execution_to_db(
        trader=trader,
        symbol=underlying,
        change_type='CLOSE',
        net_side=net_side,
        delta_magnitude=delta_magnitude,
        entry_price=decision_price,
        order_id=order_id,
        filled_price=filled_price,
        timestamp=ts,
        shares=csv_shares,
    )


def process_flip(
    row: 'PositionSummary',
    underlying: str,
    ib: 'IB | None' = None,
) -> None:
    """Handle FLIP: record to CSV and database (no order placement)."""
    decision_price = get_decision_price_for_recording(ib, underlying)
    ts = format_timestamp()
    save_execution_to_csv(
        trader=row.trader or '',
        symbol=underlying,
        change_type='FLIP',
        net_side=row.net_side or '',
        delta_magnitude=row.delta_magnitude or 0,
        entry_price=decision_price,
        timestamp=ts,
    )
    save_execution_to_db(
        trader=row.trader or '',
        symbol=underlying,
        change_type='FLIP',
        net_side=row.net_side or '',
        delta_magnitude=row.delta_magnitude or 0,
        entry_price=decision_price,
        timestamp=ts,
    )


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
        change_type: Change type (NEW, ADD, TRIM, CLOSE, FLIP)
        shares_override: If set, use this share count instead of calculating from magnitude (NEW/ADD)
            or position (TRIM/CLOSE). If None, use normal calculation.
    """
    trader = row.trader
    symbol = row.symbol
    net_side = row.net_side
    delta_magnitude = row.delta_magnitude or 0

    if not trader or not symbol or not net_side:
        return
    if delta_magnitude == 0 and change_type != 'CLOSE':
        return
    if not TRADER_ENABLED.get(trader, False):
        return
    if row.instrument_type != 'equity':
        return

    underlying = row.underlying or symbol

    if change_type in ['NEW', 'ADD']:
        if net_side in ['long', 'short']:
            process_new_or_add(ib, row, change_type, shares_override)
    elif change_type == 'TRIM':
        if net_side in ['long', 'short']:
            process_trim(ib, row, underlying, shares_override)
    elif change_type == 'CLOSE':
        process_close(ib, row, underlying, shares_override)
    elif change_type == 'FLIP':
        process_flip(row, underlying, ib=ib)
