"""Shared configuration and constants for trading package.

Single source of truth for IB connection, risk, and run settings.
Import in other modules; do not import from market_data or smb_screener here.
"""

# =========================================================
# Run configuration
# =========================================================
# "once" -> run the workflow a single time and exit
# "poll" -> keep running every INTERVAL_SECONDS
# "off"  -> do nothing (handy when you temporarily disable the script)
RUN_MODE = 'poll'
INTERVAL_SECONDS = 10  # SMB updates every 10 seconds

# =========================================================
# Active Trading Configuration
# =========================================================
ACTIVE_TRADING = True  # Set True to enable order execution to IB

# Per-trader toggle: set True to mirror trades for that trader.
TRADER_ENABLED = {
    'Justin Spero': True,
    'Jeff Holden': True,
    'Steve Spencer': False, # does not show options so trades can be misleading on his direction
    'Kenneth Sharkness': False,
}

# =========================================================
# IB Connection settings
# =========================================================
# TWS/Gateway: Configure → API → Settings → enable API, set port.
# Use 7497 for TWS paper, 7496 for TWS live, 4001 for IB Gateway paper.
IB_HOST = '127.0.0.1'
IB_PORT = 7496
IB_CLIENT_ID = 1  # smb screener (use different ID from jobot)
IB_CLIENT_ID_MARKET_DATA = 2  # market_data standalone/tests (1 reserved for screener)
IB_CLIENT_ID_CHECK_TRADE = 3  # check_trade / notebooks (avoid conflict with screener)

# =========================================================
# Risk management
# =========================================================
DAILY_STOP = 250  # USD - maximum daily loss allowed
STOP_OFFSET = 0.02  # USD - buffer below day low (long) or above day high (short)
ACCOUNT_CURRENCY = 'USD'

# =========================================================
# Order tagging
# =========================================================
ORDER_TAG = 'SMB'  # Tag for orders placed by this bot
ACTIVE_ORDER_STATUSES = ('PreSubmitted', 'Submitted', 'PendingSubmit', 'PendingCancel', 'ApiPending')
