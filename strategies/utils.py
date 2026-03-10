"""Shared utilities for strategy modules."""

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from typing import Literal

# US equities session bounds (ET). Naive bar datetimes from IB are assumed ET.
# PM: before 9:30, RTH: 9:30-16:00, AH: 16:00+
RTH_START = time(9, 30)
RTH_END = time(16, 0)


def last_trading_day(d: date | None = None) -> date:
    """Most recent weekday on or before d. If d is None, use today (weekends -> Friday)."""
    if d is None:
        d = date.today()
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d -= timedelta(days=1)
    return d


def bar_date(bar_date_value: object) -> date | None:
    """Extract date from bar's date field."""
    if isinstance(bar_date_value, datetime):
        return bar_date_value.date()
    if isinstance(bar_date_value, date):
        return bar_date_value
    return None


def is_session_bar(bar_date_value: object, session_date: date) -> bool:
    """Return True when bar belongs to the given session date."""
    bd = bar_date(bar_date_value)
    return bd == session_date if bd is not None else False


def bar_session(bar_date_value: object) -> Literal['PM', 'RTH', 'AH'] | None:
    """Classify bar datetime as PM (< 9:30), RTH (9:30-16:00), or AH (>= 16:00) ET.

    Returns None for date-only values (e.g. daily bars) or invalid input.
    Assumes naive datetimes from IB are in ET for US equities.
    """
    if not isinstance(bar_date_value, datetime):
        return None
    t = bar_date_value.time()
    if t < RTH_START:
        return 'PM'
    if t < RTH_END:
        return 'RTH'
    return 'AH'


def is_rth_session_bar(bar_date_value: object, session_date: date) -> bool:
    """Return True when bar is on session_date and within RTH (9:30-16:00 ET).

    For datetime bars (e.g. 2-min), filters by time so pre-market bars are excluded.
    For date-only values (e.g. daily bars), only date is checked. Assumes naive
    datetimes from IB are in ET for US equities.
    """
    bd = bar_date(bar_date_value)
    if bd is None or bd != session_date:
        return False
    if isinstance(bar_date_value, datetime):
        t = bar_date_value.time()
        return RTH_START <= t < RTH_END
    return True
