import asyncio
import math
from typing import Optional, Dict
from datetime import datetime, date
# Create and set an event loop BEFORE importing ib_insync
asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB, Stock, LimitOrder, MarketOrder, StopOrder

# Maximum reasonable PnL value (in USD) - used for validation
# Set to $1 billion as a sanity check
MAX_REASONABLE_PNL = 100_000.0
# constants 

MAX_NOTIONAL_FRACTION = 0.01  # do not risk more than 1 percent of AvailableFunds on test order
DAILY_STOP = 200  # USD - maximum daily loss allowed
ACCOUNT_CURRENCY = "USD"
IB_HOST = "127.0.0.1"
IB_PORT = 7497  # Use 7497 for TWS paper trading, 7496 for TWS live, 4001 for IB Gateway paper
IB_CLIENT_ID = 2  # Use different client ID from jobot.py
# Order tagging
ORDER_TAG = "jobot"  # Tag to identify orders placed by this bot (different from SMB bot's "SMB" tag)
#%%
def account_info(ib: IB):
    account_values = ib.accountValues()
    for av in account_values:
        #print(f"{av.tag:25}: {av.value} {av.currency})")
        # show available funds only
        if av.tag == "AvailableFunds":
           print(f"Available Funds: {av.value} {av.currency}")

def get_available_funds(ib: IB, currency: str = ACCOUNT_CURRENCY) -> float:
    """Get available funds for the specified currency."""
    for av in ib.accountValues():
        if av.tag == "AvailableFunds" and av.currency == currency:
            try:
                return float(av.value)
            except ValueError:
                pass
    return 0.0

#%%
def get_account_value(ib: IB, tag: str, currency: str = ACCOUNT_CURRENCY) -> Optional[float]:
    """
    Get a specific account value by tag.
    
    Args:
        ib: IB connection instance
        tag: Account value tag (e.g., "DailyPnL", "UnrealizedPnL", "RealizedPnL")
        currency: Currency filter (default: ACCOUNT_CURRENCY)
    
    Returns:
        float value if found, None otherwise
    """
    for av in ib.accountValues():
        if av.tag == tag and av.currency == currency:
            try:
                return float(av.value)
            except (ValueError, TypeError):
                pass
    return None
#%%

def list_all_account_values(ib: IB, currency: str = ACCOUNT_CURRENCY):
    """
    List all available account values for debugging purposes.
    Useful to see what tags IB actually uses for PnL data.
    
    Args:
        ib: IB connection instance
        currency: Currency filter (default: ACCOUNT_CURRENCY)
    """
    print(f"\nAvailable Account Values ({currency}):")
    print("-" * 60)
    for av in ib.accountValues():
        if av.currency == currency:
            # Highlight PnL-related tags
            tag_lower = av.tag.lower()
            if "pnl" in tag_lower or "profit" in tag_lower or "loss" in tag_lower or "daily" in tag_lower:
                print(f"  *** {av.tag:30}: {av.value}")
            else:
                print(f"     {av.tag:30}: {av.value}")
    print("-" * 60)


def is_valid_pnl_value(value: any) -> bool:
    """
    Validate that a PnL value is reasonable and not corrupted.
    
    Args:
        value: The value to validate
    
    Returns:
        True if the value is valid, False otherwise
    """
    if value is None:
        return False
    
    try:
        float_val = float(value)
        
        # Check for NaN or infinity
        if math.isnan(float_val) or math.isinf(float_val):
            return False
        
        # Check if value is within reasonable bounds
        if abs(float_val) > MAX_REASONABLE_PNL:
            return False
        
        return True
    except (ValueError, TypeError, OverflowError):
        return False


def get_pnl_single_via_reqPnLSingle(
    ib: IB, 
    contract, 
    account_id: Optional[str] = None, 
    timeout: float = 5.0
) -> Optional[Dict[str, Optional[float]]]:
    """
    Get PnL for a specific position using reqPnLSingle API call (ib_insync style).
    
    Args:
        ib: IB connection instance
        contract: Contract object for the position
        account_id: Account ID (if None, will try to get from managed accounts)
        timeout: Maximum time to wait for response in seconds
    
    Returns:
        Dictionary with 'daily_pnl', 'unrealized_pnl', 'realized_pnl', 'position', 'value' or None
    """
    if not account_id:
        try:
            managed_accounts = ib.managedAccounts()
            if managed_accounts:
                account_id = managed_accounts[0]
            else:
                account_summary = ib.accountSummary()
                if account_summary:
                    account_id = account_summary[0].account
        except Exception:
            pass
    
    if not account_id:
        print("Warning: Could not determine account ID for reqPnLSingle")
        return None
    
    # Get contract ID - qualify the contract if needed
    if not hasattr(contract, 'conId') or not contract.conId:
        try:
            qualified = ib.qualifyContracts(contract)
            if not qualified:
                return None
            contract = qualified[0]
        except Exception as e:
            print(f"Error qualifying contract: {e}")
            return None
    
    con_id = contract.conId
    pnl_single_obj = None
    
    try:
        # Request PnL for this specific position (ib_insync style)
        # Signature: reqPnLSingle(account, modelCode, conId)
        pnl_single_obj = ib.reqPnLSingle(account_id, "", con_id)
        
        # Wait for data to arrive
        ib.sleep(0.2)  # Give it time for initial data
        
        # Try to get values directly from the PnLSingle object
        daily_pnl = getattr(pnl_single_obj, 'dailyPnL', None)
        unrealized_pnl = getattr(pnl_single_obj, 'unrealizedPnL', None)
        realized_pnl = getattr(pnl_single_obj, 'realizedPnL', None)
        position = getattr(pnl_single_obj, 'pos', None)
        value = getattr(pnl_single_obj, 'value', None)
        
        # Also try alternative attribute names
        if daily_pnl is None:
            daily_pnl = getattr(pnl_single_obj, 'daily', None)
        if unrealized_pnl is None:
            unrealized_pnl = getattr(pnl_single_obj, 'unrealized', None)
        if realized_pnl is None:
            realized_pnl = getattr(pnl_single_obj, 'realized', None)
        
        # Cancel the PnL subscription
        ib.cancelPnLSingle(account_id, "", con_id)
        
        # Validate and convert values, filtering out invalid/corrupted data
        result = {}
        
        if daily_pnl is not None and is_valid_pnl_value(daily_pnl):
            result["daily_pnl"] = float(daily_pnl)
        elif daily_pnl is not None:
            print(f"Warning: Invalid daily_pnl value received: {daily_pnl} (skipping)")
        
        if unrealized_pnl is not None and is_valid_pnl_value(unrealized_pnl):
            result["unrealized_pnl"] = float(unrealized_pnl)
        elif unrealized_pnl is not None:
            print(f"Warning: Invalid unrealized_pnl value received: {unrealized_pnl} (skipping)")
        
        if realized_pnl is not None and is_valid_pnl_value(realized_pnl):
            result["realized_pnl"] = float(realized_pnl)
        elif realized_pnl is not None:
            print(f"Warning: Invalid realized_pnl value received: {realized_pnl} (skipping)")
        
        # Position and value don't need the same strict validation, but check for reasonable values
        if position is not None:
            try:
                pos_val = float(position)
                if not (math.isnan(pos_val) or math.isinf(pos_val)):
                    result["position"] = pos_val
            except (ValueError, TypeError, OverflowError):
                pass
        
        if value is not None:
            try:
                val = float(value)
                if not (math.isnan(val) or math.isinf(val)) and abs(val) <= MAX_REASONABLE_PNL * 10:
                    result["value"] = val
            except (ValueError, TypeError, OverflowError):
                pass
        
        return result if result else None
            
    except Exception as e:
        print(f"Error in reqPnLSingle: {e}")
        import traceback
        traceback.print_exc()
        if pnl_single_obj and account_id and con_id:
            try:
                ib.cancelPnLSingle(account_id, "", con_id)
            except Exception: #Replace the bare except: with except Exception: to catch regular errors and let system exceptions through.
                pass
        return None


