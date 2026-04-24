"""UTC clock helpers for incremental bar fetches."""

from datetime import UTC
from datetime import datetime
BAR_SIZE=1

def floor_now_utc_to_interval_minutes(interval_minutes: int) -> datetime:
    """Current UTC time floored to the start of the current interval (minute resolution).

    Used as an exclusive-ish upper bound for “latest closed bar” style backfills.
    """
    if interval_minutes < BAR_SIZE:
        raise ValueError('interval_minutes must be >= 1')

    now = datetime.now(UTC).replace(microsecond=0, second=0)
    floored_minute = (now.minute // interval_minutes) * interval_minutes
    return now.replace(minute=floored_minute)
