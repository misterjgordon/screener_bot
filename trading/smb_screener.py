"""
Purpose: This script is used to screen for positions from the SMB API
and execute trades in IB (interactive brokers paper account by default). For educational purposes only.
"""
# IB imports - need event loop setup before importing ib_async
import asyncio
import threading
import time
from collections import defaultdict
from typing import TYPE_CHECKING

import requests

asyncio.set_event_loop(asyncio.new_event_loop())
from ib_async import IB  # noqa: E402

from trading.config import ACTIVE_TRADING  # noqa: E402
from trading.config import IB_HOST  # noqa: E402
from trading.config import IB_PORT  # noqa: E402
from trading.config import INTERVAL_SECONDS  # noqa: E402
from trading.config import RUN_MODE  # noqa: E402
from trading.smb_api import fetch_positions  # noqa: E402
from trading.smb_api import get_session  # noqa: E402
from trading.snapshot_mgmt import SNAPSHOT_FILE  # noqa: E402
from trading.snapshot_mgmt import annotate_with_changes  # noqa: E402
from trading.snapshot_mgmt import inject_closed_position_rows  # noqa: E402
from trading.snapshot_mgmt import load_snapshot  # noqa: E402
from trading.snapshot_mgmt import normalize_record  # noqa: E402
from trading.snapshot_mgmt import save_snapshot  # noqa: E402
from trading.snapshot_mgmt import summarize_group  # noqa: E402
from trading.snapshot_printing import print_position_table  # noqa: E402
from trading.trade_mgmt import process_execution_change  # noqa: E402

if TYPE_CHECKING:
    from trading.models import NormalizedRecord
    from trading.models import PositionSummary

"""
IB Connection
Client ID 2 reserved for market_data standalone. We rotate among
1, 4, 5, 6, 7, 8, 9, 10 on reconnect so TWS doesn't reject us with "client id already
in use" when the previous session hasn't been released yet (e.g. after disconnect).
"""
_ib_connection: IB | None = None  # Module-level IB connection
_ib_connect_lock = threading.Lock()
_SCREENER_CLIENT_IDS = (1, 3, 4, 5, 6, 7, 8, 9, 10)  # skip 2 (market_data)
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
        readonly_mode = not ACTIVE_TRADING

        last_exc: Exception | None = None

        print(f'Attempting IB connection to {IB_HOST}:{IB_PORT} with client ID {client_id}...')
        try:
            ib = IB()
            ib.connect(IB_HOST, IB_PORT, clientId=client_id, readonly=readonly_mode)
            if ib.isConnected():
                _ib_connection = ib
                mode_str = 'readonly' if readonly_mode else 'trading'
                print(f'✓ IB connected: {IB_HOST}:{IB_PORT} ({mode_str} mode, client ID {client_id})')
                if readonly_mode:
                    print(
                        '⚠️  Orders will not be sent: connection is readonly (ACTIVE_TRADING is False). Set ACTIVE_TRADING = True and restart to enable trading.')
                return ib
            print('✗ Warning: IB connection failed - isConnected() returned False')
        except Exception as e:
            last_exc = e
            print(f'✗ Warning: IB connection error: {e}')

        _ib_connection = None
        if last_exc is not None:
            print(f'✗ Warning: IB connection last error: {last_exc}')
        print('  Check TWS/Gateway: API enabled, correct port, and "Allow localhost" or this machine in Trusted IPs')
        return None


def close_ib_connection():
    """Close the IB connection if it exists."""
    reset_ib_connection()
    print('IB connection closed')


def run_single_cycle(
    session: requests.Session | None = None,
    ib: IB | None = None,
) -> tuple[requests.Session, list['PositionSummary'], IB | None]:
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
        summary_rows = inject_closed_position_rows(summary_rows, previous_snapshot)

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
        print(
            f'Execution: processing {
                len(changes_to_run)} change(s) (ACTIVE_TRADING={ACTIVE_TRADING}, IB connected={
                ib is not None and ib.isConnected() if ib else False})')
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


# polling configuration, either once, polling internal, or off
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
    # placeholder
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
    if RUN_MODE == 'once':
        run_once_mode()
    elif RUN_MODE == 'poll':
        run_polling_mode(INTERVAL_SECONDS)
    else:
        print('RUN_MODE is \'off\', exiting.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped by user.')
