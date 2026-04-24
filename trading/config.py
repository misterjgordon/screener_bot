"""Shared configuration and constants for trading package.

Single source of truth for IB connection, risk, and run settings.
Import in other modules; do not import from market_data or smb_screener here.
"""

import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

# Run: "once" = single run and exit; "poll" = every INTERVAL_SECONDS; "off" = disabled
RUN_MODE = 'poll'
INTERVAL_SECONDS = 10  # SMB updates every 10 seconds

# Browser-like User-Agent for outbound HTTP (SMB login, gameplan, Google Doc export).
# Not hardware-specific; some endpoints expect a typical browser string. Change here
# if you standardize on another profile across machines.
HTTP_BROWSER_USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

ACTIVE_TRADING = True  # Set True to enable order execution to IB
# Per-trader toggle: set True to mirror trades for that trader
TRADER_ENABLED = {
    'Justin Spero': True,
    'Jeff Holden': True,
    'Steve Spencer': False,  # no options display so direction can be misleading
    'Kenneth Sharkness': False,
}

# NEW-order entry pricing policy when no qualifying pattern exists.
NewOrderEntryMode = Literal['quote', 'ema9_fallback']
# mode:
# - quote: existing behavior (long at ask, short at bid)
# - ema9_fallback: prefer 9 EMA, clamped to quote for fillability


@dataclass(frozen=True)
class NewOrderEntryPolicy:
    """Per-trader NEW-order default limit pricing when no pattern override applies."""

    enabled: bool
    mode: NewOrderEntryMode


NEW_ORDER_ENTRY_POLICY_BY_TRADER: dict[str, NewOrderEntryPolicy] = {
    'Justin Spero': NewOrderEntryPolicy(enabled=True, mode='ema9_fallback'),
    'Jeff Holden': NewOrderEntryPolicy(enabled=True, mode='ema9_fallback'),
}

# TWS/Gateway: Configure → API → Settings → enable API, set port. 7497 paper, 7496 live, 4001 Gateway paper
IB_HOST = '127.0.0.1'
IB_PORT_LIVE = 7496
IB_PORT_PAPER = 7497

# Default IB API port for screener, market_data, login launcher, etc. (7497 paper, 7496 live).
IB_PORT = IB_PORT_LIVE

IB_CLIENT_ID = 1  # smb screener (use different ID from jobot)
IB_CLIENT_ID_MARKET_DATA = 2  # market_data standalone/tests (1 reserved for screener)
IB_CLIENT_ID_TRADE_MGMT = 4  # test_trade_mgmt integration tests

# Alpaca: set ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment (.env).
# Paper trading API (orders, account) vs market data API (bars) use different hosts.
ALPACA_API_KEY = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY', '')
ALPACA_PAPER_BASE_URL = os.environ.get(
    'ALPACA_PAPER_BASE_URL',
    'https://paper-api.alpaca.markets',
)
ALPACA_DATA_BASE_URL = os.environ.get(
    'ALPACA_DATA_BASE_URL',
    'https://data.alpaca.markets',
)

DAILY_STOP = 250  # USD - maximum daily loss allowed
# SMB screener default: risk only a fraction of the daily stop per trade sizing.
SCREENER_DAILY_STOP_FRACTION = 0.10
STOP_OFFSET = 0.02  # USD - buffer below day low (long) or above day high (short)
ACCOUNT_CURRENCY = 'USD'

ORDER_TAG = 'SMB'  # Tag for orders placed by this bot
ACTIVE_ORDER_STATUSES = ('PreSubmitted', 'Submitted', 'PendingSubmit', 'PendingCancel', 'ApiPending')

# --- Screener-derived / per-trader risk sizing constants ---
# The screener provides a normalized position magnitude on a 0-100 scale.
# For estimating a trader's intended risk % and sizing shares, we use
# per-trader constants for max daily loss and max per-trade notional.
TRADER_DAILY_STOP_USD: dict[str, float] = {
    'Justin Spero': 50000.0,
    'Jeff Holden': 50000.0,
}

TRADER_MAX_PER_TRADE_VALUE_USD: dict[str, float] = {
    'Justin Spero': 160000.0,
    'Jeff Holden': 160000.0,
}

# --- Derived risk_% settings ---
# Stored as a fraction: 0.10 means 10%.
DEFAULT_RISK_FRACTION = 0.10

# Round UP to nearest 5% step (as a fraction).
# 5% = 0.05
RISK_FRACTION_ROUND_UP_STEP = 0.05

# Persist risk_% with this many decimals (fraction units).
RISK_FRACTION_DECIMALS = 2
