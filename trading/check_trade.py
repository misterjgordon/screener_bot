#!/usr/bin/env python3
"""
Diagnostic script to investigate position issues for any trader and ticker.
Run this in an interactive Python session or as a script.

Usage:
    python trading/check_trade.py --trader "Justin Spero" --ticker AMD
    python trading/check_trade.py -t "Jeff Holden" -s GOOGL
"""
import json
import csv
import argparse
import asyncio
from datetime import datetime

# Default values for interactive use (can be overridden by function arguments)
DEFAULT_TRADER = "Justin Spero"
DEFAULT_TICKER = "SEGG"
# Handle event loop for Jupyter notebooks
_notebook_loop = None
try:
    from IPython import get_ipython
    _in_notebook = get_ipython() is not None
    if _in_notebook:
        # Save the notebook's event loop BEFORE smb_screener imports
        _notebook_loop = asyncio.get_event_loop()
        
        # In notebook: use nest_asyncio to allow nested event loops
        try:
            import nest_asyncio
            nest_asyncio.apply()
            print("Notebook mode: Using nest_asyncio for event loop compatibility")
        except ImportError:
            print("Warning: nest_asyncio not installed.")
            print("   To fix IB connection in notebooks, install: pip install nest_asyncio")
            _in_notebook = False
except (ImportError, NameError):
    _in_notebook = False

# Now import smb_screener (it may create a new event loop)
from smb_screener import (
    fetch_positions,
    get_session,
    load_snapshot,
    make_position_key,
    make_position_key_no_side,
    normalize_record,
    summarize_group,
    TRADER_ENABLED,
)
from trading.trade_data import get_position_size, has_open_orders
from collections import defaultdict

# Import IB classes - needed for both notebook and terminal
from ib_async import IB

# Additional setup for notebook-safe connection
if _in_notebook:
    from ib_async import util
    # Restore notebook's event loop after smb_screener import
    if _notebook_loop is not None:
        try:
            asyncio.set_event_loop(_notebook_loop)
            # Start IB's util loop using notebook's loop - this is critical!
            # It integrates ib_async with the existing event loop
            util.startLoop()
            print("IB util loop started for notebook")
        except Exception as e:
            print(f"Warning: Could not start IB util loop: {e}")

# Store our own IB connection (independent of smb_screener)
_investigation_ib_connection: IB | None = None

# Create a notebook-safe IB connection wrapper
def get_ib_connection():
    """
    Get IB connection that works in both notebook and script environments.
    Creates its own connection independent of smb_screener to avoid event loop conflicts.
    """
    global _investigation_ib_connection
    
    # Return existing connection if valid
    if _investigation_ib_connection is not None:
        try:
            if _investigation_ib_connection.isConnected():
                return _investigation_ib_connection
        except:
            # Connection might be stale
            _investigation_ib_connection = None
    
    # Get connection settings from config (use dedicated ID so we don't conflict with screener)
    from trading.config import ACTIVE_TRADING, IB_HOST, IB_PORT, IB_CLIENT_ID_CHECK_TRADE

    if _in_notebook:
        # In notebook: Jupyter's event loop conflicts with ib_async's async operations
        # Solution: Always use a separate thread with its own event loop
        # This is the most reliable approach for notebooks
        try:
            print(f"Attempting IB connection to {IB_HOST}:{IB_PORT} with client ID {IB_CLIENT_ID_CHECK_TRADE}...")
            print("(Using thread-based connection for notebook compatibility)")
            
            import threading
            connection_done = threading.Event()
            connection_result: IB | None = None
            connection_error: Exception | None = None
            readonly_mode = not ACTIVE_TRADING
            
            def connect_in_thread():
                nonlocal connection_result, connection_error
                try:
                    # Create a completely separate event loop in this thread
                    # This avoids all conflicts with Jupyter's event loop
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    
                    # Create IB instance in this thread with its own loop
                    ib = IB()
                    
                    # Connect using synchronous method (works fine in isolated thread)
                    ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID_CHECK_TRADE, readonly=readonly_mode)
                    
                    if ib.isConnected():
                        connection_result = ib
                        connection_done.set()
                    else:
                        connection_error = RuntimeError("IB connection failed - isConnected() returned False")
                        connection_done.set()
                except Exception as err:
                    connection_error = err
                    connection_done.set()
            
            # Start connection in separate thread
            thread = threading.Thread(target=connect_in_thread, daemon=True)
            thread.start()
            
            # Wait for connection (with timeout)
            if connection_done.wait(timeout=1):
                if connection_error:
                    raise connection_error
                if connection_result and connection_result.isConnected():
                    _investigation_ib_connection = connection_result
                    mode_str = "readonly" if readonly_mode else "trading"
                    print(f"IB connected: {IB_HOST}:{IB_PORT} ({mode_str} mode, client ID {IB_CLIENT_ID_CHECK_TRADE})")
                    return connection_result
                else:
                    print(f"Warning: IB connection failed - isConnected() returned False")
                    return None
            else:
                raise TimeoutError("IB connection timed out after 15 seconds")
                
        except Exception as e:
            print(f"Warning: IB connection error: {e}")
            import traceback
            traceback.print_exc()
            print("\nNote: IB connection in Jupyter notebooks requires:")
            print("   1. TWS or IB Gateway must be running")
            print("   2. API connections must be enabled in TWS/Gateway")
            print("   3. The correct port must be open (7497 for paper trading)")
            return None
    else:
        # Not in notebook: create a simple connection (use dedicated ID so we don't conflict with screener)
        try:
            print(f"Attempting IB connection to {IB_HOST}:{IB_PORT} with client ID {IB_CLIENT_ID_CHECK_TRADE}...")
            ib = IB()
            readonly_mode = not ACTIVE_TRADING
            ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID_CHECK_TRADE, readonly=readonly_mode)

            if ib.isConnected():
                _investigation_ib_connection = ib
                mode_str = "readonly" if readonly_mode else "trading"
                print(f"IB connected: {IB_HOST}:{IB_PORT} ({mode_str} mode, client ID {IB_CLIENT_ID_CHECK_TRADE})")
                return ib
            else:
                print(f"Warning: IB connection failed - isConnected() returned False")
                return None
        except Exception as e:
            print(f"Warning: IB connection error: {e}")
            import traceback
            traceback.print_exc()
            return None



