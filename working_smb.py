#%%
from smb_screener import load_snapshot, annotate_with_changes
import copy
import json
import csv
import argparse
import asyncio
from typing import Optional
from datetime import datetime

from smb_screener import (
    get_session, fetch_positions, normalize_record, summarize_group,
    load_snapshot, make_position_key, make_position_key_no_side,
    get_position_size, has_open_orders, TRADER_ENABLED
)
from collections import defaultdict

# Import IB classes - needed for both notebook and terminal
from ib_insync import IB
# Load the previous snapshot (GOOGL should have magnitude 0)
previous_snapshot = load_snapshot()

# Find GOOGL in previous snapshot to verify it has magnitude 0
googl_prev = [r for r in previous_snapshot if r["trader"] == "Justin Spero" and r['symbol'] == 'GOOGL']
print("GOOGL in previous snapshot:", googl_prev[0] if googl_prev else "Not found")

#%%
# Create current_rows by copying previous_snapshot and removing annotation fields
# (annotate_with_changes expects summary_rows format without prev_magnitude, delta_magnitude, change_type)
current_rows = []
for row in previous_snapshot:
    # Create a copy without the annotation fields
    clean_row = {k: v for k, v in row.items() 
                 if k not in ["prev_magnitude", "delta_magnitude", "change_type"]}
    current_rows.append(clean_row)
    print(clean_row)
#%%
from collections import defaultdict

# Group positions by (trader, underlying) to combine equity + all its options
position_groups = defaultdict(list)
for row in current_rows:
    if row.get('trader') == "Kenneth Sharkness":
        # Group by (trader, underlying) only - this combines equity + all options for that underlying
        key = (row.get('trader'), row.get('underlying'))
        position_groups[key].append(row)

# Calculate net side for each group (combining equity + options)
for (trader, underlying), positions in position_groups.items():
    # Convert each position to signed magnitude
    signed_magnitudes = []
    for pos in positions:
        side = pos.get("net_side")
        mag = pos.get("total_magnitude", 0)
        option_type = pos.get("option_type")

        if option_type == "C":
            signed_mag = +mag  # Positive
        elif option_type == "P":
            signed_mag = -mag  # Negative
        else:  # flat or None
            signed_mag = 0
        
        signed_magnitudes.append(signed_mag)
        
        # Convert side to signed magnitude
        if side == "long":
            signed_mag = +mag  # Positive
        elif side == "short":
            signed_mag = -mag  # Negative
        else:  # flat or None
            signed_mag = 0
        
        signed_magnitudes.append(signed_mag)
    
    # Sum all signed magnitudes to get net
    net_magnitude = sum(signed_magnitudes)
    
    # Determine net side from the sum
    if net_magnitude > 0:
        net_side = "long"
    elif net_magnitude < 0:
        net_side = "short"
    else:
        net_side = "flat"
    
    # Print results
    print(f"\n{trader} / {underlying}: Net = {net_side} {abs(net_magnitude)}")
    for pos in positions:
        print(f"  {pos.get('instrument_type')}: {pos.get('symbol')} - {pos.get('net_side')} {pos.get('total_magnitude')}")
#%%
option_underlyings = set()
for row in current_rows:
    if row.get("instrument_type") == "option" and row.get("trader") == "Kenneth Sharkness":
        # Add the underlying symbol to the set
        option_underlyings.add(row.get('underlying'))
print(option_underlyings)
options_with_equity_positions = []
for row in current_rows:
    if row.get("instrument_type") == "equity" and row.get("trader") == "Kenneth Sharkness":
        if row.get('symbol') in option_underlyings:
            options_with_equity_positions.append(row.get('symbol'))
print(options_with_equity_positions)
#%%

# Call annotate_with_changes
annotated_rows = annotate_with_changes(current_rows, previous_snapshot)

# Find GOOGL in the result (for Justin Spero)
googl_result = [r for r in annotated_rows if r.get("symbol") == "GOOGL" and r.get("trader") == "Justin Spero"]
print("\nGOOGL after annotate_with_changes:")
if googl_result:
    googl = googl_result[0]
    print(f"  total_magnitude: {googl.get('total_magnitude')}")
    print(f"  prev_magnitude: {googl.get('prev_magnitude')}")
    print(f"  delta_magnitude: {googl.get('delta_magnitude')}")
    print(f"  change_type: {googl.get('change_type')}")
    print(f"  net_side: {googl.get('net_side')}")
