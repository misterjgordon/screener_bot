"""Daily bar pattern checks used by strategy setup scans."""

from collections.abc import Sequence
from typing import Protocol


class DailyBarLike(Protocol):
    """Protocol for daily bars with gap/volume fields."""

    open: float
    close: float
    volume: float


def _is_gap_up(
    prior_bar: DailyBarLike,
    current_bar: DailyBarLike,
) -> bool:
    """Return True when current day opens above prior day close."""
    return current_bar.open > prior_bar.close


def day_3_gap(bars_daily: Sequence[DailyBarLike]) -> bool:
    """Check for third consecutive gap-up day and day 2 volume expansion.

    Uses the most recent three daily bars in chronological order:
    day_1, day_2, day_3.

    Conditions:
    - day_2 gaps up vs day_1
    - day_3 gaps up vs day_2
    - day_2 volume is at least 1.2x day_1 volume
    - no day_3 volume condition (day_3 volume may be incomplete intraday)
    """
    if len(bars_daily) < 3:
        return False

    day_1, day_2, day_3 = bars_daily[-3:]

    if day_1.volume <= 0:
        return False

    day_2_volume_factor = day_2.volume / day_1.volume

    return (
        _is_gap_up(day_1, day_2)
        and _is_gap_up(day_2, day_3)
        and day_2_volume_factor >= 1.2
    )