def check_position_in_snapshot(trader: str = DEFAULT_TRADER, ticker: str = DEFAULT_TICKER):
    """Check if ticker appears in the current snapshot and its state."""
    print("=" * 60)
    print(f"1. CHECKING CURRENT SNAPSHOT: {trader} / {ticker}")
    print("=" * 60)
    snapshot = load_snapshot()
    if snapshot:
        positions = [p for p in snapshot if p.get("symbol") == ticker and p.get("trader") == trader]
        if positions:
            print(f"Found {len(positions)} {ticker} position(s) in snapshot for {trader}:")
            for pos in positions:
                print(f"  - Symbol: {pos.get('symbol')}")
                print(f"    Side: {pos.get('net_side')}")
                print(f"    Magnitude: {pos.get('total_magnitude')}")
                print(f"    Prev Magnitude: {pos.get('prev_magnitude')}")
                print(f"    Change Type: {pos.get('change_type')}")
                print(f"    Is Long Term: {pos.get('is_long_term')}")
        else:
            print(f"No {ticker} position found in snapshot for {trader}")
    else:
        print("No snapshot file found")
    print()

def check_position_in_executions(trader: str = DEFAULT_TRADER, ticker: str = DEFAULT_TICKER):
    """Check execution CSV for ticker entries."""
    print("=" * 60)
    print(f"2. CHECKING EXECUTION CSV: {trader} / {ticker}")
    print("=" * 60)
    try:
        from smb_screener import EXECUTIONS_DIR
        from datetime import date
        import os
        
        today = date.today()
        filename = f"executions_{today.strftime('%Y-%m-%d')}.csv"
        filepath = os.path.join(EXECUTIONS_DIR, filename)
        
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                reader = csv.DictReader(f)
                executions = [row for row in reader if row.get('symbol') == ticker and row.get('trader') == trader]
            
            if executions:
                print(f"Found {len(executions)} {ticker} execution(s) in today's CSV for {trader}:")
                for exec_row in executions:
                    print(f"  - Timestamp: {exec_row.get('timestamp')}")
                    print(f"    Change Type: {exec_row.get('change_type')}")
                    print(f"    Net Side: {exec_row.get('net_side')}")
                    print(f"    Delta Magnitude: {exec_row.get('delta_magnitude')}")
                    print(f"    Entry Price: {exec_row.get('entry_price')}")
                    print(f"    Stop Price: {exec_row.get('stop_price')}")
                    print(f"    Take Profit: {exec_row.get('take_profit_price')}")
                    print(f"    Order ID: {exec_row.get('order_id')}")
            else:
                print(f"No {ticker} executions found in today's CSV for {trader}")
        else:
            print(f"Execution CSV file not found: {filepath}")
    except Exception as e:
        print(f"Error reading execution CSV: {e}")
    print()

