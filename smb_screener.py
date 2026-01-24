# =========================================================
# Purpose: This script is used to screen for positions from the SMB API and execute trades in IB (interactive brokers papaer account). For educational purposes only. 
# =========================================================
import os
from dotenv import load_dotenv
import requests
import re
from collections import defaultdict
import json
import time
import pickle
from datetime import datetime, date
from typing import Optional, Dict
import csv
# from bs4 import BeautifulSoup dont need this since it's JSON

# IB imports - need event loop setup before importing ib_insync
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB, Stock, LimitOrder, MarketOrder, StopOrder

# =========================================================
# Run configuration
# =========================================================

# "once"  -> run the workflow a single time and exit
# "poll"  -> keep running every INTERVAL_SECONDS
# "off"   -> do nothing (handy when you temporarily disable the script)
#*******************************************
RUN_MODE = "poll"  # poll or once
#*******************************************
# Interval between API calls when in polling mode (in seconds) SMB updates every 10 seconds.
INTERVAL_SECONDS = 10 # 20 seconds

# =========================================================
# Active Trading Configuration
# =========================================================
# Set to True to enable automatic order execution to IB
ACTIVE_TRADING = True  # Set to True to enable trading, allows us to place trades in IB (interactive brokers papaer account)

# Per-trader toggle: set to True to mirror trades for that trader. Kenneth and Steve use options for hedging so turn off as options logic is pending.
TRADER_ENABLED = {
    "Justin Spero": True,
    "Jeff Holden": True,
    "Steve Spencer": False,
    "Kenneth Sharkness": False,
}

# IB Connection settings
IB_HOST = "127.0.0.1"
IB_PORT = 7497  # Use 7497 for TWS paper trading, 7496 for TWS live, 4001 for IB Gateway paper
IB_CLIENT_ID = 2  # Use different client ID from jobot.py

# Risk management constants (from jobot.py)
DAILY_STOP = 400  # USD - maximum daily loss allowed
ACCOUNT_CURRENCY = "USD"

# Order tagging
ORDER_TAG = "SMB"  # Tag to identify orders placed by this bot

# =========================================================
# Environment and constants
# =========================================================
load_dotenv()

SMB_USERNAME = os.getenv("SMB_USERNAME")
SMB_PASSWORD = os.getenv("SMB_PASSWORD")


if not SMB_USERNAME or not SMB_PASSWORD:
    raise ValueError("Missing SMB_USERNAME or SMB_PASSWORD in .env")

# session = requests.Session() # creating an instance (object) of the class requests.Session


# =========================================================
# Auth / HTTP constants
# =========================================================
CSRF_URL = "https://rt.smbtraining.com/api/auth/csrf"
LOGIN_URL = "https://rt.smbtraining.com/api/auth/callback/credentials"
CALLBACK_URL = "https://rt.smbtraining.com/auth/signin?callbackUrl=https%3A%2F%2Frt.smbtraining.com%2Fcalendar"
SESSION_URL = "https://rt.smbtraining.com/api/auth/session"
POSITIONS_URL = "https://rt.smbtraining.com/api/external-positions"
# NEW: module-level constant (a string variable) for where we store the snapshot
SNAPSHOT_FILE = "position_snapshot.json"
COOKIES_FILE = "smb_cookies.pkl"
EXECUTIONS_DIR = "smb_trader_executions"

# Auth / HTTP helpers
def create_authenticated_session():
    """Create a logged-in requests.Session and return it. Only called when no valid session exists from get_session using smb_ookies.pkl"""
    session = requests.Session()  # creating an instance (object) of the class requests.Session

    csrf_resp = session.get(CSRF_URL)
    csrf_data = csrf_resp.json()
    csrf_token = csrf_data.get("csrfToken")
    if not csrf_token:
        raise RuntimeError("No csrfToken in CSRF response")
        # print("Got CSRF token:", csrf_token[:12], "...")
    payload = {
        "email": SMB_USERNAME,       # matches DevTools
        "password": SMB_PASSWORD,    # from .env, request for access
        "redirect": "false",         # string, just like DevTools
        "csrfToken": csrf_token,
        "callbackUrl": CALLBACK_URL, # hardcoded for now
        "json": "true",
    }
    # optional but can help: send the same Origin/Referer as browser
    headers = {
        "Origin": "https://rt.smbtraining.com",
        "Referer": CALLBACK_URL,
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    # login request to SMB API
    login_resp = session.post(LOGIN_URL, data=payload, headers=headers)
    login_resp.raise_for_status()
    # print("Login status code:", login_resp.status_code)
    # print("Login response snippet:", login_resp.text[:300])

    # 3 Check session should now return user info, not {}
    session_resp = session.get(SESSION_URL)
    session_resp.raise_for_status()
    # print("Session status code:", session_resp.status_code)
    # print("Session response snippet:", session_resp.text[:300])
    
    # 4. Fetch positions from JSON endpoint
    positions_resp = session.get(POSITIONS_URL)
    # print("Positions status code:", positions_resp.status_code)

    positions_data = positions_resp.json()
    # print(f"Total raw records: {len(positions_data)}")
    # print("Sample raw record:", positions_data[0])
    return session

def is_session_valid(session):
    resp = session.get(SESSION_URL)
    if not resp.ok:
        print("Session check status:", resp.status_code)
        return False

    data = resp.json()
    # print("Session check payload:", data) # will print the session URL data if session is not valid
    # if logged out, this might be {} instead of user details
    return bool(data)

def fetch_positions(session):
    """Fetch raw positions JSON using an already authenticated session."""
    resp = session.get(POSITIONS_URL)
    resp.raise_for_status()
    return resp.json()

# funcions to save/load cookies
def save_cookies(session, path=COOKIES_FILE):
    """Save the session cookies to a file using pickle.
    session (requests.Session_): the session whose cookies to save via path (str) file path where we write the cookies.
    """
    with open(path, "wb") as f:
        pickle.dump(session.cookies, f) # pickle.dump(obj, file) this serializes the cookies object to bytes and writes it to disk

def load_cookies(session, path=COOKIES_FILE):
    """
    Load cookies from disk into the given session, if the cookie file exists.
    Parameters:
        session (requests.Session): the session we want to attach cookies to.
        path (str): file path where the cookies are stored.
    Returns:
        bool: True if cookies were loaded, False if file did not exist.
    """
    if not os.path.exists(path):
        return False

    with open(path, "rb") as f:
        loaded_cookies = pickle.load(f)

    # session.cookies is a RequestsCookieJar; update merges cookies in
    session.cookies.update(loaded_cookies)
    return True

def get_session():
    """
    Return a requests.Session that is authenticated if possible.
    Logic:
      1 Create a new session.
      2 Try to load cookies from disk into this session.
      3 If cookies loaded, check if the session is still valid.If valid, reuse this session.
      4 If not valid (or no cookie file), perform a fresh login
         and save the new cookies to disk.
    """
    # Step 1: always start with a fresh Session object
    session = requests.Session()

    # Step 2: try loading cookies from file
    cookies_loaded = load_cookies(session)
    if cookies_loaded:
        if is_session_valid(session):
            # print("Reused session from cookies.")
            return session
        else:
            print("Loaded cookies but session is invalid, performing fresh login.")
    else:
        print("No cookies loaded, performing fresh login.")
    # perform fresh login
    fresh_session = create_authenticated_session()
    save_cookies(fresh_session)
    # save cookies from the newly athenticated session
    return fresh_session


# =========================================================
# IB Connection and Market Data
# =========================================================

_ib_connection: Optional[IB] = None  # Module-level IB connection

def get_ib_connection() -> Optional[IB]:
    """
    Get or create a persistent IB connection.
    Establishes connection for market data even if ACTIVE_TRADING is False.
    Returns None if connection fails (allows script to continue without trading).
    """
    global _ib_connection
    
    # Return existing connection if valid
    if _ib_connection is not None and _ib_connection.isConnected():
        return _ib_connection
    
    # Create new connection (always try to connect for market data, even if trading is disabled)
    print(f"Attempting IB connection to {IB_HOST}:{IB_PORT} with client ID {IB_CLIENT_ID}...")
    try:
        ib = IB()
        # Use readonly=True if ACTIVE_TRADING is False, readonly=False if trading is enabled
        readonly_mode = not ACTIVE_TRADING
        ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, readonly=readonly_mode)
        if ib.isConnected():
            _ib_connection = ib
            mode_str = "readonly" if readonly_mode else "trading"
            print(f"✓ IB connected: {IB_HOST}:{IB_PORT} ({mode_str} mode, client ID {IB_CLIENT_ID})")
            return ib
        else:
            print("✗ Warning: IB connection failed - isConnected() returned False")
            return None
    except Exception as e:
        print(f"✗ Warning: IB connection error: {e}")
        import traceback
        traceback.print_exc()
        _ib_connection = None
        return None

