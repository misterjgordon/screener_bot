"""Breakout bar pattern checks used by strategy setup scans."""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from trading.market_data import get_realtime_bar
from trading.models import Bar

if TYPE_CHECKING:
    from ib_async import IB

    from trading.models import BarSeries

BREAK_OUT_LOOKBACK_BARS = 100  # max 195 bars
BREAK_OUT_SIZE_MULTIPLIER = 3.0


@dataclass
class BreakOutBarStats:
    """Result of break_out_bar pattern scan on 2-min bars."""

    breakout: bool
    largest_bar_size: float | None
    avg_bar_size: float | None
    midpoint_of_breakout_bar: float | None
    breakout_bar_bullish: bool | None  # True = long, False = short, None when no breakout


def _bar_size(bar: object) -> float:
    """Return full bar range (high-low)."""
    return bar.high - bar.low  # type: ignore[attr-defined]


def _realtime_bar_ohlc(rt_bar: object) -> tuple[float, float, float, float, datetime | None]:
    """Read OHLC and date from ib_async realtime bar (handles .open_/.high_/.low_/.close_)."""
    o = getattr(rt_bar, 'open', None) or getattr(rt_bar, 'open_', None)
    h = getattr(rt_bar, 'high', None) or getattr(rt_bar, 'high_', None)
    low_rt = getattr(rt_bar, 'low', None) or getattr(rt_bar, 'low_', None)
    c = getattr(rt_bar, 'close', None) or getattr(rt_bar, 'close_', None)
    dt = getattr(rt_bar, 'time', None) or getattr(rt_bar, 'date', None)
    if not all(x is not None for x in (o, h, low_rt, c)):
        return (0.0, 0.0, 0.0, 0.0, None)
    assert o is not None and h is not None and low_rt is not None and c is not None
    if isinstance(dt, datetime):
        return (float(o), float(h), float(low_rt), float(c), dt)
    return (float(o), float(h), float(low_rt), float(c), None)


def _bar_high_low(bar: object) -> tuple[float, float]:
    """Read high and low from any bar (handles .high/.low or .high_/.low_)."""
    h = getattr(bar, 'high', None) or getattr(bar, 'high_', None)
    low = getattr(bar, 'low', None) or getattr(bar, 'low_', None)
    if h is None or low is None:
        return (0.0, 0.0)
    return (float(h), float(low))


def _synthetic_bar(last_2min_bar: object, rt_bar: object) -> Bar:
    """Build bar: high = max(last 2-min high, realtime high), low = min(last 2-min low, realtime low); open/close from 5-sec."""
    o, h_rt, l_rt, c, dt = _realtime_bar_ohlc(rt_bar)
    h_2min, low_2min = _bar_high_low(last_2min_bar)
    high = max(h_2min, h_rt)
    low = min(low_2min, l_rt)
    date = dt if isinstance(dt, datetime) else last_2min_bar.date  # type: ignore[attr-defined]
    vol = getattr(rt_bar, 'volume', None) or getattr(rt_bar, 'volume_', 0)
    volume = float(vol) if vol is not None else 0.0
    return Bar(date=date, open=o, high=high, low=low, close=c, volume=volume)


def break_out_bar_stats(
    bar_series: 'BarSeries',
    lookback_bars: int = BREAK_OUT_LOOKBACK_BARS,
    ib: 'IB | None' = None,
    symbol: str | None = None,
) -> BreakOutBarStats:
    """Return breakout pattern result: flag, sizes, and midpoint of largest bar.

    Uses bar_series.bars_2min_rth. When ib and symbol are provided, fetches one 5-sec realtime bar
    and merges it with the last 2-min bar into a synthetic bar (high/low extended) for the lookback
    so the current incomplete 2-min bar is included in breakout detection.
    """
    bars_2min = bar_series.bars_2min_rth
    if lookback_bars <= 0:
        return BreakOutBarStats(False, None, None, None, None)

    bars_for_lookback: list = list(bars_2min)
    if ib is not None and symbol is not None and bars_2min:
        rt_bar = get_realtime_bar(ib, symbol)
        if rt_bar is not None:
            _, h_rt, l_rt, _, dt = _realtime_bar_ohlc(rt_bar)
            if dt is not None and h_rt >= l_rt:
                synthetic = _synthetic_bar(bars_2min[-1], rt_bar)
                bars_for_lookback = bars_2min + [synthetic]

    if len(bars_for_lookback) < lookback_bars:
        return BreakOutBarStats(False, None, None, None, None)

    bar_sizes = [_bar_size(bar) for bar in bars_2min]
    if not bar_sizes:
        return BreakOutBarStats(False, None, None, None, None)

    avg_size = round(sum(bar_sizes) / len(bar_sizes), 2)
    if avg_size <= 0:
        return BreakOutBarStats(False, None, None, None, None)

    lookback_bars_list = list(bars_for_lookback[-lookback_bars:])
    lookback_sizes = [_bar_size(b) for b in lookback_bars_list]
    largest_lookback_size = max(lookback_sizes) if lookback_sizes else None
    if largest_lookback_size is None:
        return BreakOutBarStats(False, None, avg_size, None, None)

    largest_lookback_size = round(largest_lookback_size, 2)
    largest_bar = max(lookback_bars_list, key=_bar_size)
    midpoint_of_largest_bar = round((largest_bar.high + largest_bar.low) / 2, 2)
    threshold = avg_size * BREAK_OUT_SIZE_MULTIPLIER
    breakout = largest_lookback_size > threshold
    is_bullish = bool(largest_bar.close > largest_bar.open) if breakout else None
    return BreakOutBarStats(
        breakout=breakout,
        largest_bar_size=largest_lookback_size,
        avg_bar_size=avg_size,
        midpoint_of_breakout_bar=midpoint_of_largest_bar if breakout else None,
        breakout_bar_bullish=is_bullish,
    )


def break_out_bar(
    bar_series: 'BarSeries',
    lookback_bars: int = BREAK_OUT_LOOKBACK_BARS,
    ib: 'IB | None' = None,
    symbol: str | None = None,
) -> bool:
    """Check for breakout bar in recent lookback window."""
    return break_out_bar_stats(
        bar_series, lookback_bars=lookback_bars, ib=ib, symbol=symbol
    ).breakout