def check_current_smb_positions(trader: str = DEFAULT_TRADER, ticker: str = DEFAULT_TICKER):
    """Fetch current positions from SMB API and check ticker."""
    print("=" * 60)
    print(f"3. CHECKING CURRENT SMB API POSITIONS: {trader} / {ticker}")
    print("=" * 60)
    try:
        session = get_session()
        positions_data = fetch_positions(session)
        
        # Filter for trader ticker positions
        raw_positions = [r for r in positions_data 
                        if r.get("symbol") == ticker and trader in r.get("account_name", "")]
        
        if raw_positions:
            print(f"Found {len(raw_positions)} {ticker} position(s) in SMB API for {trader}:")
            for pos in raw_positions:
                print(f"  - Account: {pos.get('account_name')}")
                print(f"    Symbol: {pos.get('symbol')}")
                print(f"    Side: {pos.get('side')}")
                print(f"    Magnitude: {pos.get('magnitude')}")
                print(f"    Last Updated: {pos.get('last_updated')}")
        else:
            print(f"No {ticker} positions found in SMB API for {trader} (position may be closed)")
        
        # Also normalize and summarize to see what the bot would see
        normalized = [normalize_record(r) for r in positions_data]
        normalized_positions = [n for n in normalized 
                               if n.get("underlying") == ticker and n.get("trader") == trader]
        
        if normalized_positions:
            print(f"\nNormalized {ticker} positions: {len(normalized_positions)}")
            groups = defaultdict(list)
            for p in normalized_positions:
                key = (p["trader"], p["is_long_term"], p["symbol_raw"])
                groups[key].append(p)
            
            for key, recs in groups.items():
                summary = summarize_group(recs)
                print(f"  - Summary: {summary}")
    except Exception as e:
        print(f"Error fetching SMB positions: {e}")
        import traceback
        traceback.print_exc()
    print()

def check_ib_position(ticker: str = DEFAULT_TICKER):
    """Check actual IB position for ticker."""
    print("=" * 60)
    print(f"4. CHECKING IB POSITION: {ticker}")
    print("=" * 60)
    try:
        ib = get_ib_connection()
        if ib and ib.isConnected():
            position_size = get_position_size(ib, ticker)
            print(f"Current IB position size for {ticker}: {position_size}")
            if position_size != 0:
                print(f"  - Position is {'LONG' if position_size > 0 else 'SHORT'}")
                print(f"  - Size: {abs(position_size)} shares")
            else:
                print(f"  - No position in IB for {ticker} (position is flat)")
            
            # Check for open orders
            has_buy_order = has_open_orders(ib, ticker, is_long=True)
            has_sell_order = has_open_orders(ib, ticker, is_long=False)
            print(f"  - Has open BUY orders: {has_buy_order}")
            print(f"  - Has open SELL orders: {has_sell_order}")
        else:
            print("IB not connected - cannot check position")
    except Exception as e:
        print(f"Error checking IB position: {e}")
        import traceback
        traceback.print_exc()
    print()

def simulate_change_detection(trader: str = DEFAULT_TRADER, ticker: str = DEFAULT_TICKER):
    """Simulate the change detection logic for ticker."""
    print("=" * 60)
    print(f"5. SIMULATING CHANGE DETECTION: {trader} / {ticker}")
    print("=" * 60)
    try:
        # Get current positions
        session = get_session()
        positions_data = fetch_positions(session)
        normalized = [normalize_record(r) for r in positions_data]
        
        # Group by trader, LT, symbol
        groups = defaultdict(list)
        for p in normalized:
            if p.get("underlying") == ticker and p.get("trader") == trader:
                key = (p["trader"], p["is_long_term"], p["symbol_raw"])
                groups[key].append(p)
        
        # Summarize
        current_summaries = [summarize_group(recs) for recs in groups.values()]
        
        # Load previous snapshot
        previous_snapshot = load_snapshot()
        
        if previous_snapshot:
            # Find ticker in previous snapshot
            prev_positions = [p for p in previous_snapshot 
                            if p.get("symbol") == ticker and p.get("trader") == trader]
            
            print(f"Previous snapshot {ticker} positions:")
            for p in prev_positions:
                key = make_position_key(p)
                print(f"  - Key: {key}")
                print(f"    Side: {p.get('net_side')}, Mag: {p.get('total_magnitude')}")
            
            print(f"\nCurrent SMB API {ticker} positions:")
            for s in current_summaries:
                key = make_position_key(s)
                print(f"  - Key: {key}")
                print(f"    Side: {s.get('net_side')}, Mag: {s.get('total_magnitude')}")
            
            # Check if CLOSE should be detected
            for curr in current_summaries:
                curr_key = make_position_key(curr)
                prev_row = None
                for prev in prev_positions:
                    if make_position_key(prev) == curr_key:
                        prev_row = prev
                        break
                
                if prev_row is None and curr.get('total_magnitude') == 0:
                    # Check if there was a previous position (without side)
                    key_no_side = make_position_key_no_side(curr)
                    prev_rows_same_symbol = [p for p in prev_positions 
                                            if make_position_key_no_side(p) == key_no_side]
                    had_non_flat = any(p["net_side"] != "flat" and p["total_magnitude"] > 0 
                                     for p in prev_rows_same_symbol)
                    if had_non_flat:
                        print(f"\nCLOSE should be detected for: {curr.get('symbol')}")
                    else:
                        print(f"\nCLOSE not detected - no previous non-flat position")
                elif prev_row:
                    print(f"\nPosition still exists: {curr.get('symbol')} - {curr.get('net_side')}")
        else:
            print("No previous snapshot to compare")
    except Exception as e:
        print(f"Error simulating change detection: {e}")
        import traceback
        traceback.print_exc()
    print()