def get_positions_pnl_by_order_ref(ib: IB, order_ref: str, timeout: float = 3.0) -> Dict[str, Dict[str, float]]:
    """
    Get PnL for positions that were opened by orders with a specific orderRef.
    
    Note: This function matches positions to orders by checking if there are
    today's fills/executions with the specified orderRef for each position's symbol.
    Only today's executions are considered to avoid confusion with historical SMB trades.
    This is an approximation since positions don't directly store order references.
    
    Args:
        ib: IB connection instance
        order_ref: Order reference to filter by (e.g., "jobot", "SMB")
        timeout: Timeout per position in seconds
    
    Returns:
        Dictionary mapping contract symbols to their PnL data (only for positions
        that have matching order references in today's trades)
    """
    # Get all positions first
    all_positions_pnl = get_all_positions_pnl(ib, timeout)
    
    if not order_ref:
        return all_positions_pnl
    
    # Get today's fills/executions only (not historical)
    # This ensures we only match positions to today's jobot trades, not old SMB trades
    try:
        # Get all today's fills filtered by order_ref
        today_fills = get_todays_fills(ib, order_ref=order_ref)
        
        # Extract symbols from today's fills with matching orderRef
        matching_symbols = set()
        for trade in today_fills:
            contract = getattr(trade, 'contract', None)
            if contract:
                symbol = getattr(contract, 'symbol', None)
                if symbol:
                    matching_symbols.add(symbol)
        
        # Return only PnL for positions with matching symbols
        filtered_pnl = {symbol: pnl_data for symbol, pnl_data in all_positions_pnl.items() 
                       if symbol in matching_symbols}
        
        return filtered_pnl
    except Exception:
        # If filtering fails, return all positions (fallback)
        return all_positions_pnl


def get_all_positions_pnl(ib: IB, timeout: float = 3.0) -> Dict[str, Dict[str, float]]:
    """
    Get PnL for all current positions using reqPnLSingle.
    
    Args:
        ib: IB connection instance
        timeout: Timeout per position in seconds
    
    Returns:
        Dictionary mapping contract symbols to their PnL data
    """
    positions = ib.positions()
    if not positions:
        return {}
    
    # Get account ID once
    account_id = None
    try:
        account_summary = ib.accountSummary()
        if account_summary:
            account_id = account_summary[0].account
    except Exception:
        pass
    
    results = {}
    for pos in positions:
        contract = pos.contract
        symbol = contract.symbol if hasattr(contract, 'symbol') else str(contract)
        
        pnl_data = get_pnl_single_via_reqPnLSingle(ib, contract, account_id, timeout)
        if pnl_data:
            results[symbol] = pnl_data
    
    return results


def get_daily_pnl(ib: IB, currency: str = ACCOUNT_CURRENCY) -> Dict[str, Optional[float]]:
    """
    Get daily PnL information from account values.
    Tries multiple possible tag names that IB might use.
    
    Args:
        ib: IB connection instance
        currency: Currency filter (default: ACCOUNT_CURRENCY)
    
    Returns:
        Dictionary with keys: 'daily_pnl', 'unrealized_pnl', 'realized_pnl'
    """
    # Try multiple possible tag names for daily PnL
    # IB uses different tags depending on account type and configuration
    daily_pnl = None
    for tag in ["DailyPnL", "DayPnL", "PnL", "PnLDaily", "TodayPnL"]:
        daily_pnl = get_account_value(ib, tag, currency)
        if daily_pnl is not None:
            break
    
    # Try multiple possible tag names for unrealized PnL
    unrealized_pnl = None
    for tag in ["UnrealizedPnL", "UnrealPnL", "OpenPnL"]:
        unrealized_pnl = get_account_value(ib, tag, currency)
        if unrealized_pnl is not None:
            break
    
    # Try multiple possible tag names for realized PnL
    realized_pnl = None
    for tag in ["RealizedPnL", "RealPnL", "ClosedPnL", "RealizedPnLDaily"]:
        realized_pnl = get_account_value(ib, tag, currency)
        if realized_pnl is not None:
            break
    
    return {
        "daily_pnl": daily_pnl,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": realized_pnl
    }


