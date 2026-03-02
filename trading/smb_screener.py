# =========================================================
# Purpose: This script is used to screen for positions from the SMB API 
# and execute trades in IB (interactive brokers live account). For educational purposes only. 
# =========================================================
import os
import threading
from pathlib import Path
from dotenv import load_dotenv
import requests
import re
from collections import defaultdict
import json
import time
import pickle
import traceback
from datetime import datetime, date
import csv
# from bs4 import BeautifulSoup dont need this since it's JSON

# IB imports - need event loop setup before importing ib_async
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_async import IB  # noqa: E402

from trading.config import (  # noqa: E402
    ACTIVE_TRADING,
    DAILY_STOP,
    IB_HOST,
    IB_PORT,
    INTERVAL_SECONDS,
    RUN_MODE,
    STOP_OFFSET,
    TRADER_ENABLED,
)
from trading.ib_trading import (  # noqa: E402
    cancel_all_orders_for_position,
    calculate_num_shares_from_risk,
    send_bracket_order,
    send_entry_only_order,
    send_market_order,
    send_scaling_order,
    update_child_orders_for_position,
)
from trading.trade_data import (  # noqa: E402
    get_available_funds,
    get_position_size,
    has_open_orders,
    has_open_orders_for_trader,
)
from trading.market_data import (  # noqa: E402
    calculate_adr,
    calculate_gap_percentage,
    calculate_trailing_stop,
    diagnose_market_price,
    get_market_price,
    get_todays_range,
)
from trading.models import NormalizedRecord, PositionSummary  # noqa: E402


# =========================================================
# Environment and constants
# =========================================================
load_dotenv()

SMB_USERNAME = os.getenv('SMB_USERNAME')
SMB_PASSWORD = os.getenv('SMB_PASSWORD')


if not SMB_USERNAME or not SMB_PASSWORD:
    raise ValueError('Missing SMB_USERNAME or SMB_PASSWORD in .env')

# session = requests.Session() # creating an instance (object) of the class requests.Session


# =========================================================
# Auth / HTTP constants
# =========================================================
CSRF_URL = 'https://rt.smbtraining.com/api/auth/csrf'
LOGIN_URL = 'https://rt.smbtraining.com/api/auth/callback/credentials'
CALLBACK_URL = 'https://rt.smbtraining.com/auth/signin?callbackUrl=https%3A%2F%2Frt.smbtraining.com%2Fcalendar'
SESSION_URL = 'https://rt.smbtraining.com/api/auth/session'
POSITIONS_URL = 'https://rt.smbtraining.com/api/external-positions'
# Paths relative to repo root so they are consistent regardless of cwd
_REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_FILE = str(_REPO_ROOT / 'resources' / 'positions' / 'position_snapshot.json')
COOKIES_FILE = str(_REPO_ROOT / 'resources' / 'cookies' / 'smb_cookies.pkl')
EXECUTIONS_DIR = str(_REPO_ROOT / 'smb_trader_executions')