def investigate_position(trader: str = DEFAULT_TRADER, ticker: str = DEFAULT_TICKER):
    """
    Main investigation function for a trader and ticker.
    
    Args:
        trader: Trader name (e.g., "Justin Spero")
        ticker: Stock symbol (e.g., "AMD")
    """
    print("\n" + "=" * 60)
    print(f"POSITION INVESTIGATION: {trader} / {ticker}")
    print("=" * 60 + "\n")
    
    check_position_in_snapshot(trader, ticker)
    check_position_in_executions(trader, ticker)
    check_current_smb_positions(trader, ticker)
    check_ib_position(ticker)
    simulate_change_detection(trader, ticker)
    
    print("=" * 60)
    print("INVESTIGATION COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    # Check if running in Jupyter/IPython notebook
    try:
        # Check if we're in IPython/Jupyter by looking for get_ipython
        from IPython import get_ipython
        ipython = get_ipython()
        if ipython is not None:
            # Running in notebook - skip argparse, use defaults
            print("Running in notebook mode. Using default values.")
            print(f"Default: {DEFAULT_TRADER} / {DEFAULT_TICKER}")
            print("To customize, call: investigate_position('Trader Name', 'TICKER')")
            print()
            investigate_position()
        else:
            # IPython exists but not in notebook - use argparse
            raise ImportError("Not in notebook")
    except (ImportError, NameError):
        # Not in notebook - use argparse for command line
        parser = argparse.ArgumentParser(
            description="Investigate position issues for any trader and ticker",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  python trading/check_trade.py --trader "Justin Spero" --ticker AMD
  python trading/check_trade.py -t "Jeff Holden" -s GOOGL
  python trading/check_trade.py  # Uses default values
            """
        )
        parser.add_argument(
            "-t", "--trader",
            default=DEFAULT_TRADER,
            help=f"Trader name (default: {DEFAULT_TRADER})"
        )
        parser.add_argument(
            "-s", "--ticker", "--symbol",
            dest="ticker",
            default=DEFAULT_TICKER,
            help=f"Stock symbol/ticker (default: {DEFAULT_TICKER})"
        )
        
        args = parser.parse_args()
        investigate_position(args.trader, args.ticker)
#%%
from trading.config import ACTIVE_TRADING, IB_CLIENT_ID_CHECK_TRADE, IB_HOST, IB_PORT
from trading.market_data import calculate_adr
from ib_async import IB

# Create connection with unique client ID (avoids conflict with screener which uses 1)
ib = IB()
readonly_mode = not ACTIVE_TRADING
try:
    ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID_CHECK_TRADE, readonly=readonly_mode)
except Exception as e:
    print(f"Connection error: {e}")
    ib = None

ticker = "ACHR"  # Change this to test other tickers

if ib and ib.isConnected():
    print(f"Connected to IB with client ID {IB_CLIENT_ID_CHECK_TRADE}")
    adr = calculate_adr(ib, ticker, days=20)  # 20 days is default
    if adr:
        print(f"{ticker} ADR (20 days): ${adr:.2f}")
    else:
        print(f"Failed to calculate ADR for {ticker}")
else:
    print("IB not connected - make sure TWS/Gateway is running")
#%%

#%%