else:
    print("GOOGL not found in results for Justin Spero")

#%%
# Test IB execution for GOOGL change
from smb_screener import get_ib_connection, process_execution_change

# Get IB connection
ib = get_ib_connection()
if ib is None:
    print("Warning: IB connection unavailable - market data will not be retrieved")

# Process execution changes (NEW, ADD, TRIM, CLOSE, FLIP)
change_types_to_process = ["NEW", "ADD", "TRIM", "CLOSE", "FLIP"]
for row in annotated_rows:
    change_type = row.get("change_type")
    if change_type in change_types_to_process:
        process_execution_change(ib, row, change_type)
#%%
# Trailing Stop Calculator - Use existing IB connection (avoids event loop issues)
# # Set up event loop BEFORE importing ib_insync (required for both terminal and IW)
# import asyncio
# try:
#     # Check if loop is already running (IW/Jupyter)
#     asyncio.get_running_loop()
#     # Loop exists - use util.startLoop() to integrate
#     from ib_insync import util
#     util.startLoop()
# except RuntimeError:
#     # No loop running (terminal) - create one
#     asyncio.set_event_loop(asyncio.new_event_loop())

# # Now safe to import ib_insync
# from ib_insync import Stock
# from smb_screener import get_ib_connection
# from typing import Optional

# ACCOUNT_CURRENCY = "USD"

# def calculate_trailing_stop(ib, symbol: str = "IBIT", prior_bars: int = 3, position_side: str = "long") -> Optional[float]:
#     """
#     Calculate trailing stop price based on last N bars (15-minute bars).
#     For long: minimum (lowest low) from the prior bars.
#     For short: maximum (highest high) from the prior bars.
#     If market closed, uses last available close price.
#     """
#     try:
#         contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
#         ib.qualifyContracts(contract)
        
#         # Calculate duration: prior_bars * 15 minutes per bar * 60 seconds per minute
#         # Add a small buffer to ensure we get enough bars
#         duration_seconds = (prior_bars * 15 * 60) + (15 * 60)  # Request one extra bar to be safe
#         bars = ib.reqHistoricalData(
#             contract, endDateTime='', durationStr=f'{duration_seconds} S',
#             barSizeSetting='15 mins', whatToShow='TRADES', useRTH=True, formatDate=1
#         )
        
#         if not bars:
#             # Market closed - try to get last close price
#             try:
#                 ticker = ib.reqMktData(contract, '', False, False)
#                 ib.sleep(0.5)
#                 close_price = ticker.close if hasattr(ticker, 'close') and ticker.close else None
#                 if close_price:
#                     return float(close_price)
#             except:
#                 pass
#             return None
        
#         # Take only the last prior_bars bars to ensure we use exactly the requested number
#         bars = bars[-prior_bars:] if len(bars) >= prior_bars else bars
        
#         if position_side.lower() == "long":
#             # For long: minimum value (lowest low) from the prior bars
#             return float(min(bar.low for bar in bars))
#         else:
#             # For short: maximum value (highest high) from the prior bars
#             return float(max(bar.high for bar in bars))
            
#     except Exception as e:
#         print(f"Error calculating trailing stop for {symbol}: {e}")
#         return None

# # Use existing connection from smb_screener (works because it's already set up correctly)
# ib = get_ib_connection()

# if ib and ib.isConnected():
#     symbol = "IBIT"
#     prior_bars = 3  # Define the variable before using it
#     print(f"Testing {symbol} with existing IB connection (client ID 2):")
    
#     long_stop = calculate_trailing_stop(ib, symbol, prior_bars, "long")
#     short_stop = calculate_trailing_stop(ib, symbol, prior_bars, "short")
    
#     if long_stop:
#         print(f"  Long stop: ${long_stop:.2f}")
#     if short_stop:
#         print(f"  Short stop: ${short_stop:.2f}")
# else:
#     print("No IB connection available - run smb_screener first or check IB connection")
#     print("Note: This uses client ID 2. For client ID 3, you'd need to restart IW and set up before any ib_insync imports.")
#%%