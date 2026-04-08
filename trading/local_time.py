"""Local wall clock vs naive US/Eastern for IB bar timestamps.

Use this module for the **local** IANA timezone only. Default is **Vancouver**
(``America/Vancouver``). Another developer can set env ``LOCAL_TZ`` to any
IANA name (e.g. ``Europe/London``).

Bar data and ``strategies.utils`` session boundaries stay **naive Eastern**;
``local_wall_to_naive_et`` is the single conversion from local date+time into
that convention. Do not change BarSeries or indicators here.
"""

import os
from datetime import date
from datetime import datetime
from datetime import time
from zoneinfo import ZoneInfo

DEFAULT_LOCAL_TZ_NAME = 'America/Vancouver'
_ENV_LOCAL_TZ = 'LOCAL_TZ'


def local_timezone_name() -> str:
    """Active local IANA zone: ``LOCAL_TZ`` env or Vancouver."""
    return os.environ.get(_ENV_LOCAL_TZ, DEFAULT_LOCAL_TZ_NAME)


def local_zone() -> ZoneInfo:
    """ZoneInfo for :func:`local_timezone_name`."""
    return ZoneInfo(local_timezone_name())


def session_et_zone() -> ZoneInfo:
    """US/Eastern: naive IB bar datetimes are interpreted in this zone."""
    return ZoneInfo('America/New_York')


def local_wall_to_naive_et(d: date, t: time) -> datetime:
    """Local calendar date + wall time -> same instant as naive ET datetime."""
    aware_local = datetime.combine(d, t, tzinfo=local_zone())
    return aware_local.astimezone(session_et_zone()).replace(tzinfo=None)