def filter_trades_by_order_ref(trades: list, order_ref: str) -> list:
    """
    Filter trades by order reference (orderRef).
    
    Args:
        trades: List of Trade objects
        order_ref: Order reference to filter by (e.g., "jobot", "SMB")
    
    Returns:
        List of Trade objects matching the order reference
    """
    filtered = []
    for trade in trades:
        order = getattr(trade, 'order', None)
        if order:
            trade_order_ref = getattr(order, 'orderRef', None)
            # Check if orderRef matches (exact match or starts with order_ref)
            if trade_order_ref == order_ref or (isinstance(trade_order_ref, str) and trade_order_ref.startswith(order_ref)):
                filtered.append(trade)
    return filtered


def get_todays_fills(ib: IB, order_ref: Optional[str] = None) -> list:
    """
    Get all fills (executions) from today's trades.
    Uses multiple methods to try to get execution data.
    
    Args:
        ib: IB connection instance
        order_ref: Optional order reference to filter by (e.g., "IBRK", "SMB")
                   If provided, only returns fills from orders with this orderRef
    
    Returns:
        List of Trade objects with executions from today (optionally filtered by order_ref)
    """
    today = date.today()
    fills = []
    
    # Try different methods to get fills
    try:
        # Method 1: Try ib.fills() if available
        if hasattr(ib, 'fills'):
            trades = ib.fills()
        # Method 2: Try getting from openTrades (might include recent fills)
        elif hasattr(ib, 'openTrades'):
            trades = ib.openTrades()
        else:
            # Method 3: Try reqExecutions (this requires callback setup, so we'll skip for now)
            return []
        
        for trade in trades:
            execution = getattr(trade, 'execution', None)
            if execution:
                exec_time = getattr(execution, 'time', None)
                if exec_time:
                    try:
                        # Parse execution time - format varies, try common formats
                        if isinstance(exec_time, str):
                            # Format: "20231215 14:30:00" or "2023-12-15 14:30:00"
                            parts = exec_time.split()
                            if parts:
                                date_str = parts[0]
                                # Try different date formats
                                exec_date = None
                                for fmt in ["%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"]:
                                    try:
                                        exec_date = datetime.strptime(date_str, fmt).date()
                                        break
                                    except ValueError:
                                        continue
                                if exec_date and exec_date == today:
                                    fills.append(trade)
                        elif hasattr(exec_time, 'date'):
                            exec_date = exec_time.date()
                            if exec_date == today:
                                fills.append(trade)
                    except (ValueError, AttributeError, IndexError, TypeError):
                        # If we can't parse the date, skip this fill
                        continue
    except (AttributeError, TypeError):
        # If fills() method doesn't exist or fails, return empty list
        return []
    
    # Filter by order_ref if provided
    if order_ref:
        fills = filter_trades_by_order_ref(fills, order_ref)
    
    return fills


def calculate_realized_pnl_from_fills(ib: IB, order_ref: Optional[str] = None) -> float:
    """
    Calculate realized PnL from today's fills.
    This sums up the commission-adjusted PnL from all executions today.
    
    Args:
        ib: IB connection instance
        order_ref: Optional order reference to filter by (e.g., "IBRK", "SMB")
                   If provided, only calculates PnL from orders with this orderRef
    
    Returns:
        Total realized PnL from today's fills (optionally filtered by order_ref)
    """
    today_fills = get_todays_fills(ib, order_ref=order_ref)
    total_realized = 0.0
    
    for trade in today_fills:
        execution = getattr(trade, 'execution', None)
        if execution:
            # Try to get realizedPnL from the execution
            realized_pnl = getattr(execution, 'realizedPnL', None)
            if realized_pnl is not None:
                try:
                    total_realized += float(realized_pnl)
                except (ValueError, TypeError):
                    pass
    
    return total_realized


