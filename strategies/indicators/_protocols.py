"""Shared protocols for indicator bar types."""

from datetime import date
from datetime import datetime
from typing import Protocol


class BarLike(Protocol):
    """Bar with close, high, low, volume (e.g. ib_async BarData)."""

    close: float | None
    high: float | None
    low: float | None
    volume: float | int


class BarWithDate(Protocol):
    """Bar with date and volume for time-of-day matching (e.g. Relative Volume at Time)."""

    date: datetime | date
    volume: float | int