def close_ib_connection():
    """Close the IB connection if it exists."""
    global _ib_connection
    if _ib_connection is not None and _ib_connection.isConnected():
        try:
            _ib_connection.disconnect()
            print("IB connection closed")
        except Exception as e:
            print(f"Error closing IB connection: {e}")
    _ib_connection = None

def diagnose_market_price(ib: IB, symbol: str) -> None:
    """
    Diagnostic function to print detailed market price information for debugging.
    This is isolated and doesn't affect other code paths.
    
    Args:
        ib: IB connection instance
        symbol: Stock symbol to diagnose
    """
    if ib is None or not ib.isConnected():
        print(f"❌ DIAGNOSTIC [{symbol}]: IB not connected")
        return
    
    try:
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        ticker = ib.reqMktData(contract, '', False, False)
        ib.sleep(0.5)  # Give more time for data to arrive
        
        # Check all possible price sources
        midpoint = getattr(ticker, 'midpoint', None)
        close = getattr(ticker, 'close', None)
        bid = getattr(ticker, 'bid', None)
        ask = getattr(ticker, 'ask', None)
        last = getattr(ticker, 'last', None)
        
        # Try to get values
        midpoint_val = None
        close_val = None
        bid_val = None
        ask_val = None
        last_val = None
        
        if midpoint is not None:
            try:
                midpoint_val = float(midpoint) if not callable(midpoint) else None
            except (TypeError, ValueError):
                pass
        
        if close is not None:
            try:
                close_val = float(close) if not callable(close) else None
            except (TypeError, ValueError):
                pass
        
        if bid is not None:
            try:
                bid_val = float(bid) if not callable(bid) else None
            except (TypeError, ValueError):
                pass
        
        if ask is not None:
            try:
                ask_val = float(ask) if not callable(ask) else None
            except (TypeError, ValueError):
                pass
        
        if last is not None:
            try:
                last_val = float(last) if not callable(last) else None
            except (TypeError, ValueError):
                pass
        
        # Print diagnostic information
        print(f"🔍 DIAGNOSTIC [{symbol}]:")
        print(f"   Midpoint: {midpoint_val if midpoint_val else 'N/A'}")
        print(f"   Close: {close_val if close_val else 'N/A'}")
        print(f"   Bid: {bid_val if bid_val else 'N/A'}")
        print(f"   Ask: {ask_val if ask_val else 'N/A'}")
        print(f"   Last: {last_val if last_val else 'N/A'}")
        
        # Determine if price is available
        if midpoint_val and midpoint_val > 0:
            print(f"   ✓ Price available: ${midpoint_val:.2f} (using midpoint)")
        elif close_val and close_val > 0:
            print(f"   ✓ Price available: ${close_val:.2f} (using close)")
        elif bid_val and ask_val and bid_val > 0 and ask_val > 0:
            estimated = (bid_val + ask_val) / 2.0
            print(f"   ✓ Price available: ${estimated:.2f} (estimated from bid/ask)")
        elif last_val and last_val > 0:
            print(f"   ✓ Price available: ${last_val:.2f} (using last)")
        else:
            print(f"   ❌ ERROR: Price not available for {symbol}")
            print("      All price sources returned None or invalid values")
            
    except Exception as e:
        print(f"❌ DIAGNOSTIC [{symbol}]: Error - {e}")
        import traceback
        traceback.print_exc()