# Auth / HTTP helpers
def create_authenticated_session() -> requests.Session:
    """Create a logged-in requests.Session and return it. called when no valid
    session exists from get_session using smb_ookies.pkl
    """
    session = requests.Session()  # creating an instance (object) of the class requests.Session

    csrf_resp = session.get(CSRF_URL)
    csrf_data = csrf_resp.json()
    csrf_token = csrf_data.get('csrfToken')
    if not csrf_token:
        raise RuntimeError('No csrfToken in CSRF response')

    payload = {
        'email': SMB_USERNAME,       # matches DevTools
        'password': SMB_PASSWORD,    # from .env, request for access
        'redirect': 'false',         # string, just like DevTools
        'csrfToken': csrf_token,
        'callbackUrl': CALLBACK_URL, # hardcoded for now
        'json': 'true',
    }
    # optional but can help: send the same Origin/Referer as browser
    headers = {
        'Origin': 'https://rt.smbtraining.com',
        'Referer': CALLBACK_URL,
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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

def is_session_valid(session: requests.Session) -> bool:
    resp = session.get(SESSION_URL)
    if not resp.ok:
        print('Session check status:', resp.status_code)
        return False

    data = resp.json()
    # print("Session check payload:", data) # will print the session URL data if session is not valid
    # if logged out, this might be {} instead of user details
    return bool(data)

def fetch_positions(session: requests.Session) -> dict: 
    """Fetch raw positions JSON using an already authenticated session.
    session (requests.Session_): the session whose cookies to save via path (str) file path where we write the cookies.
    """
    resp = session.get(POSITIONS_URL)
    resp.raise_for_status()
    return resp.json()

# funcions to save/load cookies
def save_cookies(session: requests.Session, path: str = COOKIES_FILE) -> None:
    """Save the session cookies to a file using pickle.
    session (requests.Session_): the session whose cookies to save via path (str) file path where we write the cookies.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('wb') as f:
        pickle.dump(session.cookies, f) # pickle.dump(obj, file) this serializes 
        # the cookies object to bytes & writes to disk

def load_cookies(session: requests.Session, path: str = COOKIES_FILE) -> bool:
    """
    Load cookies from disk into the given session, if the cookie file exists.

    Parameters_:
        session (requests.Session): the session we want to attach cookies to.
        path (str): file path where the cookies are stored.
        
    Returns_:
        bool: True if cookies were loaded, False if file did not exist.
    """
    if not Path(path).exists():
        return False

    with Path(path).open('rb') as f:
        loaded_cookies = pickle.load(f)

    # session.cookies is a RequestsCookieJar; update merges cookies in
    session.cookies.update(loaded_cookies)
    return True

def get_session() -> requests.Session:
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
            print('Loaded cookies but session is invalid, performing fresh login.')
    else:
        print('No cookies loaded, performing fresh login.')
    # perform fresh login
    fresh_session = create_authenticated_session()
    save_cookies(fresh_session)
    # save cookies from the newly athenticated session
    return fresh_session


# =========================================================
# IB Connection and Market Data
# =========================================================
# Client IDs 2 and 3 are reserved (market_data standalone, check_trade). We rotate among
# 1, 4, 5, 6, 7, 8, 9, 10 on reconnect so TWS doesn't reject us with "client id already
# in use" when the previous session hasn't been released yet (e.g. after disconnect).

_ib_connection: IB | None = None  # Module-level IB connection
_ib_connect_lock = threading.Lock()
_SCREENER_CLIENT_IDS = (1, 4, 5, 6, 7, 8, 9, 10)  # skip 2, 3
_ib_reconnect_attempt = 0

def reset_ib_connection():
    """Reset the IB connection, forcing a reconnect on next use."""
    global _ib_connection
    if _ib_connection is not None:
        try:
            if _ib_connection.isConnected():
                _ib_connection.disconnect()
        except Exception:
            pass  # Ignore errors when disconnecting a broken connection
    _ib_connection = None

def get_ib_connection() -> IB | None:
    """
    Get or create a persistent IB connection.
    Establishes connection for market data even if ACTIVE_TRADING is False.
    Returns None if connection fails (allows script to continue without trading).
    Uses rotating client IDs on reconnect to avoid 'client id already in use' when
    TWS hasn't released the previous session yet.
    """
    global _ib_connection, _ib_reconnect_attempt

    # Return existing connection if valid - but verify it's actually working
    if _ib_connection is not None:
        try:
            if _ib_connection.isConnected():
                # Check if the client socket is still valid by checking the connection state
                # This catches cases where isConnected() returns True but socket is broken
                if _ib_connection.client.isConnected():
                    return _ib_connection
                else:
                    # Client reports disconnected
                    reset_ib_connection()
            else:
                # Connection reports as disconnected
                reset_ib_connection()
        except (ConnectionError, OSError, AttributeError, Exception) as e:
            # Connection is broken, reset it
            print(f'IB connection lost: {type(e).__name__}: {e}')
            reset_ib_connection()

    # Create new connection: one attempt at a time, rotating client ID to avoid
    # "client id already in use" when reconnecting before TWS released the old session
    with _ib_connect_lock:
        # Re-check after acquiring lock (another thread may have connected)
        if _ib_connection is not None and _ib_connection.isConnected():
            try:
                if _ib_connection.client.isConnected():
                    return _ib_connection
            except Exception:
                pass
            reset_ib_connection()

        client_id = _SCREENER_CLIENT_IDS[_ib_reconnect_attempt % len(_SCREENER_CLIENT_IDS)]
        _ib_reconnect_attempt += 1
        print(f'Attempting IB connection to {IB_HOST}:{IB_PORT} with client ID {client_id}...')
        try:
            ib = IB()
            readonly_mode = not ACTIVE_TRADING
            ib.connect(IB_HOST, IB_PORT, clientId=client_id, readonly=readonly_mode)
            if ib.isConnected():
                _ib_connection = ib
                mode_str = 'readonly' if readonly_mode else 'trading'
                print(f'✓ IB connected: {IB_HOST}:{IB_PORT} ({mode_str} mode, client ID {client_id})')
                if readonly_mode:
                    print('⚠️  Orders will not be sent: connection is readonly (ACTIVE_TRADING is False). Set ACTIVE_TRADING = True and restart to enable trading.')
                return ib
            print('✗ Warning: IB connection failed - isConnected() returned False')
            print('  Check TWS/Gateway: API enabled, correct port, and "Allow localhost" or this machine in Trusted IPs')
            _ib_connection = None
            return None
        except Exception as e:
            print(f'✗ Warning: IB connection error: {e}')
            print('  Check TWS/Gateway: API enabled, correct port, and "Allow localhost" or this machine in Trusted IPs')
            traceback.print_exc()
            _ib_connection = None
        return None

def close_ib_connection():
    """Close the IB connection if it exists."""
    reset_ib_connection()
    print('IB connection closed')

# =========================================================
# normalization and summarization
def normalize_record(rec: dict) -> NormalizedRecord:
    """
    Convert a raw external-positions record into a NormalizedRecord.

    Extracts trader name, long-term flag, symbol info (equity vs option),
    side (long/short/flat), and magnitude.
    """
    account_name = rec['account_name']  # e.g. "VC Jeff Holden" or "VC Justin Spero LT"

    # Strip leading "VC " if present
    if account_name.startswith('VC '):
        core = account_name[3:]
    else:
        core = account_name

    # Long-term flag if name ends with " LT"
    is_long_term = core.endswith(' LT')
    if is_long_term:
        trader_name = core[:-3]  # drop trailing " LT"
    else:
        trader_name = core

    symbol_raw = rec['symbol']       # e.g. "AMD" or "ETHA 2025-11-21 P 23.00"
    side = rec['side'].lower()      # "long" "short" "flat"
    magnitude = rec['magnitude']

    # Try to detect options of the form: "TICKER YYYY-MM-DD C/P STRIKE"
    # Example: "ETHA 2025-11-21 P 23.00"
    opt_match = re.match(r'^([A-Z]+)\s+(\d{4}-\d{2}-\d{2})\s+([CP])\s+([\d.]+)$', symbol_raw)

    if opt_match:
        underlying, expiry, opt_type, strike_str = opt_match.groups()
        instrument_type = 'option'
        strike = float(strike_str)
    else:
        underlying = symbol_raw
        expiry = None
        opt_type = None
        strike = None
        instrument_type = 'equity'

    # Normalize side into long/short/flat
    if side not in ('long', 'short', 'flat'):
        normalized_side = 'unknown'
    else:
        normalized_side = side

    return NormalizedRecord(
        trader=trader_name,
        is_long_term=is_long_term,
        symbol_raw=symbol_raw,
        side=normalized_side,
        magnitude=magnitude,
        last_updated=rec['last_updated'],
        created_at=rec['created_at'],
        instrument_type=instrument_type,
        underlying=underlying,
        expiry=expiry,
        strike=strike, # "C" or "P" for options
        option_type=opt_type,# full SMB account name
    )

    
def summarize_group(records: list[NormalizedRecord]) -> PositionSummary:
    """
    Determine the trader's *net* position for one symbol.

    Args:
        records: The list of normalized records for one (trader, symbol) group.
    """
    has_long = any(r.side == 'long' and r.magnitude > 0 for r in records)
    has_short = any(r.side == 'short' and r.magnitude > 0 for r in records)

    if has_long and has_short:
        net_side = 'conflict'
        conflict = True
    elif has_long:
        net_side = 'long'
        conflict = False
    elif has_short:
        net_side = 'short'
        conflict = False
    else:
        net_side = 'flat'
        conflict = False
    base = records[0]
    return PositionSummary(
        trader=base.trader,
        is_long_term=base.is_long_term,
        symbol=base.symbol_raw,
        instrument_type=base.instrument_type,
        underlying=base.underlying,
        expiry=base.expiry,
        strike=base.strike,
        option_type=base.option_type,
        net_side=net_side,
        conflict=conflict,
        total_magnitude=sum(r.magnitude for r in records),
    )





def save_snapshot(summary_rows: list[PositionSummary], path: str = SNAPSHOT_FILE) -> None:
    """
    Save the current summarized positions to disk as JSON.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('w', encoding='utf-8') as f:
        json.dump([r.to_dict() for r in summary_rows], f, indent=2)


def load_snapshot(path: str = SNAPSHOT_FILE) -> list[PositionSummary] | None:
    """
    Load a previously saved snapshot of summarized positions from disk.
    """
    if not Path(path).exists():
        print(f'Snapshot file not found: {path}')
        return None
    with Path(path).open('r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        print(f'Snapshot file has invalid format (expected list of dicts): {path}')
        return None
    return [PositionSummary.from_dict(row) for row in data]


# =========================================================
# Execution Tracking (CSV)
# =========================================================

def format_timestamp(dt: datetime | None = None) -> str:
    """
    Format datetime as a database and Excel-friendly timestamp string.
    
    Format: YYYY-MM-DD HH:MM:SS (space-separated, seconds precision)
    This format is:
    - Recognized by Excel when importing CSV
    - Compatible with most databases (PostgreSQL, MySQL, SQLite, etc.)
    - Sortable and filterable in both Excel and databases
    
    Args:
        dt: datetime object (defaults to current time if None)
    
    Returns_:
        str: Formatted timestamp string
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def ensure_executions_dir():
    """Ensure the executions directory exists."""
    Path(EXECUTIONS_DIR).mkdir(parents=True, exist_ok=True)


def get_executions_filename() -> str:
    """Get the executions CSV filename for today."""
    today = date.today()
    filename = f"executions_{today.strftime('%Y-%m-%d')}.csv"
    return str(Path(EXECUTIONS_DIR) / filename)

def save_execution_to_csv(
    trader: str,
    symbol: str,
    change_type: str,
    net_side: str,
    delta_magnitude: float,
    entry_price: float | None = None,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    order_id: str | None = None,
    timestamp: str | None = None
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
    file_exists = Path(filename).exists()
    
    # CSV columns: timestamp, trader, symbol, change_type, net_side, delta_magnitude, 
    #              entry_price, stop_price, take_profit_price, order_id
    fieldnames = [
        'timestamp', 'trader', 'symbol', 'change_type', 'net_side', 'delta_magnitude',
        'entry_price', 'stop_price', 'take_profit_price', 'order_id'
    ]
    
    with Path(filename).open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        
        writer.writerow({
            'timestamp': timestamp,
            'trader': trader,
            'symbol': symbol,
            'change_type': change_type,
            'net_side': net_side,
            'delta_magnitude': delta_magnitude,
            'entry_price': entry_price if entry_price is not None else '',
            'stop_price': stop_price if stop_price is not None else '',
            'take_profit_price': take_profit_price if take_profit_price is not None else '',
            'order_id': order_id if order_id is not None else '',
        })



def process_execution_change(
    ib: IB | None,
    row: PositionSummary,
    change_type: str
) -> None:
    """
    Process a position change and execute orders if active_trading is enabled.
    
    Args:
        ib: IB connection (None if not connected)
        row: Position summary row with change annotations
        change_type: Change type (NEW, ADD, TRIM, CLOSE)
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
                    
                    # Initialize adjusted magnitude (may be reduced for large gap up positions)
                    adjusted_magnitude = abs(delta_magnitude)
                    
                    # For NEW positions, check gap percentage and adjust position size if gap > 99%
                    if change_type == 'NEW':
                        gap_percentage = calculate_gap_percentage(ib, underlying, entry_price)
                        if gap_percentage and gap_percentage > 99:
                            adjusted_magnitude = abs(delta_magnitude) / 10
                            print(f'⚠️  WARNING: {underlying} gapped up {gap_percentage:.2f}% (>99%) - reducing position size from {abs(delta_magnitude)} to {adjusted_magnitude:.2f}')
                    
                    # First, check if ADR is available (required for take profit)
                    adr = calculate_adr(ib, underlying)
                    
                    if not adr:
                        # ADR not available - cannot use trailing stop, will send entry-only order
                        print(f'⚠️  WARNING: ADR not available for {underlying} - cannot calculate take profit, will send entry-only order')
                        stop_price = None
                        take_profit_price = None
                    else:
                        # ADR available - now try trailing stop as primary
                        position_side_str = 'long' if is_long else 'short'
                        trailing_stop = calculate_trailing_stop(ib, underlying, prior_bars=3, position_side=position_side_str)
                        
                        if trailing_stop:
                            # PRIMARY: Use trailing stop (15-min bar lows/highs)
                            stop_price = round(trailing_stop, 2)
                            print(f'✓ Using trailing stop for {underlying}: ${stop_price:.2f}')
                        else:
                            # No completed 15-min bar: use day's low (long) or day's high (short) with $0.02 offset
                            todays_range = get_todays_range(ib, underlying)
                            if todays_range:
                                day_low, day_high = todays_range.low, todays_range.high
                                if is_long:
                                    stop_price = round(day_low - STOP_OFFSET, 2)
                                else:
                                    stop_price = round(day_high + STOP_OFFSET, 2)
                                print(f'✓ Using day range stop for {underlying}: ${stop_price:.2f} (day low ${day_low:.2f} / high ${day_high:.2f}, ${STOP_OFFSET:.2f} offset)')
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
                                take_profit_price = entry_price + (1.0 * adr)
                            else:
                                take_profit_price = entry_price - (1.0 * adr)
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
                                    # Check if we have both stop price and take profit (ADR required)
                                    if stop_price and take_profit_price:
                                        order_id = send_bracket_order(
                                            ib, underlying, is_long, entry_price,
                                            stop_price, take_profit_price, adjusted_magnitude, trader
                                        )
                                    else:
                                        # No stop available or ADR failed - send entry-only order with warning
                                        if not stop_price:
                                            print(f'⚠️  WARNING: No stop loss available for {underlying} (trailing stop and ADR both failed)')
                                        else:
                                            print(f'WARNING: ADR not available for {underlying} - cannot calculate take profit, sending entry-only order')
                                        order_id = send_entry_only_order(
                                            ib, underlying, is_long, entry_price, adjusted_magnitude, trader
                                        )
                                        # Set stop_price to None for CSV logging
                                        stop_price = None
                                        take_profit_price = None
                            # For ADD changes, check if we already have a position
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
                                            print(f'Using assumed 2% stop for scaling order sizing: ${scaling_stop_price:.2f}')
                                    
                                    if available_funds <= 0:
                                        no_place_reason = 'no_available_funds'
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
                                                    no_place_reason = 'open_order_already_exists (ADD scaling)'
                                                    print(f'Skipping ADD scaling order for {underlying}: {no_place_reason}')
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
                                                no_place_reason = 'add_child_orders_updated_no_new_order'
                                                stop_price = None
                                                take_profit_price = None
                                        else:
                                            no_place_reason = 'num_shares_to_add_zero'
                                else:
                                    # No existing position, check for open orders before creating new bracket
                                    has_open_order = has_open_orders(ib, underlying, is_long)
                                    if has_open_order:
                                        no_place_reason = 'open_order_already_exists (ADD bracket)'
                                        print(f'Skipping ADD bracket order for {underlying}: {no_place_reason}')
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
                                                print(f' WARNING: No stop loss available for {underlying} (trailing stop and ADR both failed)')
                                            else:
                                                print(f'WARNING: ADR not available for {underlying} - cannot calculate take profit, sending entry-only order')
                                            order_id = send_entry_only_order(
                                                ib, underlying, is_long, entry_price, abs(delta_magnitude), trader
                                            )
                                            # Set stop_price to None for CSV logging
                                            stop_price = None
                                            take_profit_price = None
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
                print(f'Recording {change_type} for {underlying} ({trader}) with no order_id (skipped or order placement failed)')
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
                        exit_size = abs(int(current_position * (abs(delta_magnitude) / 100.0)))
                        
                        if exit_size > 0 and ACTIVE_TRADING:
                            # Check if the TRIM would result in closing the position
                            # If exit_size >= abs(current_position), we should CLOSE instead
                            if exit_size >= abs(current_position):
                                print(f'⚠️  TRIM ({exit_size} shares) >= position size ({abs(current_position)} shares) - converting to CLOSE')
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
                cancelled_count = cancel_all_orders_for_position(ib, underlying, trader)
                
                # Get current position size from IB
                current_position = get_position_size(ib, underlying)
                print(f'   Current position in IB: {current_position} shares')
                if current_position != 0:
                    # Exit entire position
                    is_long = current_position > 0
                    exit_size = abs(current_position)
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

def make_position_key(row: PositionSummary) -> tuple[str, bool, str, str]:
    """Create a unique key for a position summary row based on trader, LT flag, symbol, and side."""
    return (row.trader, row.is_long_term, row.symbol, row.net_side)


def make_position_key_no_side(row: PositionSummary) -> tuple[str, bool, str]:
    """Create a key for a position without the side (used to detect position closures)."""
    return (row.trader, row.is_long_term, row.symbol)


def _inject_closed_position_rows(
    current_rows: list[PositionSummary],
    previous_snapshot: list[PositionSummary] | None,
) -> list[PositionSummary]:
    """
    When a trader exits a position, the API omits that symbol (no row returned).
    Add synthetic flat rows for (trader, symbol) that were in the previous
    snapshot (non-flat) but missing from current so we detect CLOSE and place
    the exit order.
    """
    if not previous_snapshot:
        return current_rows
    current_keys = {make_position_key_no_side(row) for row in current_rows}
    synthetic: list[PositionSummary] = []
    for prev in previous_snapshot:
        if prev.net_side == 'flat' or (prev.total_magnitude or 0) <= 0:
            continue
        key = make_position_key_no_side(prev)
        if key in current_keys:
            continue
        synthetic.append(PositionSummary(
            trader=prev.trader,
            is_long_term=prev.is_long_term,
            symbol=prev.symbol,
            instrument_type=prev.instrument_type,
            underlying=prev.underlying or prev.symbol,
            expiry=prev.expiry,
            strike=prev.strike,
            option_type=prev.option_type,
            net_side='flat',
            conflict=False,
            total_magnitude=0,
        ))
    return current_rows + synthetic


def annotate_with_changes(
    current_rows: list[PositionSummary],
    previous_snapshot: list[PositionSummary] | None,
) -> list[PositionSummary]:
    """
    Given the current summary rows and the previously saved snapshot,
    enrich each row with prev_magnitude, delta_magnitude, and change_type.
    """
    if previous_snapshot is None:
        for row in current_rows:
            row.prev_magnitude = 0
            row.delta_magnitude = row.total_magnitude
            row.change_type = 'NEW' if row.total_magnitude != 0 else 'NONE'
        return current_rows
    prev_index: dict[tuple[str, bool, str, str], PositionSummary] = {}
    for prev_row in previous_snapshot:
        key = make_position_key(prev_row)
        prev_index[key] = prev_row
    prev_index_no_side: dict[tuple[str, bool, str], list[PositionSummary]] = {}
    for prev_row in previous_snapshot:
        key_no_side = make_position_key_no_side(prev_row)
        if key_no_side not in prev_index_no_side:
            prev_index_no_side[key_no_side] = []
        prev_index_no_side[key_no_side].append(prev_row)
    for row in current_rows:
        key = make_position_key(row)
        prev_row = prev_index.get(key)
        prev_mag = prev_row.total_magnitude if prev_row else 0
        curr_mag = row.total_magnitude
        prev_side = prev_row.net_side if prev_row else 'flat'
        curr_side = row.net_side
        row.prev_magnitude = prev_mag
        row.delta_magnitude = curr_mag - prev_mag
        if prev_side != 'flat' and curr_side == 'flat':
            change = 'CLOSE'
        elif prev_row is None and curr_mag > 0:
            change = 'NEW'
        elif prev_row is None and curr_mag == 0:
            key_no_side = make_position_key_no_side(row)
            prev_rows_same_symbol = prev_index_no_side.get(key_no_side, [])
            had_non_flat_position = any(
                p.net_side != 'flat' and p.total_magnitude > 0 for p in prev_rows_same_symbol
            )
            change = 'CLOSE' if had_non_flat_position else 'FLAT'
        elif prev_side != curr_side and prev_side != 'flat' and curr_side != 'flat':
            change = 'FLIP'
        elif (row.delta_magnitude or 0) > 0:
            change = 'ADD'
        elif (row.delta_magnitude or 0) < 0:
            change = 'TRIM'
        else:
            change = None
        row.change_type = change
    return current_rows

def _get_field(r: PositionSummary, field: str) -> str | int | float | bool | None:
    """Get field value from PositionSummary by name."""
    return getattr(r, field, '')


def print_position_table(summary_rows: list[PositionSummary], hide_flat: bool = True) -> None:
    """Print a table of positions: Trader | LT | Symbol | Type | Side | Mag | MagChg | Change."""
    rows_to_show: list[PositionSummary] = []
    for r in summary_rows:
        if hide_flat and r.net_side == 'flat' and r.total_magnitude == 0:
            if r.change_type and r.change_type in ['CLOSE', 'NEW', 'ADD', 'TRIM', 'FLIP']:
                rows_to_show.append(r)
        else:
            rows_to_show.append(r)
    column_specs = [
        {'header': 'Trader',  'field': 'trader',           'width': 25, 'align': '<'},
        {'header': 'LT',      'field': 'is_long_term',     'width': 3,  'align': '<'},
        {'header': 'Symbol',  'field': 'symbol',           'width': 30, 'align': '<'},
        {'header': 'Type',    'field': 'instrument_type',  'width': 8,  'align': '<'},
        {'header': 'Side',    'field': 'net_side',         'width': 6,  'align': '<'},
        {'header': 'Mag',     'field': 'total_magnitude',  'width': 6,  'align': '>'},
        {'header': 'MagChg',  'field': 'delta_magnitude',  'width': 6,  'align': '>'},
        {'header': 'Change',  'field': 'change_type',      'width': 8,  'align': '<'},
    ]

    def format_cell(value: str | int | float | bool | None, spec: dict) -> str:
        if spec['field'] == 'is_long_term':
            return 'LT' if value else '  '
        if spec['field'] == 'change_type' and value is None:
            return ' ' * spec['width']
        if value is None:
            return 'NA'
        return f'{str(value):{spec["align"]}{spec["width"]}}'

    def build_header_line() -> str:
        return ' '.join(f'{col["header"]:{col["align"]}{col["width"]}}' for col in column_specs)

    def build_divider() -> str:
        return '-' * (sum(int(col['width']) + 1 for col in column_specs) - 1)

    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print(build_header_line())
    print(build_divider())
    for r in rows_to_show:
        cells = [format_cell(_get_field(r, str(spec['field'])), spec) for spec in column_specs]
        print(' '.join(cells))




def run_single_cycle(
    session: requests.Session | None = None,
    ib: IB | None = None,
) -> tuple[requests.Session, list[PositionSummary], IB | None]:
    """Log in and create session and fetch positions."""
    if session is None:
        session = get_session()
    positions_data = fetch_positions(session)
    
    # Normalize all raw records
    normalized_positions = [normalize_record(r) for r in positions_data]
    
    # group
    groups: defaultdict[tuple[str, bool, str], list[NormalizedRecord]] = defaultdict(list)
    for p in normalized_positions:
        key = (p.trader, p.is_long_term, p.symbol_raw)
        groups[key].append(p)
    
    # ***************build summary_rows from groups from def summarize_group(records)***************
    summary_rows = [summarize_group(recs) for recs in groups.values()]
    
    # Load previous snapshot
    previous_snapshot = load_snapshot()

    if previous_snapshot is None:
        print(f'No previous snapshot found at {SNAPSHOT_FILE}')
    else:
        # Inject synthetic flat rows for symbols that disappeared from the API
        # (trader exited) so we detect CLOSE and place the exit order
        summary_rows = _inject_closed_position_rows(summary_rows, previous_snapshot)

    # Annotate current summary with prev/delta/change_type
    summary_rows = annotate_with_changes(summary_rows, previous_snapshot)
    
    # Get IB connection if needed (for market data and execution tracking)
    # Always try to connect for market data, even if trading is disabled
    if ib is None:
        ib = get_ib_connection()
        if ib is None:
            print('Warning: IB connection unavailable - market data will not be retrieved')
    else:
        # Verify existing connection is still valid
        try:
            if ib is not None and not ib.isConnected():
                ib = get_ib_connection()  # Try to reconnect
        except Exception as e:
            print(f'Warning: IB connection check failed: {e}')
            ib = get_ib_connection()  # Try to reconnect
    
    # Process execution changes (NEW, ADD, TRIM, CLOSE, FLIP)
    change_types_to_process = ['NEW', 'ADD', 'TRIM', 'CLOSE', 'FLIP']
    changes_to_run: list[tuple[PositionSummary, str]] = []
    for row in summary_rows:
        ct = row.change_type
        if ct in change_types_to_process:
            changes_to_run.append((row, ct))
    if changes_to_run:
        print(f'Execution: processing {len(changes_to_run)} change(s) (ACTIVE_TRADING={ACTIVE_TRADING}, IB connected={ib is not None and ib.isConnected() if ib else False})')
        # Refresh positions and open orders from TWS before processing
        # (avoids stale cache after connectivity hiccups like 1100/1102)
        if ib is not None and ib.isConnected():
            try:
                ib.run(ib.reqPositionsAsync())
                if ACTIVE_TRADING:
                    ib.run(ib.reqOpenOrdersAsync())
            except Exception as e:
                print(f'Warning: Failed to refresh positions/orders from TWS: {e}')
    for row, change_type in changes_to_run:
        process_execution_change(ib, row, change_type)
    
    # Always persist full snapshot so we never retry by omitting; diagnose real causes when orders aren't placed.
    save_snapshot(summary_rows)

    # look for conflicts
    conflicts = [r for r in summary_rows if r.conflict]
    print('\nConflicts detected:', len(conflicts))
    for c in conflicts:
        print('CONFLICT:', c.trader, c.symbol)
    summary_rows = [r for r in summary_rows if r.trader != 'Steven Wang']
    trader_order = {
        'Justin Spero': 0,
        'Jeff Holden': 1,
        'Steve Spencer': 2,
        'Kenneth Sharkness': 3,
    }
    summary_rows.sort(key=lambda r: (
        trader_order.get(r.trader, 99),
        r.is_long_term,
        -(r.total_magnitude or 0),
    ))
    
    # Print the final position table
    # print("\n== table of current positions ==")
    print_position_table(summary_rows, hide_flat=True)
    return session, summary_rows, ib


#polling configuration, either once, polling internal, or off
def run_once_mode():
    # Polling configuration, either once, polling interval, or off
    print('Running in once mode')
    ib = None
    try:
        session, _, ib = run_single_cycle(session=None, ib=None)
    finally:
        if ib is not None:
            close_ib_connection()

def run_polling_mode(interval_seconds: int):
    #placeholder
    print(f'Running in polling mode every {interval_seconds} seconds')
    session = None
    ib = None
    try:
        while True:
            try:
                session, _, ib = run_single_cycle(session=session, ib=ib)
                time.sleep(interval_seconds)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                # Handle connection errors (e.g., after computer sleep/wake)
                print(f'Connection error detected: {type(e).__name__}')
                print('Recreating session and retrying...')
                session = None  # Force session recreation on next cycle
                time.sleep(2)  # Brief pause before retry
                continue
            except (ConnectionError, OSError) as e:
                # Handle IB connection errors
                print(f'IB connection error detected: {type(e).__name__}: {e}')
                print('Resetting IB connection and retrying...')
                reset_ib_connection()
                ib = None  # Force IB reconnection on next cycle
                time.sleep(2)  # Brief pause before retry
                continue
    except KeyboardInterrupt:
        print('Polling mode interrupted by user.')
    finally:
        if ib is not None:
            close_ib_connection()

def main():
    if RUN_MODE =='once':
        run_once_mode()
    elif RUN_MODE =='poll':
        run_polling_mode(INTERVAL_SECONDS)
    else:
        print('RUN_MODE is \'off\', exiting.')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped by user.')

