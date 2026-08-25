"""Session label series: classifies each bar as PM, RTH, or AH."""

from zoneinfo import ZoneInfo

import pandas as pd

from strategies.utils import RTH_END
from strategies.utils import RTH_START


def _rth_bounds_minutes() -> tuple[int, int]:
    return (
        RTH_START.hour * 60 + RTH_START.minute,
        RTH_END.hour * 60 + RTH_END.minute,
    )


def session_series(timestamp_utc: pd.Series, timezone: str) -> pd.Series:
    """Classify each bar as PM, RTH, or AH using US equity session bounds in ``timezone``.

    Parameters
    ----------
    timestamp_utc:
        UTC bar open timestamps.
    timezone:
        IANA timezone name (e.g. ``'America/New_York'``).
    """
    tz = ZoneInfo(timezone)
    ts_local = pd.to_datetime(timestamp_utc, utc=True).dt.tz_convert(tz)
    minute = ts_local.dt.hour * 60 + ts_local.dt.minute
    rth_lo, rth_hi = _rth_bounds_minutes()
    labels = pd.Series('AH', index=timestamp_utc.index, dtype='string')
    labels.loc[minute < rth_lo] = 'PM'
    labels.loc[(minute >= rth_lo) & (minute < rth_hi)] = 'RTH'
    return labels
