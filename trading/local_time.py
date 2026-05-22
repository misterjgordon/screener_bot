"""Local wall clock vs naive US/Eastern for IB bar timestamps.

Local IANA zone: :func:`trading.market_timezones.local_timezone_name` (``LOCAL_TZ`` env,
default from ``market_timezones.yaml``). Exchange zone: :func:`exchange_zone`.

Bar data and ``strategies.utils`` session boundaries stay **naive Eastern**;
``local_wall_to_naive_et`` is the single conversion from local date+time into
that convention. Do not change BarSeries or indicators here.
"""

from datetime import date
from datetime import datetime
from datetime import time
from zoneinfo import ZoneInfo

from trading.market_timezones import exchange_zone
from trading.market_timezones import local_timezone_name
from trading.market_timezones import local_zone


def session_et_zone() -> ZoneInfo:
    """US/Eastern: naive IB bar datetimes are interpreted in this zone."""
    return exchange_zone()


def local_wall_to_naive_et(d: date, t: time) -> datetime:
    """Local calendar date + wall time -> same instant as naive ET datetime."""
    aware_local = datetime.combine(d, t, tzinfo=local_zone())
    return aware_local.astimezone(session_et_zone()).replace(tzinfo=None)
