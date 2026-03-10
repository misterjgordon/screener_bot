"""Breakout bar pattern checks used by strategy setup scans."""

from dataclasses import dataclass

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.models import BarSeries

BREAK_OUT_LOOKBACK_BARS = 5  # max 195 bars
BREAK_OUT_SIZE_MULTIPLIER = 3.0


@dataclass
class BreakOutBarStats:
    """Result of break_out_bar pattern scan on 2-min bars."""

    breakout: bool
    largest_bar_size: float | None
    avg_bar_size: float | None
    midpoint_of_breakout_bar: float | None


def _bar_size(bar: object) -> float:
    """Return full bar range (high-low)."""
    return bar.high - bar.low  # type: ignore[attr-defined]


def break_out_bar_stats(bar_series: 'BarSeries', lookback_bars: int = BREAK_OUT_LOOKBACK_BARS) -> BreakOutBarStats:
    """Return breakout pattern result: flag, sizes, and midpoint of largest bar.

    Uses bar_series.bars_2min_rth. breakout is True when largest bar in lookback window
    is > 4x the average 2-minute bar size across the full open-to-now sample.
    """
    bars_2min = bar_series.bars_2min_rth
    if lookback_bars <= 0:
        return BreakOutBarStats(False, None, None, None)
    if len(bars_2min) < lookback_bars:
        return BreakOutBarStats(False, None, None, None)

    bar_sizes = [_bar_size(bar) for bar in bars_2min]
    if not bar_sizes:
        return BreakOutBarStats(False, None, None, None)

    avg_size = round(sum(bar_sizes) / len(bar_sizes), 2)
    if avg_size <= 0:
        return BreakOutBarStats(False, None, None, None)

    lookback_bars_list = list(bars_2min[-lookback_bars:])
    lookback_sizes = bar_sizes[-lookback_bars:]
    largest_lookback_size = max(lookback_sizes) if lookback_sizes else None
    if largest_lookback_size is None:
        return BreakOutBarStats(False, None, avg_size, None)

    largest_lookback_size = round(largest_lookback_size, 2)
    largest_bar = max(lookback_bars_list, key=_bar_size)
    midpoint_of_largest_bar = round((largest_bar.high + largest_bar.low) / 2, 2)
    threshold = avg_size * BREAK_OUT_SIZE_MULTIPLIER
    breakout = largest_lookback_size > threshold
    return BreakOutBarStats(
        breakout=breakout,
        largest_bar_size=largest_lookback_size,
        avg_bar_size=avg_size,
        midpoint_of_breakout_bar=midpoint_of_largest_bar if breakout else None,
    )


def break_out_bar(bar_series: 'BarSeries', lookback_bars: int = BREAK_OUT_LOOKBACK_BARS) -> bool:
    """Check for breakout bar in recent lookback window."""
    return break_out_bar_stats(bar_series, lookback_bars=lookback_bars).breakout