def show_daily_pnl_summary(ib: IB, currency: str = ACCOUNT_CURRENCY, order_ref: Optional[str] = None):
    """
    Display a comprehensive summary of daily PnL including:
    - Daily PnL from account values
    - Unrealized PnL from account values and positions
    - Realized PnL from account values and today's fills
    - Number of fills today
    
    Args:
        ib: IB connection instance
        currency: Currency filter (default: ACCOUNT_CURRENCY)
        order_ref: Optional order reference to filter by (e.g., "IBRK", "SMB")
                   If provided, only shows PnL from orders with this orderRef
    """
    print("\n" + "=" * 60)
    print("DAILY P&L SUMMARY")
    print("=" * 60)
    
    # Get PnL from account values
    pnl_data = get_daily_pnl(ib, currency)
    daily_pnl = pnl_data.get("daily_pnl")
    unrealized_pnl = pnl_data.get("unrealized_pnl")
    realized_pnl = pnl_data.get("realized_pnl")
    
    # Get position-level PnL using reqPnLSingle (this is working well)
    positions = ib.positions()
    positions_pnl = {}
    total_position_daily = 0.0
    total_position_unrealized = 0.0
    
    if positions and len(positions) <= 10:  # Limit to avoid timeout
        print("\nGetting position-level PnL via reqPnLSingle...")
        # Use filtered positions if order_ref is provided
        if order_ref:
            positions_pnl = get_positions_pnl_by_order_ref(ib, order_ref, timeout=2.0)
        else:
            positions_pnl = get_all_positions_pnl(ib, timeout=2.0)
        if positions_pnl:
            print("\nPosition-level PnL breakdown:")
            for symbol, pnl_info in positions_pnl.items():
                pos_daily = pnl_info.get("daily_pnl", 0) or 0
                pos_unreal = pnl_info.get("unrealized_pnl", 0) or 0
                
                # Validate values before adding to totals
                if is_valid_pnl_value(pos_daily):
                    total_position_daily += pos_daily
                    print(f"  {symbol:10} - Daily: ${pos_daily:8,.2f}, Unrealized: ${pos_unreal:8,.2f}")
                else:
                    print(f"  {symbol:10} - Daily: INVALID (skipped), Unrealized: ${pos_unreal:8,.2f}")
                
                if is_valid_pnl_value(pos_unreal):
                    total_position_unrealized += pos_unreal
    
    # Use position-level daily PnL sum if account values don't have it
    # But only if the value is valid
    if daily_pnl is None and total_position_daily != 0.0 and is_valid_pnl_value(total_position_daily):
        daily_pnl = total_position_daily
    elif daily_pnl is not None and not is_valid_pnl_value(daily_pnl):
        print(f"\n⚠️  Warning: Daily PnL from account values is invalid ({daily_pnl}), attempting to use position-level data")
        daily_pnl = total_position_daily if is_valid_pnl_value(total_position_daily) else None
    
    # Get today's fills (optionally filtered by order_ref)
    today_fills = get_todays_fills(ib, order_ref=order_ref)
    fills_realized = calculate_realized_pnl_from_fills(ib, order_ref=order_ref)
    
    # Add order_ref context to header if filtering
    if order_ref:
        print(f"\n[Filtered by orderRef: {order_ref}]")
    
    # Display daily PnL (with validation)
    if daily_pnl is not None and is_valid_pnl_value(daily_pnl):
        print(f"\nDaily P&L: ${daily_pnl:,.2f} {currency}")
    elif daily_pnl is not None:
        print(f"\nDaily P&L: INVALID VALUE DETECTED ({daily_pnl}) - Data may be corrupted")
        print("This can happen when markets are closed or IB API returns invalid data.")
    else:
        print("Daily P&L: Not available")
    
    # Display unrealized PnL
    if unrealized_pnl is not None:
        print(f"Unrealized P&L: ${unrealized_pnl:,.2f} {currency}")
    elif total_position_unrealized != 0.0:
        print(f"Unrealized P&L: ${total_position_unrealized:,.2f} {currency} (from positions)")
    else:
        print("Unrealized P&L: Not available")
    
    # Display realized PnL
    if realized_pnl is not None:
        print(f"Realized P&L: ${realized_pnl:,.2f} {currency}")
    else:
        print("Realized P&L: Not available")
    
    if fills_realized != 0.0:
        print(f"Realized P&L (from today's fills): ${fills_realized:,.2f} {currency}")
    
    # Show today's fills count
    print(f"\nToday's fills: {len(today_fills)}")
    if today_fills:
        print("\nToday's Executions:")
        for i, trade in enumerate(today_fills, 1):
            execution = getattr(trade, 'execution', None)
            contract = getattr(trade, 'contract', None)
            if execution and contract:
                shares = getattr(execution, 'shares', 'N/A')
                avg_fill_price = getattr(execution, 'avgFillPrice', None)
                symbol = getattr(contract, 'symbol', 'Unknown')
                
                price_str = f"${avg_fill_price:.2f}" if avg_fill_price else "N/A"
                print(f"  {i}. {symbol}: {shares} shares @ {price_str}")
                
                realized_pnl = getattr(execution, 'realizedPnL', None)
                if realized_pnl is not None:
                    try:
                        print(f"     Realized P&L: ${float(realized_pnl):,.2f}")
                    except (ValueError, TypeError):
                        pass
    
    # Calculate remaining daily stop room (only if daily_pnl is valid)
    if daily_pnl is not None and is_valid_pnl_value(daily_pnl):
        remaining_room = DAILY_STOP - abs(min(0, daily_pnl))  # Only count losses
        if daily_pnl < 0:
            print(f"\n⚠️  Daily loss: ${abs(daily_pnl):,.2f}")
            print(f"Remaining daily stop room: ${remaining_room:,.2f} out of ${DAILY_STOP:,.2f}")
            if abs(daily_pnl) >= DAILY_STOP:
                print("🚨 DAILY STOP LIMIT REACHED!")
        else:
            print(f"\n✅ Daily profit: ${daily_pnl:,.2f}")
    elif daily_pnl is not None:
        print("\n⚠️  Cannot calculate daily stop room - Daily PnL value is invalid") # f string should not be used for single string messages.
    
    print("=" * 60)


def get_reference_price(ib: IB, symbol: str) -> float: # float is a type hint
    """Get a reference price for symbol based on available market data.
    Used for risk calculation in market orders.
    Returns the first valid price from: midpoint, close.
    Raises ValueError if no valid price is found.
    """
    contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
    ticker = ib.reqMktData(contract, '', False, False)
    ib.sleep(0.2)  # give it a moment for data to arrive
    
    # Prioritize midpoint (reflects current bid/ask spread), then close
    price_candidates = [
        ticker.midpoint,
        ticker.close
    ]
    
    for price in price_candidates:
        if price is not None and price > 0:
            return float(price)
    
    raise ValueError(f"No valid market price found for {symbol}")