def get_market_price(ib: IB, symbol: str) -> Optional[float]:
    """
    Get last trade price for a symbol.
    Returns None if price cannot be obtained.
    """
    try:
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ticker = ib.reqMktData(contract, '', False, False)
        ib.sleep(0.5)  # give it a moment for data to arrive
        
        # Prioritize last (most recent trade price), then close
        # Access as properties (not methods) - these should be float values or None
        price_candidates = []
        
        # Get last (if available and is a number)
        last = getattr(ticker, 'last', None)
        if last is not None:
            try:
                last_val = float(last) if not callable(last) else None
                if last_val is not None and last_val > 0:
                    price_candidates.append(last_val)
            except (TypeError, ValueError):
                pass
        
        # Get close (if available and is a number)
        close = getattr(ticker, 'close', None)
        if close is not None:
            try:
                close_val = float(close) if not callable(close) else None
                if close_val is not None and close_val > 0:
                    price_candidates.append(close_val)
            except (TypeError, ValueError):
                pass
        
        # Return first valid price
        if price_candidates:
            return price_candidates[0]
        
        # Log diagnostic info when price cannot be obtained
        print(f"Warning: Could not get market price for {symbol} - last={getattr(ticker, 'last', None)}, close={getattr(ticker, 'close', None)}")
        return None
    except Exception as e:
        print(f"Error getting market price for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None

def calculate_trailing_stop(ib: IB, symbol: str, prior_bars: int = 3, position_side: str = "long") -> Optional[float]:
    """
    Calculate trailing stop price based on last N bars (15-minute bars) during regular trading hours only.
    For long: minimum (lowest low) from the prior bars.
    For short: maximum (highest high) from the prior bars.
    Only uses data from regular trading hours (RTH). Returns None if market is closed or insufficient data.
    
    Args:
        ib: IB connection instance
        symbol: Stock symbol to calculate trailing stop for
        prior_bars: Number of 15-minute bars to look back (default: 3)
        position_side: "long" or "short" (default: "long")
    
    Returns:
        Trailing stop price as float, or None if calculation fails or market is closed
    """
    try:
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        
        # Calculate duration: prior_bars * 15 minutes per bar * 60 seconds per minute
        # Add a small buffer to ensure we get enough bars
        duration_seconds = (prior_bars * 15 * 60) + (15 * 60)  # Request one extra bar to be safe
        bars = ib.reqHistoricalData(
            contract, endDateTime='', durationStr=f'{duration_seconds} S',
            barSizeSetting='15 mins', whatToShow='TRADES', useRTH=True, formatDate=1
        )
        
        # Only use bars from regular trading hours - return None if no bars available
        if not bars:
            return None
        
        # Take only the last prior_bars bars to ensure we use exactly the requested number
        bars = bars[-prior_bars:] if len(bars) >= prior_bars else bars
        
        # Need at least one bar to calculate stop
        if not bars:
            return None
        
        if position_side.lower() == "long":
            # For long: minimum value (lowest low) from the prior bars during market hours
            return float(min(bar.low for bar in bars))
        else:
            # For short: maximum value (highest high) from the prior bars during market hours
            return float(max(bar.high for bar in bars))
            
    except Exception as e:
        print(f"Error calculating trailing stop for {symbol}: {e}")
        return None

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
            return None
        
        # Calculate daily ranges (high - low) for each bar
        daily_ranges = [bar.high - bar.low for bar in bars if bar.high is not None and bar.low is not None]
        
        if not daily_ranges:
            return None
        
        # Calculate average daily range
        adr = sum(daily_ranges) / len(daily_ranges)
        
        return float(adr)
        
    except Exception as e:
        print(f"Error calculating ADR for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None

def calculate_gap_percentage(ib: IB, symbol: str, current_price: float) -> Optional[float]:
    """
    Calculate the gap percentage from previous day's close to current price.
    
    Gap = ((current_price - yesterday_close) / yesterday_close) * 100
    
    Only returns positive gaps (gap up). Returns None for negative gaps or if calculation fails.
    
    Args:
        ib: IB connection instance
        symbol: Stock symbol to calculate gap for
        current_price: Current market price (used instead of today's open for pre-market trading)
    
    Returns:
        Gap percentage as float (positive for gap up only), or None if calculation fails or gap is negative
    """
    try:
        # Create and qualify contract
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        
        # Request 2 days of daily bars to get yesterday's close
        bars = ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr='2 D',
            barSizeSetting='1 day',
            whatToShow='TRADES',
            useRTH=True,
            formatDate=1
        )
        
        if not bars or len(bars) < 2:
            # Need at least 2 days of data (yesterday and today)
            return None
        
        # Get yesterday's close (second-to-last bar)
        yesterday_bar = bars[-2]
        yesterday_close = yesterday_bar.close
        
        if yesterday_close is None or yesterday_close <= 0:
            return None
        
        # Calculate gap percentage using current price instead of today's open
        gap_percentage = ((current_price - yesterday_close) / yesterday_close) * 100
        
        # Only return positive gaps (gap up)
        if gap_percentage > 0:
            return float(gap_percentage)
        else:
            # Negative gap or no gap - return None
            return None
        
    except Exception as e:
        print(f"Error calculating gap percentage for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None


# =========================================================
# normalization and summarization
def normalize_record(rec):
    """
    Convert a raw external-positions record into a normalized dict with:
    - trader name
    - long-term flag
    - symbol info (equity vs option, underlying, expiry, strike, C/P)
    - side (long/short/flat)
    - magnitude
    """
    account_name = rec["account_name"]  # e.g. "VC Jeff Holden" or "VC Justin Spero LT"

    # Strip leading "VC " if present
    if account_name.startswith("VC "):
        core = account_name[3:]
    else:
        core = account_name

    # Long-term flag if name ends with " LT"
    is_long_term = core.endswith(" LT")
    if is_long_term:
        trader_name = core[:-3]  # drop trailing " LT"
    else:
        trader_name = core

    symbol_raw = rec["symbol"]       # e.g. "AMD" or "ETHA 2025-11-21 P 23.00"
    side = rec["side"].lower()      # "long" "short" "flat"
    magnitude = rec["magnitude"]

    # Try to detect options of the form: "TICKER YYYY-MM-DD C/P STRIKE"
    # Example: "ETHA 2025-11-21 P 23.00"
    opt_match = re.match(r"^([A-Z]+)\s+(\d{4}-\d{2}-\d{2})\s+([CP])\s+([\d.]+)$", symbol_raw)

    if opt_match:
        underlying, expiry, opt_type, strike_str = opt_match.groups()
        instrument_type = "option"
        strike = float(strike_str)
    else:
        underlying = symbol_raw
        expiry = None
        opt_type = None
        strike = None
        instrument_type = "equity"

    # Normalize side into long/short/flat
    if side not in ("long", "short", "flat"):
        # just in case they add something weird
        normalized_side = "unknown"
    else:
        normalized_side = side

    return {
        "trader": trader_name,          # e.g. "Jeff Holden"
        "is_long_term": is_long_term,   # True for LT accounts
        "symbol_raw": symbol_raw,       # as given by API
        "side": normalized_side,        # "long" "short" "flat"
        "magnitude": magnitude,         # position size / weight
        "last_updated": rec["last_updated"],
        "created_at": rec["created_at"],"instrument_type": instrument_type,  # equity/option
        "underlying": underlying,       # equity ticker or option underlying
        "expiry": expiry,               # option expiry as string, or None
        "strike": strike,               # option strike as float, or None
        "option_type": opt_type,        # "C" or "P" for options
        # "account_name": account_name,   # full SMB account name
    }

    
def summarize_group(records):
    """
    Determine the trader's *net* position for one symbol.
    """
    has_long =  any(r["side"] == "long" and r["magnitude"] > 0 for r in records)
    has_short = any(r["side"] == "short" and r["magnitude"] > 0 for r in records)
    has_activity = any(r["magnitude"] > 0 for r in records)

    if has_long and has_short:
        net_side = "conflict"
        conflict = True
    elif has_long:
        net_side = "long"
        conflict = False
    elif has_short:
        net_side = "short"
        conflict = False
    else:
        net_side = "flat"
        conflict = False
    base = records[0]
    return {
        "trader": base["trader"],
        "is_long_term": base["is_long_term"],
        "symbol": base["symbol_raw"],
        "instrument_type": base["instrument_type"],
        "underlying": base["underlying"],
        "expiry": base["expiry"],
        "strike": base["strike"],
        "option_type": base["option_type"],
        "net_side": net_side,            # long / short / flat / conflict
        "conflict": conflict,
        "total_magnitude": sum(r["magnitude"] for r in records),
    }





def save_snapshot(summary_rows, path=SNAPSHOT_FILE):
    """
    Save the current summarized positions to disk as JSON.
    Parameters:
        summary_rows (list[dict]): the list of position summaries produced
                                   by summarize_group (one per symbol/trader).
        path (str): file path where the JSON will be written.
    """
    # json.dump converts Python objects -> JSON and writes them to a file.
    # indent=2 keeps it readable if you open it manually.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary_rows, f, indent=2)


def load_snapshot(path=SNAPSHOT_FILE):
    """
    Load a previously saved snapshot of summarized positions from disk.
    Parameters:
        path (str): file path where the JSON snapshot is stored.
    Returns:
        list[dict]: the list of position summaries as loaded from JSON.
    """
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


# =========================================================
# Execution Tracking (CSV)
# =========================================================
#%%
def format_timestamp(dt: Optional[datetime] = None) -> str:
    """
    Format datetime as a database and Excel-friendly timestamp string.
    
    Format: YYYY-MM-DD HH:MM:SS (space-separated, seconds precision)
    This format is:
    - Recognized by Excel when importing CSV
    - Compatible with most databases (PostgreSQL, MySQL, SQLite, etc.)
    - Sortable and filterable in both Excel and databases
    
    Args:
        dt: datetime object (defaults to current time if None)
    
    Returns:
        Formatted timestamp string
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def ensure_executions_dir():
    """Ensure the executions directory exists."""
    if not os.path.exists(EXECUTIONS_DIR):
        os.makedirs(EXECUTIONS_DIR)

#%%
def get_executions_filename() -> str:
    """Get the executions CSV filename for today."""
    today = date.today()
    filename = f"executions_{today.strftime('%Y-%m-%d')}.csv"
    return os.path.join(EXECUTIONS_DIR, filename)

def save_execution_to_csv(
    trader: str,
    symbol: str,
    change_type: str,
    net_side: str,
    delta_magnitude: float,
    entry_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    order_id: Optional[str] = None,
    timestamp: Optional[str] = None
):
    """
    Save execution data to CSV file.
    
    Args:
        trader: Trader name
        symbol: Symbol/ticker
        change_type: NEW, ADD, TRIM, FLIP, etc.
        net_side: long, short, flat
        delta_magnitude: Change in magnitude
        entry_price: Entry/limit price (optional)
        stop_price: Stop loss price (optional)
        take_profit_price: Take profit price (optional)
        order_id: IB order ID if order was placed (optional)
        timestamp: Timestamp string (defaults to current time)
    """
    ensure_executions_dir()
    filename = get_executions_filename()
    
    if timestamp is None:
        timestamp = format_timestamp()
    
    # Check if file exists to determine if we need to write header
    file_exists = os.path.exists(filename)
    
    # CSV columns: timestamp, trader, symbol, change_type, net_side, delta_magnitude, 
    #              entry_price, stop_price, take_profit_price, order_id
    fieldnames = [
        "timestamp", "trader", "symbol", "change_type", "net_side", "delta_magnitude",
        "entry_price", "stop_price", "take_profit_price", "order_id"
    ]
    
    with open(filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        
        writer.writerow({
            "timestamp": timestamp,
            "trader": trader,
            "symbol": symbol,
            "change_type": change_type,
            "net_side": net_side,
            "delta_magnitude": delta_magnitude,
            "entry_price": entry_price if entry_price is not None else "",
            "stop_price": stop_price if stop_price is not None else "",
            "take_profit_price": take_profit_price if take_profit_price is not None else "",
            "order_id": order_id if order_id is not None else "",
        })


# =========================================================
# Order Execution
# =========================================================

def get_available_funds(ib: IB, currency: str = ACCOUNT_CURRENCY) -> float:
    """Get available funds for the specified currency."""
    try:
        for av in ib.accountValues():
            if av.tag == "AvailableFunds" and av.currency == currency:
                try:
                    return float(av.value)
                except ValueError:
                    pass
    except Exception:
        pass
    return 0.0

def calculate_num_shares_from_risk(
    trade_stop_amount: float,
    entry_price: float,
    stop_loss_price: float,
    is_long: bool,
    available_funds: float,
    max_notional_fraction: float = 0.01
) -> int:
    """
    Calculate number of shares based on risk management parameters.
    
    Args:
        trade_stop_amount: Maximum dollar amount to risk on this trade
        entry_price: Entry price
        stop_loss_price: Stop loss price
        is_long: True for long positions, False for short
        available_funds: Available funds in account
        max_notional_fraction: Maximum fraction of available funds to use (default 1%)
    
    Returns:
        int: Number of shares (floored to lower whole number), or 0 if calculation not possible
    """
    # Calculate risk per share
    if is_long:
        risk_per_share = entry_price - stop_loss_price
    else:
        risk_per_share = stop_loss_price - entry_price
    
    if risk_per_share <= 0:
        return 0
    
    # Calculate shares based on risk
    shares_from_risk = int(trade_stop_amount / risk_per_share)
    
    # Calculate shares based on available funds constraint
    max_notional = available_funds * max_notional_fraction
    shares_from_funds = int(max_notional / entry_price)
    
    # Take the minimum of both constraints
    num_shares = min(shares_from_risk, shares_from_funds)
    
    return max(0, num_shares)
#%%
def get_position_size(ib: IB, symbol: str) -> int:
    """
    Get current position size for a symbol.
    
    Returns:
        int: Positive number for long positions, negative for short positions, 0 if no position
    """
    try:
        positions = ib.positions()
        for pos in positions:
            if pos.contract.symbol == symbol and pos.contract.secType == "STK":
                return int(pos.position)
        return 0
    except Exception:
        return 0

def has_open_orders(ib: IB, symbol: str, is_long: Optional[bool] = None) -> bool:
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
            
            # Check if this is the symbol we're looking for
            if contract.symbol == symbol and contract.secType == "STK":
                # Check order status - skip filled/cancelled orders
                if status.status in ["PreSubmitted", "Submitted", "PendingSubmit", "PendingCancel", "ApiPending"]:
                    # If is_long is specified, check direction
                    if is_long is not None:
                        order_is_buy = order.action.upper() == "BUY"
                        if order_is_buy == is_long:
                            return True
                    else:
                        # Any open order matches
                        return True
        return False
    except Exception:
        return False

def find_orders_for_symbol_trader(ib: IB, symbol: str, trader: str = "") -> list:
    """
    Find all open orders for a specific symbol and trader.
    
    Args:
        ib: IB connection
        symbol: Symbol to find orders for
        trader: Trader name (optional)
    
    Returns:
        list: List of Trade objects matching the criteria
    """
    matching_trades = []
    try:
        order_tag = f"{ORDER_TAG}-{trader}" if trader else ORDER_TAG
        open_trades = ib.openTrades()
        
        for trade in open_trades:
            contract = trade.contract
            order = trade.order
            status = trade.orderStatus
            
            # Check if this is the symbol we're looking for
            if contract.symbol == symbol and contract.secType == "STK":
                # Check if orderRef matches
                order_ref = getattr(order, 'orderRef', '')
                if order_ref == order_tag:
                    # Only include active orders (not filled/cancelled)
                    if status.status in ["PreSubmitted", "Submitted", "PendingSubmit", "PendingCancel", "ApiPending"]:
                        matching_trades.append(trade)
        
        return matching_trades
    except Exception as e:
        print(f"Error finding orders for {symbol} ({trader}): {e}")
        return []

def cancel_all_orders_for_position(ib: IB, symbol: str, trader: str = "") -> int:
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
            
            # Only cancel active orders
            if status.status in ["PreSubmitted", "Submitted", "PendingSubmit", "PendingCancel", "ApiPending"]:
                try:
                    ib.cancelOrder(order)
                    cancelled_count += 1
                    print(f"   ✓ Cancelled order {order.orderId} ({order.action} {order.totalQuantity} {symbol})")
                except Exception as e:
                    print(f"Error cancelling order {order.orderId}: {e}")
        
        if cancelled_count > 0:
            print(f"   ✓ Cancelled {cancelled_count} order(s) for {symbol}")
        
        return cancelled_count
    except Exception as e:
        print(f"Error cancelling orders for {symbol} ({trader}): {e}")
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
        
        # Filter for child orders (orders with parentId set)
        child_orders = []
        for trade in matching_trades:
            order = trade.order
            if hasattr(order, 'parentId') and order.parentId is not None and order.parentId > 0:
                child_orders.append(trade)
        
        if not child_orders:
            # No child orders found - fallback to current behavior
            return False
        
        # Update each child order
        updated_count = 0
        for trade in child_orders:
            order = trade.order
            status = trade.orderStatus
            
            # Only update active orders
            if status.status in ["PreSubmitted", "Submitted", "PendingSubmit", "PendingCancel", "ApiPending"]:
                current_quantity = order.totalQuantity
                new_quantity = current_quantity + share_delta
                
                # If new quantity would be 0 or negative, cancel the order instead
                if new_quantity <= 0:
                    try:
                        ib.cancelOrder(order)
                        print(f"   ✓ Cancelled child order {order.orderId} (would be {new_quantity} shares)")
                    except Exception as e:
                        print(f"Error cancelling child order {order.orderId}: {e}")
                else:
                    # Modify the order with new quantity
                    try:
                        order.totalQuantity = new_quantity
                        # Re-qualify contract to ensure it's valid
                        ib.qualifyContracts(trade.contract)
                        ib.placeOrder(trade.contract, order)
                        ib.sleep(0.2)  # Small delay to ensure order modification is processed
                        updated_count += 1
                        print(f"   ✓ Updated child order {order.orderId}: {current_quantity} -> {new_quantity} shares")
                    except Exception as e:
                        print(f"Error updating child order {order.orderId}: {e}")
        
        if updated_count > 0:
            print(f"   ✓ Updated {updated_count} child order(s) for {symbol}")
            return True
        
        return False
    except Exception as e:
        print(f"Error updating child orders for {symbol} ({trader}): {e}")
        import traceback
        traceback.print_exc()
        return False

#%%
def send_scaling_order(
    ib: IB,
    symbol: str,
    is_long: bool,
    entry_price: float,
    num_shares: int,
    trader: str = ""
) -> Optional[str]:
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
        Order ID string if successful, None otherwise
    """
    try:
        if num_shares <= 0:
            print(f"Error: Invalid share quantity {num_shares} for {symbol}")
            return None
        
        # Determine action
        action = "BUY" if is_long else "SELL"
        
        # Create and qualify contract
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        
        # Create order tag with trader name if provided
        order_tag = f"{ORDER_TAG}-{trader}" if trader else ORDER_TAG
        
        # Create stop entry order (simple order to add to position)
        order = StopOrder(action, num_shares, entry_price)
        order.tif = 'GTC'  # Good-Til-Canceled (default to avoid Error 10349)
        order.orderRef = order_tag  # Tag the order with trader
        
        # Place order
        trade = ib.placeOrder(contract, order)
        ib.sleep(0.2)
        
        order_id = str(order.orderId) if order.orderId else "pending"
        print(f"Scaling order placed for {symbol}: {action} {num_shares} shares @ ${entry_price:.2f} (STOP-ENTRY)")
        return order_id
        
    except Exception as e:
        print(f"Error placing scaling order for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None
#%%
def send_bracket_order(
    ib: IB,
    symbol: str,
    is_long: bool,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    magnitude: float,
    trader: str = ""
) -> Optional[str]:
    """
    Send a bracket order to IB for NEW/ADD positions.
    
    Args:
        ib: IB connection instance
        symbol: Stock symbol
        is_long: True for long, False for short
        entry_price: Entry/stop trigger price (midpoint)
        stop_price: Stop loss price
        take_profit_price: Take profit price
        magnitude: Position magnitude (used to calculate trade_stop_percent)
    
    Returns:
        Order ID string if successful, None otherwise
    """
    try:
        # Calculate trade stop amount: magnitude = % of daily stop
        trade_stop_percent = magnitude / 100.0
        trade_stop_amount = DAILY_STOP * trade_stop_percent
        
        # Get available funds
        available_funds = get_available_funds(ib)
        if available_funds <= 0:
            print(f"Error: Insufficient available funds: ${available_funds:.2f}")
            return None
        
        # Calculate number of shares
        num_shares = calculate_num_shares_from_risk(
            trade_stop_amount=trade_stop_amount,
            entry_price=entry_price,
            stop_loss_price=stop_price,
            is_long=is_long,
            available_funds=available_funds
        )
        
        if num_shares == 0:
            print(f"Error: Calculated share quantity is zero for {symbol}")
            return None
        
        # Create and qualify contract
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        
        # Determine actions
        if is_long:
            entry_action = "BUY"
            stop_action = "SELL"
            take_profit_action = "SELL"
        else:
            entry_action = "SELL"
            stop_action = "BUY"
            take_profit_action = "BUY"
        
        # Create order tag with trader name if provided
        order_tag = f"{ORDER_TAG}-{trader}" if trader else ORDER_TAG
        
        # Create parent order (stop entry)
        parent_order = StopOrder(entry_action, num_shares, entry_price)
        parent_order.tif = 'GTC'  # Good-Til-Canceled (default to avoid Error 10349)
        parent_order.transmit = False
        parent_order.orderRef = order_tag  # Tag the order with trader
        
        # Place parent order
        parent_trade = ib.placeOrder(contract, parent_order)
        ib.sleep(0.5)
        
        # Get parent order ID
        parent_order_id = parent_order.orderId
        if parent_order_id is None and hasattr(parent_trade, 'order'):
            parent_order_id = parent_trade.order.orderId
        
        if parent_order_id is None:
            print(f"Error: Could not obtain parent order ID for {symbol}")
            return None
        
        # Create take profit order (child)
        take_profit_order = LimitOrder(take_profit_action, num_shares, take_profit_price)
        take_profit_order.tif = 'GTC'  # Good-Til-Canceled (default to avoid Error 10349)
        take_profit_order.parentId = parent_order_id
        take_profit_order.transmit = False
        take_profit_order.orderRef = order_tag  # Tag the order with trader
        
        # Create stop loss order (child)
        stop_order = StopOrder(stop_action, num_shares, stop_price)
        stop_order.tif = 'GTC'  # Good-Til-Canceled (default to avoid Error 10349)
        stop_order.parentId = parent_order_id
        stop_order.transmit = True  # This sends the whole bracket
        stop_order.orderRef = order_tag  # Tag the order with trader
        
        # Place child orders
        ib.placeOrder(contract, take_profit_order)
        stop_trade = ib.placeOrder(contract, stop_order)
        ib.sleep(0.5)
        
        # Get stop order ID as the main order ID
        order_id = str(parent_order_id)
        print(f"Bracket order placed for {symbol}: {entry_action} {num_shares} @ ${entry_price:.2f} (STOP-ENTRY), Stop Loss @ ${stop_price:.2f}, TP @ ${take_profit_price:.2f}")
        return order_id
        
    except Exception as e:
        print(f"Error placing bracket order for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None

def send_entry_only_order(
    ib: IB,
    symbol: str,
    is_long: bool,
    entry_price: float,
    magnitude: float,
    trader: str = ""
) -> Optional[str]:
    """
    Send an entry order without stop loss (when trailing stop and ADR both fail).
    
    Args:
        ib: IB connection instance
        symbol: Stock symbol
        is_long: True for long, False for short
        entry_price: Entry/stop trigger price
        magnitude: Position magnitude (used to calculate trade_stop_percent)
    
    Returns:
        Order ID string if successful, None otherwise
    """
    try:
        # Calculate trade stop amount: magnitude = % of daily stop
        trade_stop_percent = magnitude / 100.0
        trade_stop_amount = DAILY_STOP * trade_stop_percent
        
        # Get available funds
        available_funds = get_available_funds(ib)
        if available_funds <= 0:
            print(f"Error: Insufficient available funds: ${available_funds:.2f}")
            return None
        
        # Use a conservative risk calculation (assume 2% stop for sizing)
        # This is a fallback when we don't have a real stop price
        assumed_risk_percent = 0.02  # 2% assumed risk
        num_shares = int((available_funds * trade_stop_percent) / (entry_price * assumed_risk_percent))
        
        if num_shares == 0:
            print(f"Error: Calculated share quantity is zero for {symbol}")
            return None
        
        # Create and qualify contract
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        
        # Determine action
        action = "BUY" if is_long else "SELL"
        
        # Create order tag with trader name if provided
        order_tag = f"{ORDER_TAG}-{trader}" if trader else ORDER_TAG
        
        # Create stop entry order (no stop loss attached)
        order = StopOrder(action, num_shares, entry_price)
        order.tif = 'GTC'  # Good-Til-Canceled (default to avoid Error 10349)
        order.orderRef = order_tag  # Tag the order with trader
        
        # Place order
        trade = ib.placeOrder(contract, order)
        ib.sleep(1)
        
        order_id = str(order.orderId) if order.orderId else "pending"
        print(f"⚠️  WARNING: Entry-only order placed for {symbol}: {action} {num_shares} @ ${entry_price:.2f} (NO STOP LOSS)")
        return order_id
        
    except Exception as e:
        print(f"Error placing entry-only order for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None

def send_market_order(ib: IB, symbol: str, is_long: bool, position_size: int, trader: str = "") -> Optional[str]:
    """
    Send a market order to IB for TRIM positions (exit).
    
    Args:
        ib: IB connection instance
        symbol: Stock symbol
        is_long: True if currently long (so we SELL), False if short (so we BUY)
        position_size: Number of shares to exit
        trader: Trader name for order tagging (optional)
    
    Returns:
        Order ID string if successful, None otherwise
    """
    try:
        if position_size <= 0:
            print(f"Error: Invalid position size {position_size} for {symbol}")
            return None
        
        # Determine action: if long, sell to exit; if short, buy to exit
        action = "SELL" if is_long else "BUY"
        
        # Create and qualify contract
        contract = Stock(symbol, 'SMART', ACCOUNT_CURRENCY)
        ib.qualifyContracts(contract)
        
        # Create order tag with trader name if provided
        order_tag = f"{ORDER_TAG}-{trader}" if trader else ORDER_TAG
        
        # Create market order
        order = MarketOrder(action, position_size)
        order.tif = 'GTC'  # Good-Til-Canceled (default to avoid Error 10349)
        order.orderRef = order_tag  # Tag the order with trader
        
        # Place order
        trade = ib.placeOrder(contract, order)
        ib.sleep(1)
        
        order_id = str(order.orderId) if order.orderId else "pending"
        print(f"Market order placed for {symbol}: {action} {position_size} shares (exit)")
        return order_id
        
    except Exception as e:
        print(f"Error placing market order for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None
#%%
def process_execution_change(
    ib: Optional[IB],
    row: dict,
    change_type: str
) -> None:
    """
    Process a position change and execute orders if active_trading is enabled.
    
    Args:
        ib: IB connection (None if not connected)
        row: Position summary row with change annotations
        change_type: Change type (NEW, ADD, TRIM, CLOSE)
    """
    trader = row.get("trader")
    symbol = row.get("symbol")
    net_side = row.get("net_side")
    delta_magnitude = row.get("delta_magnitude", 0)
    
    # Validate required fields
    if not trader or not symbol or not net_side:
        return
    
    # Skip if there's no magnitude change (delta_magnitude == 0)
    # EXCEPT for CLOSE - we need to process CLOSE even if delta is 0 because we need to exit the position
    if delta_magnitude == 0 and change_type != "CLOSE":
        return
    
    # Check if trader is enabled
    if not TRADER_ENABLED.get(trader, False):
        return
    
    # Only process equity instruments
    if row.get("instrument_type") != "equity":
        return
    
    # Extract underlying symbol (should be same as symbol for equity)
    underlying = row.get("underlying") or symbol
   
    timestamp = format_timestamp()
    entry_price = None
    stop_price = None
    take_profit_price = None
    order_id = None
    
    # Process based on change type
    if change_type in ["NEW", "ADD"]:
        if net_side == "long" or net_side == "short":
            is_long = (net_side == "long")
            
            # Get market data if IB is connected
            if ib is not None and ib.isConnected():
                # For NEW changes, run diagnostic first (before trying to get price)
                if change_type == "NEW":
                    diagnose_market_price(ib, underlying)
                
                entry_price = get_market_price(ib, underlying)
                if entry_price:
                    entry_price = round(entry_price, 2)
                    stop_price = None
                    take_profit_price = None
                    
                    # Initialize adjusted magnitude (may be reduced for large gap up positions)
                    adjusted_magnitude = abs(delta_magnitude)
                    
                    # For NEW positions, check gap percentage and adjust position size if gap > 99%
                    if change_type == "NEW":
                        gap_percentage = calculate_gap_percentage(ib, underlying, entry_price)
                        if gap_percentage and gap_percentage > 99:
                            adjusted_magnitude = abs(delta_magnitude) / 10
                            print(f"⚠️  WARNING: {underlying} gapped up {gap_percentage:.2f}% (>99%) - reducing position size from {abs(delta_magnitude)} to {adjusted_magnitude:.2f}")
                    
                    # First, check if ADR is available (required for take profit)
                    adr = calculate_adr(ib, underlying)
                    
                    if not adr:
                        # ADR not available - cannot use trailing stop, will send entry-only order
                        print(f"⚠️  WARNING: ADR not available for {underlying} - cannot calculate take profit, will send entry-only order")
                        stop_price = None
                        take_profit_price = None
                    else:
                        # ADR available - now try trailing stop as primary
                        position_side_str = "long" if is_long else "short"
                        trailing_stop = calculate_trailing_stop(ib, underlying, prior_bars=3, position_side=position_side_str)
                        
                        if trailing_stop:
                            # PRIMARY: Use trailing stop
                            stop_price = round(trailing_stop, 2)
                            print(f"✓ Using trailing stop for {underlying}: ${stop_price:.2f}")
                        else:
                            # FALLBACK: Use ADR for stop if trailing stop fails
                            if is_long:
                                stop_price = entry_price - (0.5 * adr)
                            else:
                                stop_price = entry_price + (0.5 * adr)
                            stop_price = round(stop_price, 2)
                            print(f"✓ Using ADR stop for {underlying}: ${stop_price:.2f} (ADR: ${adr:.2f})")
                        
                        # Calculate take profit using ADR (required)
                        if stop_price:
                            if is_long:
                                take_profit_price = entry_price + (1.0 * adr)
                            else:
                                take_profit_price = entry_price - (1.0 * adr)
                            take_profit_price = round(take_profit_price, 2)
                    
                    # Send orders if active trading is enabled
                    if ACTIVE_TRADING:
                            # For NEW changes, check if there's already an order or position
                            if change_type == "NEW":
                                
                                # Check for existing position
                                current_position = get_position_size(ib, underlying)
                                has_position = current_position != 0
                                
                                # Check for open orders in the same direction
                                has_open_order = has_open_orders(ib, underlying, is_long)
                                
                                # Skip if there's already a position or open order
                                if has_position or has_open_order:
                                    print(f"Skipping NEW order for {underlying}: position={current_position}, open_order={has_open_order}")
                                    # Don't create order, but still save to CSV
                                else:
                                    # Check if we have both stop price and take profit (ADR required)
                                    if stop_price and take_profit_price:
                                        # Create bracket order with stop
                                        order_id = send_bracket_order(
                                            ib, underlying, is_long, entry_price,
                                            stop_price, take_profit_price, adjusted_magnitude, trader
                                        )
                                    else:
                                        # No stop available or ADR failed - send entry-only order with warning
                                        if not stop_price:
                                            print(f"⚠️  WARNING: No stop loss available for {underlying} (trailing stop and ADR both failed)")
                                        else:
                                            print(f"WARNING: ADR not available for {underlying} - cannot calculate take profit, sending entry-only order")
                                        order_id = send_entry_only_order(
                                            ib, underlying, is_long, entry_price, adjusted_magnitude, trader
                                        )
                                        # Set stop_price to None for CSV logging
                                        stop_price = None
                                        take_profit_price = None
                            
                            # For ADD changes, check if we already have a position
                            elif change_type == "ADD":
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
                                        # Recalculate ADR for sizing purposes
                                        scaling_adr = calculate_adr(ib, underlying)
                                        if scaling_adr:
                                            if is_long:
                                                scaling_stop_price = entry_price - (0.5 * scaling_adr)
                                            else:
                                                scaling_stop_price = entry_price + (0.5 * scaling_adr)
                                        else:
                                            # No ADR available - use conservative 2% stop for sizing
                                            scaling_stop_price = entry_price * (0.98 if is_long else 1.02)
                                            print(f"Using assumed 2% stop for scaling order sizing: ${scaling_stop_price:.2f}")
                                    
                                    if available_funds > 0:
                                        num_shares_to_add = calculate_num_shares_from_risk(
                                            trade_stop_amount=trade_stop_amount,
                                            entry_price=entry_price,
                                            stop_loss_price=scaling_stop_price,
                                            is_long=is_long,
                                            available_funds=available_funds
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
                                                    print(f"Skipping ADD scaling order for {underlying}: open order already exists")
                                                    # Don't create order, but still save to CSV
                                                else:
                                                    # Scale into existing position with a simple stop order
                                                    order_id = send_scaling_order(
                                                        ib, underlying, is_long, entry_price, num_shares_to_add, trader
                                                    )
                                                    # For scaling, we don't set stop/take_profit in CSV (existing position has them)
                                                    stop_price = None
                                                    take_profit_price = None
                                            else:
                                                # Child orders were updated - no need to create new order
                                                # For scaling, we don't set stop/take_profit in CSV (existing position has them)
                                                stop_price = None
                                                take_profit_price = None
                                else:
                                    # No existing position, check for open orders before creating new bracket
                                    has_open_order = has_open_orders(ib, underlying, is_long)
                                    if has_open_order:
                                        print(f"Skipping ADD bracket order for {underlying}: open order already exists")
                                        # Don't create order, but still save to CSV
                                    else:
                                        # No existing position or order, create bracket order (treat like NEW)
                                        if stop_price and take_profit_price:
                                            # Create bracket order with stop
                                            order_id = send_bracket_order(
                                                ib, underlying, is_long, entry_price,
                                                stop_price, take_profit_price, abs(delta_magnitude), trader
                                            )
                                        else:
                                            # No stop available or ADR failed - send entry-only order with warning
                                            if not stop_price:
                                                print(f" WARNING: No stop loss available for {underlying} (trailing stop and ADR both failed)")
                                            else:
                                                print(f"WARNING: ADR not available for {underlying} - cannot calculate take profit, sending entry-only order")
                                            order_id = send_entry_only_order(
                                                ib, underlying, is_long, entry_price, abs(delta_magnitude), trader
                                            )
                                            # Set stop_price to None for CSV logging
                                            stop_price = None
                                            take_profit_price = None
                else:
                    # No entry price available
                    print(f"Warning: Could not get market price for {underlying} - order not placed")
            else:
                print(f"IB not connected - skipping market data for {underlying}")
            
            # Save to CSV (always save, even if order failed)
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
                timestamp=timestamp
            )
    
    elif change_type == "TRIM":
        if net_side in ["long", "short"]:
            is_long = (net_side == "long")
            
            if ib is not None and ib.isConnected():
                # Get current position size from IB
                try:
                    current_position = get_position_size(ib, underlying)
                    
                    if current_position != 0:
                        # Calculate shares to trim based on delta_magnitude
                        # This is approximate - real implementation would track position sizes
                        exit_size = abs(int(current_position * (abs(delta_magnitude) / 100.0)))
                        
                        if exit_size > 0:
                            if ACTIVE_TRADING:
                                # Check if the TRIM would result in closing the position
                                # If exit_size >= abs(current_position), we should CLOSE instead
                                if exit_size >= abs(current_position):
                                    print(f"⚠️  TRIM ({exit_size} shares) >= position size ({abs(current_position)} shares) - converting to CLOSE")
                                    # Cancel all orders and exit entire position (same as CLOSE)
                                    cancel_all_orders_for_position(ib, underlying, trader)
                                    order_id = send_market_order(ib, underlying, is_long, abs(current_position), trader)
                                    # Update change_type for CSV logging
                                    change_type = "CLOSE"
                                else:
                                    # TRIM is less than position size - reduce position AND update child orders
                                    # Always send market order to reduce the actual position
                                    order_id = send_market_order(ib, underlying, is_long, exit_size, trader)
                                    print(f"   ✓ Market order placed to trim {exit_size} shares: order_id={order_id}")
                                    
                                    # Also update child orders to match the new position size
                                    # This keeps stop loss and take profit in sync with the reduced position
                                    child_orders_updated = update_child_orders_for_position(
                                        ib, underlying, trader, -exit_size
                                    )
                                    if child_orders_updated:
                                        print(f"   ✓ Updated child orders to match reduced position size")
                    else:
                        print(f"⚠️  No position found in IB for {underlying} - nothing to trim")
                except Exception as e:
                    print(f"Error getting position for {underlying}: {e}")
                    import traceback
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
    
    elif change_type == "CLOSE":
        # CLOSE: Position went flat - exit the entire position
        print(f"🔄 CLOSE detected for {underlying} ({trader})")
        if ib is not None and ib.isConnected():
            try:
                # First, cancel all open orders (stop loss, take profit, entry orders) for this position
                print(f"   Cancelling all open orders for {underlying}...")
                cancelled_count = cancel_all_orders_for_position(ib, underlying, trader)
                
                # Get current position size from IB
                current_position = get_position_size(ib, underlying)
                print(f"   Current position in IB: {current_position} shares")
                if current_position != 0:
                    # Exit entire position
                    is_long = current_position > 0
                    exit_size = abs(current_position)
                    print(f"   Exiting {exit_size} shares ({'long' if is_long else 'short'})")
                    if ACTIVE_TRADING and exit_size > 0:
                        order_id = send_market_order(ib, underlying, is_long, exit_size, trader)
                        print(f"   ✓ Market order placed: order_id={order_id}")
                    else:
                        print(f"   ⚠️  ACTIVE_TRADING is {ACTIVE_TRADING} or exit_size={exit_size}")
                else:
                    print(f"   ⚠️  No position found in IB for {underlying} - nothing to close")
            except Exception as e:
                print(f"   ❌ Error closing position for {underlying}: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("   ❌ IB not connected - cannot check position or place order")
        
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
    
    elif change_type == "FLIP":
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
        print(underlying)

#%%
def make_position_key(row):
    """
    Create a unique key for a position summary row based on trader, LT flag, symbol, and side
    """
    return (row["trader"], row["is_long_term"], row["symbol"], row["net_side"])

def make_position_key_no_side(row):
    """
    Create a key for a position without the side (used to detect position closures)
    """
    return (row["trader"], row["is_long_term"], row["symbol"])
#%%
def annotate_with_changes(current_rows, previous_snapshot):
    """
    Given the current summary rows and the previously saved snapshot,
    add:
      - prev_magnitude
      - delta_magnitude
      - change_type  (NEW / ADD / TRIM / CLOSE / FLIP / UNCHANGED / NONE)

    Returns:
        list[dict]: the same list, but each dict is enriched with change info.
    """
    # First run: no previous snapshot everything is treated as NEW from 0
    if previous_snapshot is None:
        for row in current_rows:
            row["prev_magnitude"] = 0
            row["delta_magnitude"] = row["total_magnitude"]
            row["change_type"] = "NEW" if row["total_magnitude"] != 0 else "NONE"
        return current_rows
    # Build a lookup from previous snapshot using a tuple key (with side)
    prev_index = {}
    for prev_row in previous_snapshot:
        key = make_position_key(prev_row)
        prev_index[key] = prev_row
    
    # Build a lookup by key without side (to detect position closures)
    prev_index_no_side = {}
    for prev_row in previous_snapshot:
        key_no_side = make_position_key_no_side(prev_row)
        if key_no_side not in prev_index_no_side:
            prev_index_no_side[key_no_side] = []
        prev_index_no_side[key_no_side].append(prev_row)
    
    # Now annotate current rows
    for row in current_rows:
        key = make_position_key(row)
        prev_row = prev_index.get(key)

        prev_mag = prev_row["total_magnitude"] if prev_row else 0
        curr_mag = row["total_magnitude"]

        prev_side = prev_row["net_side"] if prev_row else "flat"
        curr_side = row["net_side"]

        row["prev_magnitude"] = prev_mag
        row["delta_magnitude"] = curr_mag - prev_mag

        # Determine change type (similar to a switch TRUE function in Excel)
        # First check for CLOSE: position went from long/short to flat
        if prev_side != "flat" and curr_side == "flat":
            change = "CLOSE"  # Position was closed (went flat)
        elif prev_row == None and curr_mag > 0:
            change = "NEW"
        elif prev_row == None and curr_mag == 0:
            # Check if this position was closed (went from long/short to flat)
            key_no_side = make_position_key_no_side(row)
            prev_rows_same_symbol = prev_index_no_side.get(key_no_side, [])
            # Check if there was a previous position with non-flat side
            had_non_flat_position = any(p["net_side"] != "flat" and p["total_magnitude"] > 0 
                                       for p in prev_rows_same_symbol)
            if had_non_flat_position:
                change = "CLOSE"  # Position was closed (went flat)
            else:
                change = "FLAT"  # Always been flat
        elif prev_side != curr_side and prev_side != "flat" and curr_side != "flat":
            change = "FLIP"
        elif row["delta_magnitude"] > 0:
            change = "ADD"
        elif row["delta_magnitude"] < 0:
            change = "TRIM"
        else:
            change = None
        row["change_type"] = change
    return current_rows
#%%
def print_position_table(summary_rows, hide_flat=True):
    """
     print a table of positions:
     
    Trader | LT | Symbol | Type | Side | Mag | ΔMag | Change
    """
    # Optionally hide fully flat positions with zero size
    # BUT show flat positions when they have a change_type (e.g., CLOSE) - show them at least once
    rows_to_show = []
    for r in summary_rows:
        if hide_flat and r["net_side"] == "flat" and r["total_magnitude"] == 0:
            # Show flat positions if they have a change_type (they're being processed)
            change_type = r.get("change_type")
            if change_type and change_type in ["CLOSE", "NEW", "ADD", "TRIM", "FLIP"]:
                rows_to_show.append(r)
            # Skip other flat positions
        else:
            rows_to_show.append(r)

    # Column definitions (order is easy to change)
    COLUMN_SPECS = [
        {"header": "Trader",  "field": "trader",           "width": 25, "align": "<"},
        {"header": "LT",      "field": "is_long_term",     "width": 3,  "align": "<"},
        {"header": "Symbol",  "field": "symbol",           "width": 30, "align": "<"},
        {"header": "Type",    "field": "instrument_type",  "width": 8,  "align": "<"},
        {"header": "Side",    "field": "net_side",         "width": 6,  "align": "<"},
        {"header": "Mag",     "field": "total_magnitude",  "width": 6,  "align": ">"},
        {"header": "MagChg",    "field": "delta_magnitude",  "width": 6,  "align": ">"},
        {"header": "Type",  "field": "change_type",      "width": 8,  "align": "<"},
    ]

    def format_cell(value, spec):
        """ format one cell value according to spec 
        """
    # special cases
        if spec["field"] == "is_long_term":
            return "LT" if value else "  "
        if spec["field"] == "change_type" and value is None:
            return " " * spec["width"]
        if value is None:
            return "NA"
        return f"{str(value):{spec['align']}{spec['width']}}"
    def build_header_line():
        """Build the header row using the column specs."""
        cells = [f"{col['header']:{col['align']}{col['width']}}" for col in COLUMN_SPECS]
        return " ".join(cells)


    def build_divider():
        """ build divider line using COLUMN_SPECS"""
        total_width = sum(col['width'] + 1 for col in COLUMN_SPECS) - 1  # account for spaces
        return "-" * total_width
    

#     # sort by trader, the LT flag, then symbol
#     rows_to_show.sort(key=lambda r: (r["trader"], r["is_long_term"], r["symbol"]))
#    # header
#     header = "{:<25} {:<3} {:<30} {:<8} {:<6} {:>6}".format(
#     "Trader", "LT", "Symbol", "Type", "Side", "Mag"
#     )
#     line = "-" * len(header)
    # Print header

    print(build_header_line())
    print(build_divider())


    # Print rows
    for r in rows_to_show:
        cells = []
        for spec in COLUMN_SPECS:
            value = r.get(spec["field"], "")
            cells.append(format_cell(value, spec))
        print(" ".join(cells))




def run_single_cycle(session=None, ib=None):
    # Log in and create session and fetch positions
    if session is None:
        session = get_session()
    positions_data = fetch_positions(session)
    
    # Normalize all raw records
    normalized_positions = [normalize_record(r) for r in positions_data]
    
    # group
    groups = defaultdict(list)
    for p in normalized_positions:
        key = (p["trader"], p["is_long_term"], p["symbol_raw"])
        groups[key].append(p)
    
    # ***************build summary_rows from groups from def summarize_group(records)***************
    summary_rows = [summarize_group(recs) for recs in groups.values()]
    
    #load Previoius Snapshot    
    previous_snapshot = load_snapshot()
    
    if previous_snapshot is None:
        print(f"No previous snapshot found at {SNAPSHOT_FILE}")
    else:
        # print (f"Loaded previous snapshot from {SNAPSHOT_FILE}, {len(previous_snapshot)} records.")
    
    # *************** Annotate current summary with prev/delta/change_type ****************
        summary_rows = annotate_with_changes(summary_rows, previous_snapshot)
    
    # Get IB connection if needed (for market data and execution tracking)
    # Always try to connect for market data, even if trading is disabled
    if ib is None:
        ib = get_ib_connection()
        if ib is None:
            print("Warning: IB connection unavailable - market data will not be retrieved")
    
    # Process execution changes (NEW, ADD, TRIM, CLOSE, FLIP)
    change_types_to_process = ["NEW", "ADD", "TRIM", "CLOSE", "FLIP"]
    for row in summary_rows:
        change_type = row.get("change_type")
        if change_type in change_types_to_process:
            process_execution_change(ib, row, change_type)
    
    save_snapshot(summary_rows)

    # look for conflicts
    conflicts = [r for r in summary_rows if r["conflict"]]
    print("\nConflicts detected:", len(conflicts))
    for c in conflicts:
        print("CONFLICT:", c["trader"], c["symbol"])
    
    # filter summary_rows to not inlcude trader name Steven Wang and then sort by trader name
    summary_rows = [r for r in summary_rows if r["trader"] != "Steven Wang"]
    
    trader_order = {
        "Justin Spero": 0,
        "Jeff Holden": 1,
        "Steve Spencer": 2,
        "Kenneth Sharkness": 3,
    }
    # Sort by: trader order (primary), is_long_term (non-LT first, then LT), magnitude descending
    summary_rows.sort(key=lambda r: (
        trader_order.get(r["trader"], 99),
        r["is_long_term"],  # False (non-LT) comes before True (LT)
        -r.get("total_magnitude", 0)  # descending order (negate to reverse)
    ))
    
    # Print the final position table
    # print("\n== table of current positions ==")
    print_position_table(summary_rows, hide_flat=True)
    return session, summary_rows, ib

    # Periodically check session validity
    # while True:
    #     ok = is_session_valid(session)
    #     print(time.strftime("%H:%M:%S"), "- session valid:", ok)

    #     if not ok:
    #         print("Looks like the session expired.")
    #         break

    #     time.sleep(30)  # wait 30 seconds between checks

#polling configuration, either once, polling internal, or off
def run_once_mode():
    # Polling configuration, either once, polling interval, or off
    print("Running in once mode")
    ib = None
    try:
        session, _, ib = run_single_cycle(session=None, ib=None)
    finally:
        if ib is not None:
            close_ib_connection()

def run_polling_mode(interval_seconds):
    #placeholder
    print(f"Running in polling mode every {interval_seconds} seconds")
    session = None
    ib = None
    try:
        while True:
            try:
                session, _, ib = run_single_cycle(session=session, ib=ib)
                time.sleep(interval_seconds)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                # Handle connection errors (e.g., after computer sleep/wake)
                print(f"Connection error detected: {type(e).__name__}")
                print("Recreating session and retrying...")
                session = None  # Force session recreation on next cycle
                time.sleep(5)  # Brief pause before retry
                continue
    except KeyboardInterrupt:
        print("Polling mode interrupted by user.")
    finally:
        if ib is not None:
            close_ib_connection()

def main():
    if RUN_MODE =="once":
        run_once_mode()
    elif RUN_MODE =="poll":
        run_polling_mode(INTERVAL_SECONDS)
    else:
        print("RUN_MODE is 'off', exiting.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped by user.")