def calculate_adr(ib: IB, symbol: str, days: int = 20) -> Optional[float]:
    """
    Calculate Average Daily Range (ADR) for a symbol.
    
    ADR is the average of (high - low) over the specified number of days.
    
    Args:
        ib: IB connection instance
        symbol: Stock symbol to calculate ADR for
        days: Number of days to use for calculation (default: 20)
    
    Returns:
        ADR value as float, or None if calculation fails
    """
    try:
        # Create and qualify contract
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        
        # Request historical daily bars
        bars = ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr=f'{days} D',
            barSizeSetting='1 day',
            whatToShow='TRADES',
            useRTH=True,
            formatDate=1
        )
        
        if not bars:
            print(f"Warning: No historical data retrieved for {symbol}")
            return None
        
        # Calculate daily ranges (high - low) for each bar
        daily_ranges = [bar.high - bar.low for bar in bars if bar.high is not None and bar.low is not None]
        
        if not daily_ranges:
            print(f"Warning: No valid range data found for {symbol}")
            return None
        
        # Calculate average daily range
        adr = sum(daily_ranges) / len(daily_ranges)
        
        return float(adr)
        
    except Exception as e:
        print(f"Error calculating ADR for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None


def show_ticker_fields(ticker):
    for name, value in vars(ticker).items(): # name is the attribute of the dictionary vars(object) returns
        if name.startswith ('_'):
            continue
        if value is None:
            continue
        if isinstance(value, float) and value ==0.0:
            continue
        print(f"{name:20}: {value}")
def show_positions(ib: IB):
    positions = ib.positions()
    for pos in positions:
        contract = pos.contract
        print(f"{contract.symbol:10} {contract.secType:10} {pos.position:10} @ {pos.avgCost:10}")
def show_open_orders(ib: IB):
    """Display all open orders."""
    open_orders = ib.reqAllOpenOrders()
    # openTrades() holds Trade objects with contract + order + status
    open_trades = ib.openTrades()
    for trade in open_trades:
        contract = trade.contract
        order = trade.order
        status = trade.orderStatus
        limit_price = order.lmtPrice if hasattr(order, 'lmtPrice') and order.lmtPrice else 0.0
        print(
            f"{contract.symbol:10} {contract.secType:8} "
            f"{order.action:4} {order.totalQuantity:8} "
            f"type={order.orderType:8} "
            f"limit={limit_price:8.2f} "
            f"status={status.status}"
        )


def validate_order_preconditions(ib: IB, symbol: str, num_shares: int, price: Optional[float] = None) -> tuple[bool, str]:
    """
    Validate all preconditions before placing an order.
    
    Returns:
        (is_valid, error_message): Tuple where is_valid is True if all checks pass,
                                   error_message contains reason if validation fails.
    """
    # 1. Check connection
    if not ib.isConnected():
        return False, "IB is not connected"
    
    # 2. Check share quantity
    if not isinstance(num_shares, int) or num_shares <= 0:
        return False, f"Invalid share quantity: {num_shares}. Must be a positive integer."
    
    # 3. Get market price if not provided
    if price is None:
        try:
            price = get_reference_price(ib, symbol)
        except ValueError as e:
            return False, str(e)
    
    # 4. Validate price
    if price is None or price <= 0:
        return False, f"Invalid price: {price}. Price must be positive."
    
    # 5. Check available funds
    available_funds = get_available_funds(ib)
    if available_funds <= 0:
        return False, f"Insufficient available funds: {available_funds}"
    
    # 6. Calculate notional value and check against available funds
    order_notional = price * num_shares
    max_allowed_notional = available_funds * MAX_NOTIONAL_FRACTION
    
    if order_notional > max_allowed_notional:
        return False, (
            f"Order notional ({order_notional:.2f}) exceeds maximum allowed "
            f"({max_allowed_notional:.2f}, {MAX_NOTIONAL_FRACTION*100}% of available funds)"
        )
    
    # 7. Validate contract (implicit check - will fail when creating contract if invalid)
    try:
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
    except Exception as e:
        return False, f"Invalid contract for {symbol}: {str(e)}"
    
    return True, ""


def calculate_num_shares(ib: IB, symbol: str, max_notional_fraction: float = MAX_NOTIONAL_FRACTION) -> int:
    """
    Calculate the maximum number of shares that can be purchased within the risk limit.
    
    Args:
        ib: IB connection instance
        symbol: Stock symbol to trade
        max_notional_fraction: Maximum fraction of available funds to use (default from constant)
    
    Returns:
        Maximum number of shares (as integer) that can be purchased, or 0 if calculation fails.
    """
    try:
        available_funds = get_available_funds(ib)
        if available_funds <= 0:
            print(f"Warning: Available funds is {available_funds}")
            return 0
        
        price = get_reference_price(ib, symbol)
        if price <= 0:
            print(f"Warning: Invalid price {price} for {symbol}")
            return 0
        
        max_notional = available_funds * max_notional_fraction
        max_shares = int(max_notional / price)
        
        return max(0, max_shares)  # Ensure non-negative
    
    except Exception as e:
        print(f"Error calculating num_shares: {e}")
        return 0


def prompt_trade_stop_percentage() -> float:
    """
    Prompt user for trade_stop percentage as an integer and convert to decimal.
    
    Returns:
        float: Trade stop percentage as decimal (e.g., 10 -> 0.10)
    """
    while True:
        try:
            trade_stop_int = int(input("Enter TRADE STOP %: "))
            if trade_stop_int <= 0:
                print("Trade stop percentage must be positive. Please try again.")
                continue
            if trade_stop_int > 100:
                print("Trade stop percentage cannot exceed 100%. Please try again.")
                continue
            trade_stop_percent = trade_stop_int / 100.0
            return trade_stop_percent
        except ValueError:
            print("Invalid input. Please enter a valid integer.")
        except KeyboardInterrupt:
            print("\nCancelled by user.")
            raise


def prompt_order_type() -> str:
    """
    Prompt user for order type selection.
    
    Returns:
        str: Order type ("LIMIT", "MARKET", or "STOP")
    """
    while True:
        order_type_input = input("Enter ORDER TYPE (L/Limit, M/Market, S/StopEntry): ").strip().upper()
        
        if order_type_input in ["L", "LIMIT"]:
            return "LIMIT"
        elif order_type_input in ["M", "MARKET"]:
            return "MARKET"
        elif order_type_input in ["S", "STOP", "STOPENTRY"]:
            return "STOP"
        else:
            print("Invalid order type. Please enter 'L' for Limit, 'M' for Market, or 'S' for StopEntry.")


def prompt_entry_price(order_type: str, action: str) -> Optional[float]:
    """
    Prompt user for entry price based on order type.
    
    Args:
        order_type: "LIMIT", "MARKET", or "STOP"
        action: "BUY" or "SELL" (for context in prompts)
    
    Returns:
        float: Entry price, or None for MARKET orders
    """
    if order_type == "MARKET":
        return None  # Market orders don't need entry price
    
    if order_type == "LIMIT":
        prompt_text = "Enter LIMIT PRICE: "
    elif order_type == "STOP":
        prompt_text = "Enter STOP ENTRY PRICE: "
    else:
        prompt_text = "Enter entry price: "
    
    while True:
        try:
            price = float(input(prompt_text))
            if price <= 0:
                print("Price must be positive. Please try again.")
                continue
            return price
        except ValueError:
            print("Invalid input. Please enter a valid number.")
        except KeyboardInterrupt:
            print("\nCancelled by user.")
            raise


def prompt_stop_loss_price() -> float:
    """
    Prompt user for stop loss price.
    
    Returns:
        float: Stop loss price
    """
    while True:
        try:
            stop_price = float(input("Enter stop loss price: "))
            if stop_price <= 0:
                print("Stop loss price must be positive. Please try again.")
                continue
            return stop_price
        except ValueError:
            print("Invalid input. Please enter a valid number.")
        except KeyboardInterrupt:
            print("\nCancelled by user.")
            raise


def validate_stop_loss_direction(action: str, entry_price: Optional[float], stop_loss_price: float) -> tuple[bool, str]:
    """
    Validate that stop loss price is in the correct direction relative to entry price.
    
    Args:
        action: "BUY" or "SELL"
        entry_price: Entry price (limit price or stop entry price, None for market orders)
        stop_loss_price: Stop loss price
    
    Returns:
        (is_valid, error_message): Tuple where is_valid is True if direction is correct
    """
    action_upper = action.upper().strip()
    
    # For market orders, we can't validate without a reference price, so skip validation
    if entry_price is None:
        return True, ""  # Market orders - validation happens elsewhere with reference price
    
    if action_upper in ["B", "BUY"]:
        if stop_loss_price >= entry_price:
            return False, f"For BUY orders, stop loss price ({stop_loss_price}) must be below entry price ({entry_price})"
    elif action_upper in ["S", "SELL"]:
        if stop_loss_price <= entry_price:
            return False, f"For SELL orders, stop loss price ({stop_loss_price}) must be above entry price ({entry_price})"
    else:
        return False, f"Invalid action: '{action}'. Must be 'b'/'buy' or 's'/'sell'"
    
    return True, ""


def calculate_num_shares_from_risk(
    trade_stop_amount: float,
    entry_price: Optional[float],
    stop_loss_price: float,
    action: str,
    available_funds: float,
    max_notional_fraction: float = MAX_NOTIONAL_FRACTION
) -> int:
    """
    Calculate number of shares based on risk management parameters.
    Considers both risk-based sizing and available funds constraints.
    
    Args:
        trade_stop_amount: Maximum dollar amount to risk on this trade
        entry_price: Entry price (limit price, stop entry price, or None for market orders)
        stop_loss_price: Stop loss price
        action: "BUY" or "SELL"
        available_funds: Available funds in account
        max_notional_fraction: Maximum fraction of available funds to use
    
    Returns:
        int: Number of shares (floored to lower whole number), or 0 if calculation not possible
    """
    # For market orders, entry_price should be provided (reference price)
    if entry_price is None:
        return 0
    
    # Calculate risk per share based on action
    action_upper = action.upper().strip()
    if action_upper in ["B", "BUY"]:
        risk_per_share = entry_price - stop_loss_price
    elif action_upper in ["S", "SELL"]:
        risk_per_share = stop_loss_price - entry_price
    else:
        raise ValueError(f"Invalid action: '{action}'. Must be 'b'/'buy' or 's'/'sell'")
    
    # Calculate shares based on risk
    if risk_per_share <= 0:
        return 0
    
    shares_from_risk = math.floor(trade_stop_amount / risk_per_share)
    
    # Calculate shares based on available funds constraint
    max_notional = available_funds * max_notional_fraction
    shares_from_funds = math.floor(max_notional / entry_price)
    
    # Take the minimum of both constraints
    num_shares = min(shares_from_risk, shares_from_funds)
    
    # Ensure non-negative
    return max(0, num_shares)


def normalize_action(action: str) -> str:
    """
    Normalize action string to IB API format.
    Accepts: "b", "B", "buy", "BUY" -> "BUY"
             "s", "S", "sell", "SELL" -> "SELL"
    
    Returns:
        "BUY" or "SELL" in uppercase
    Raises:
        ValueError if action is invalid
    """
    if not action:
        raise ValueError("Action cannot be empty")
    
    action_upper = action.upper().strip()
    
    # Handle single letter inputs "b" or "s"
    if action_upper == "B":
        return "BUY"
    elif action_upper == "S":
        return "SELL"
    # Handle full word inputs "buy" or "sell"
    elif action_upper == "BUY":
        return "BUY"
    elif action_upper == "SELL":
        return "SELL"
    else:
        raise ValueError(f"Invalid action: '{action}'. Must be 'b'/'buy' or 's'/'sell'")


def get_position_size(ib: IB, ticker: str) -> int:
    """
    Get current position size for a ticker.
    
    Returns:
        int: Positive number for long positions, negative for short positions, 0 if no position
    """
    positions = ib.positions()
    for pos in positions:
        if pos.contract.symbol == ticker and pos.contract.secType == "STK":
            return int(pos.position)
    return 0


def place_bracket_order(
    ib: IB,
    ticker: str,
    action: str,
    order_type: str,
    entry_price: Optional[float],
    stop_loss_price: float,
    trade_stop_percent: float,
    adr: Optional[float] = None
) -> bool:
    """
    Place a bracket order (entry order + stop loss + take profit) with risk-based position sizing.
    
    Args:
        ib: IB connection instance
        ticker: Stock symbol to trade
        action: "b"/"buy"/"BUY" or "s"/"sell"/"SELL"
        order_type: "LIMIT", "MARKET", or "STOP" (for entry order)
        entry_price: Entry price (limit price, stop entry price, or None for market orders)
        stop_loss_price: Stop loss price
        trade_stop_percent: Trade stop as decimal (e.g., 0.10 for 10%)
        adr: Average Daily Range for take profit calculation (optional)
    
    Returns:
        True if bracket order was placed successfully, False otherwise.
    """
    # Normalize action to IB API format
    try:
        ib_action = normalize_action(action)
    except ValueError as e:
        print(f"Error: {e}")
        return False
    
    # Check for existing position before placing bracket order
    # IB may reject bracket orders if there's already a position due to order presets
    current_position = get_position_size(ib, ticker)
    if current_position != 0:
        position_type = "long" if current_position > 0 else "short"
        print(f"⚠️  Warning: Existing {position_type} position detected for {ticker}: {current_position} shares")
        print("   IB may reject bracket orders when there's an existing position due to order presets.")
        print("   Consider exiting the existing position first, or adjust IB order presets.")
        # Don't block the order, but warn the user
    
    # Validate stop loss direction
    is_valid, error_msg = validate_stop_loss_direction(action, entry_price, stop_loss_price)
    if not is_valid:
        print(f"Stop loss validation failed: {error_msg}")
        return False
    
    # For market orders, entry_price should have been set to reference price in execute_risk_based_order
    if entry_price is None:
        print("Error: Entry price is required for risk calculation")
        return False
    
    # Calculate trade stop amount
    trade_stop_amount = DAILY_STOP * trade_stop_percent
    print(f"Trade stop amount: ${trade_stop_amount:.2f} ({trade_stop_percent*100:.0f}% of ${DAILY_STOP} daily stop)")
    
    # Get available funds
    available_funds = get_available_funds(ib)
    if available_funds <= 0:
        print(f"Error: Insufficient available funds: ${available_funds:.2f}")
        return False
    
    # Calculate number of shares based on risk (use original action for calculation which uses "b"/"s")
    num_shares = calculate_num_shares_from_risk(
        trade_stop_amount=trade_stop_amount,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        action=action,
        available_funds=available_funds
    )
    
    if num_shares == 0:
        print("Error: Calculated share quantity is zero. Cannot place order.")
        return False
    
    print(f"Calculated position size: {num_shares} shares")
    risk_per_share = abs(entry_price - stop_loss_price)
    total_risk = num_shares * risk_per_share
    order_notional = num_shares * entry_price
    print(f"  - Risk per share: ${risk_per_share:.2f}")
    print(f"  - Total risk if stop hit: ${total_risk:.2f}")
    print(f"  - Order notional: ${order_notional:.2f}")
    
    try:
        # Create and qualify contract
        contract = Stock(ticker, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        
        # Determine stop action (opposite of entry action)
        if ib_action == "BUY":
            stop_action = "SELL"
            take_profit_action = "SELL"
        else:
            stop_action = "BUY"
            take_profit_action = "BUY"
        
        # Calculate take profit price based on ADR and action
        # If ADR is not available, use 2% as default
        # Round to 2 decimal places (IB requires stock prices to be rounded to 2 decimals)
        take_profit_price = None
        if adr is not None and adr > 0:
            if ib_action == "BUY":
                take_profit_price = entry_price + adr
            else:  # SELL
                take_profit_price = entry_price - adr
        else:
            # Default to 2% take profit if ADR is not available
            if ib_action == "BUY":
                take_profit_price = entry_price * 1.02
            else:  # SELL
                take_profit_price = entry_price * 0.98
        
        # Round to 2 decimal places for stock prices
        if take_profit_price is not None:
            take_profit_price = round(take_profit_price, 2)
        
        # Create parent order (entry order) based on order_type - don't transmit yet
        if order_type == "LIMIT":
            parent_order = LimitOrder(ib_action, num_shares, entry_price)
            order_type_display = "LIMIT"
            price_display = f"${entry_price:.2f}"
        elif order_type == "MARKET":
            parent_order = MarketOrder(ib_action, num_shares)
            order_type_display = "MARKET"
            price_display = "MARKET"
        elif order_type == "STOP":
            parent_order = StopOrder(ib_action, num_shares, entry_price)
            order_type_display = "STOP-ENTRY"
            price_display = f"${entry_price:.2f}"
        else:
            print(f"Error: Unsupported order type: {order_type}")
            return False
        
        # Set TIF to GTC (Good Till Cancel) so orders persist when market is closed
        parent_order.tif = "GTC"
        
        parent_order.transmit = False
        parent_order.orderRef = ORDER_TAG  # Tag parent order
        
        # Note: Error 10349 can occur when IB Gateway/TWS order presets override TIF (Time In Force)
        # We explicitly set TIF to "GTC" (Good Till Cancel) to prevent this and allow orders
        # to persist when the market is closed (e.g., weekends, after hours)
        
        # Place parent order first to get its orderId
        print(f"\nPlacing bracket order:")
        print(f"  Parent: {ib_action} {num_shares} shares @ {price_display} {order_type_display}")
        print(f"  Take Profit: {take_profit_action} {num_shares} shares @ ${take_profit_price:.2f} LIMIT")
        print(f"  Stop Loss: {stop_action} {num_shares} shares @ ${stop_loss_price:.2f} STOP")
        
        parent_trade = ib.placeOrder(contract, parent_order)
        
        # Wait a moment for parent order to be assigned an orderId
        ib.sleep(0.2)
        
        # Get the parent order ID (assigned after placing)
        # Check both the order object and the trade object
        parent_order_id = parent_order.orderId
        if parent_order_id is None and hasattr(parent_trade, 'order'):
            parent_order_id = parent_trade.order.orderId
        
        if parent_order_id is None:
            print("Error: Could not obtain parent order ID. Retrying...")
            # Wait a bit longer and try again
            ib.sleep(0.2)
            parent_order_id = parent_order.orderId
            if parent_order_id is None and hasattr(parent_trade, 'order'):
                parent_order_id = parent_trade.order.orderId
        
        if parent_order_id is None:
            print("Error: Could not obtain parent order ID after retry")
            return False
        
        print(f"Parent order ID obtained: {parent_order_id}")
        
        # Create take profit order (child order) linked to parent
        # Take profit is always created (using ADR or 2% default)
        take_profit_order = LimitOrder(take_profit_action, num_shares, take_profit_price)
        take_profit_order.tif = "GTC"  # Set TIF to GTC so orders persist when market is closed
        take_profit_order.parentId = parent_order_id
        take_profit_order.transmit = False  # Take profit child: transmit = False
        take_profit_order.orderRef = ORDER_TAG  # Tag take profit order
        
        # Create stop loss order (child order) linked to parent
        stop_order = StopOrder(stop_action, num_shares, stop_loss_price)
        stop_order.tif = "GTC"  # Set TIF to GTC so orders persist when market is closed
        stop_order.parentId = parent_order_id
        stop_order.transmit = True  # Stop loss child: transmit = True (sends the whole bracket)
        stop_order.orderRef = ORDER_TAG  # Tag stop loss order
        
        # Place orders in the correct order: take profit first, then stop loss last
        # The stop loss with transmit=True will transmit the entire bracket
        take_profit_trade = ib.placeOrder(contract, take_profit_order)
        stop_trade = ib.placeOrder(contract, stop_order)
        
        # Wait for order acknowledgment
        ib.sleep(0.2)
        
        # Check order statuses
        parent_status = "Unknown"
        if hasattr(parent_trade, 'orderStatus'):
            parent_status = parent_trade.orderStatus.status
        
        stop_status = "Unknown"
        if hasattr(stop_trade, 'orderStatus'):
            stop_status = stop_trade.orderStatus.status
        
        take_profit_status = "N/A"
        if take_profit_trade is not None and hasattr(take_profit_trade, 'orderStatus'):
            take_profit_status = take_profit_trade.orderStatus.status
        
        print(f"\nOrder Status:")
        print(f"  Parent Order ID: {parent_order_id}, Status: {parent_status}")
        stop_order_id = stop_order.orderId if hasattr(stop_order, 'orderId') and stop_order.orderId else "Pending"
        print(f"  Stop Order ID: {stop_order_id}, Status: {stop_status}")
        if take_profit_order is not None:
            take_profit_order_id = take_profit_order.orderId if hasattr(take_profit_order, 'orderId') and take_profit_order.orderId else "Pending"
            print(f"  Take Profit Order ID: {take_profit_order_id}, Status: {take_profit_status}")
        
        if parent_status in ["Submitted", "PreSubmitted", "PendingSubmit", "ApiPending"]:
            print("\nBracket order successfully submitted!")
            return True
        else:
            print(f"Warning: Parent order status is {parent_status}")
            # Still return True if stop order was placed, as the bracket might still work
            if stop_status in ["Submitted", "PreSubmitted", "PendingSubmit", "ApiPending"]:
                print("Stop order was placed successfully. Bracket may be active.")
                return True
            return False
            
    except Exception as e:
        print(f"Error placing bracket order: {e}")
        import traceback
        traceback.print_exc()
        return False


def execute_risk_based_order(ib: IB) -> bool:
    """
    Interactive workflow to place a risk-based bracket order.
    Prompts user for all required inputs and executes the order.
    
    Returns:
        True if order was placed successfully, False otherwise.
    """
    print("\n" + "=" * 60)
    print("RISK-BASED BRACKET ORDER EXECUTION")
    print("=" * 60)
    
    # Prompt for ticker
    ticker = input("\nEnter TICKER SYMBOL: ").strip().upper()
    if not ticker:
        print("Error: Ticker symbol cannot be empty")
        return False
    
    # Calculate ADR for take profit calculation
    adr = calculate_adr(ib, ticker, days=20)
    if adr is None:
        print(f"Warning: Could not calculate ADR for {ticker}. Take profit will not be set.")
    
    # Prompt for action (b/buy/BUY or s/sell/SELL)
    # Keep original input format for place_bracket_order to handle normalization internally
    while True:
        action = input("Enter ACTION (b/buy or s/sell): ").strip()
        try:
            # Validate that it's a valid action (but keep original for passing to place_bracket_order)
            ib_action = normalize_action(action)  # Get normalized for prompts
            break
        except ValueError as e:
            print(f"Invalid action. {e}")
    
    # Prompt for order type (L/M/S for Limit/Market/StopEntry)
    try:
        order_type = prompt_order_type()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        return False
    
    # Prompt for trade stop percentage
    try:
        trade_stop_percent = prompt_trade_stop_percentage()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        return False
    
    # Prompt for entry price based on order type
    try:
        entry_price = prompt_entry_price(order_type, ib_action)
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        return False
    
    # For market orders, we need to get a reference price for risk calculation
    if order_type == "MARKET" and entry_price is None:
        try:
            print("Getting current market price for risk calculation...")
            reference_price = get_reference_price(ib, ticker)
            entry_price = reference_price  # Use reference price for risk calculation
            print(f"Using current market price: ${reference_price:.2f} for risk calculation")
        except ValueError as e:
            print(f"Error: Could not get market price for risk calculation: {e}")
            return False
    
    # Prompt for stop loss price
    try:
        stop_loss_price = prompt_stop_loss_price()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        return False
    
    # Place the bracket order (will normalize action internally)
    return place_bracket_order(
        ib=ib,
        ticker=ticker,
        action=action,  # Pass original format, let place_bracket_order normalize
        order_type=order_type,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        trade_stop_percent=trade_stop_percent,
        adr=adr
    )


def main():
    ib = IB()
    try:
        # IMPORTANT: Set readonly=False when you want to actually place orders
        ib.connect(IB_HOST, 7497, clientId=1, readonly=False)

        print("Connected:", ib.isConnected())
        
        # Show available funds
        account_info(ib)
        
        # Show daily PnL summary (all trades)
        show_daily_pnl_summary(ib)
        
        # Show daily PnL summary filtered by this bot's order reference
        # This separates PnL from this bot (jobot) from the SMB bot
        print("\n" + "=" * 60)
        print(f"DAILY P&L SUMMARY (Filtered by orderRef: {ORDER_TAG})")
        print("=" * 60)
        show_daily_pnl_summary(ib, order_ref=ORDER_TAG)

        # Show existing positions
        print("\nCurrent Positions:")
        show_positions(ib)
        
        # Show open orders
        print("\nOpen Orders:")
        show_open_orders(ib)
        
        # Optional: Show market data for existing positions
        # COMMENTED OUT: Market data not needed since P&L is obtained via reqPnLSingle
        # This section can be uncommented if market data for positions is needed in the future
        # symbols = {contract.symbol for contract in (pos.contract for pos in ib.positions())}
        # if symbols:
        #     contracts = [Stock(sym, 'SMART', 'USD') for sym in symbols]
        #     tickers = [ib.reqMktData(c, '', False, False) for c in contracts]
        #     ib.sleep(2)  # give it a moment for data to arrive
        #     print("\nMarket Data for Current Positions:")
        #     for sym, ticker in zip(symbols, tickers):
        #         print(
        #             f"{sym}: last= {ticker.last}, bid = {ticker.bid}, ask = {ticker.ask}, volume = {ticker.volume}"
        #         )
        
        # ============================================================
        # Risk-based bracket order execution
        # ============================================================
        execute_risk_based_order(ib)
        
    finally:
        if ib.isConnected():
            ib.disconnect()

if __name__ == "__main__":
    main()


