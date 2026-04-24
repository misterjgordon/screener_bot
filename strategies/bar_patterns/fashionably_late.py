"""Fashionably Late scalp: 9 EMA crosses VWAP with relative volume > 2.

Long: upsloping 9 EMA crosses above flat-to-downsloping VWAP. Stop 1/3 distance VWAP to low;
target = cross + measured move (measured move = cross - low of day).
Short: downsloping 9 EMA crosses below flat-to-upsloping VWAP. Stop 1/3 distance VWAP to high;
target = cross - measured move (measured move = high of day - cross).
Cross price and entry are the 9 EMA value at the bar where the cross is detected. All crosses in the time
window are returned (list with direction per cross). Trigger only between 10:00 and 13:30 ET.
Move filter: from trailing low (long) or trailing high (short) over last 7 2-min bars to VWAP must be
at least 0.3 ADR to avoid many triggers. ADR from bars_1d; trailing low/high same logic as bar_loader trailing stop.
EMA/VWAP from strategies.indicators; rvol from bars_1d.
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import time
from typing import Literal

from strategies.indicators.ema import ema9
from strategies.indicators.rvol import rvol
from strategies.indicators.vwap import vwap
from trading.bar_loader import TRAILING_STOP_BARS_2MIN
from trading.models import Bar
from trading.models import BarSeries

RVOL_MIN = 2.0
RVOL_PERIOD = 10
MIN_BARS_2MIN = 10
# Min move from trailing low (long) or trailing high (short) to VWAP, in ADR units.
ADR_MOVE_MIN = 0.3
ADR_DAYS = 20
# ET window for valid trigger (bar's datetime assumed ET).
TIME_WINDOW_START = time(10, 0)
TIME_WINDOW_END = time(13, 30)


@dataclass
class FashionablyLateCross:
    """Single cross event: direction, price (bar close), time, stop/target."""

    direction: Literal['long', 'short']
    cross_price: float
    cross_bar_time: datetime
    stop_price: float
    target_price: float
    measured_move: float
    vwap_at_cross: float
    low_of_day: float | None  # long only
    high_of_day: float | None  # short only


@dataclass
class FashionablyLateStats:
    """Result of Fashionably Late scan: all crosses in window, plus first-cross fields for compat."""

    triggered: bool
    direction: Literal['long', 'short'] | None
    cross_price: float | None
    cross_bar_time: datetime | None
    low_of_day: float | None
    high_of_day: float | None
    vwap_at_cross: float | None
    measured_move: float | None
    stop_price: float | None
    target_price: float | None
    rvol_at_cross: float | None
    crosses: list[FashionablyLateCross]


def _vwap_at(bar_series: BarSeries, bars_slice: list) -> float | None:
    """VWAP over bars_slice (uses strategies.indicators.vwap). Slice = bars through desired bar."""
    if not bars_slice:
        return None
    series = BarSeries(bars_1d=bar_series.bars_1d, bars_2min=bars_slice)
    return vwap(series)


def _ema9_at(bar_series: BarSeries, bars_slice: list) -> float | None:
    """9-period EMA over bars_slice (uses strategies.indicators.ema9). Slice = bars through desired bar."""
    if not bars_slice:
        return None
    series = BarSeries(bars_1d=bar_series.bars_1d, bars_2min=bars_slice)
    return ema9(series)


def _adr_from_daily_bars(bars_1d: list, days: int = ADR_DAYS) -> float | None:
    """Average Daily Range from last `days` daily bars (high - low)."""
    if not bars_1d or len(bars_1d) < days:
        return None
    take = bars_1d[-days:]
    ranges = [b.high - b.low for b in take if b.high is not None and b.low is not None]
    if not ranges:
        return None
    return round(float(sum(ranges) / len(ranges)), 2)


def _trailing_low(bars: list, bar_idx: int) -> float | None:
    """Min low over last TRAILING_STOP_BARS_2MIN bars up to bar_idx (same logic as trailing stop)."""
    start = max(0, bar_idx - TRAILING_STOP_BARS_2MIN + 1)
    slice_bars = bars[start: bar_idx + 1]
    lows = [b.low for b in slice_bars if b.low is not None]
    return min(lows) if lows else None


def _trailing_high(bars: list, bar_idx: int) -> float | None:
    """Max high over last TRAILING_STOP_BARS_2MIN bars up to bar_idx (same logic as trailing stop)."""
    start = max(0, bar_idx - TRAILING_STOP_BARS_2MIN + 1)
    slice_bars = bars[start: bar_idx + 1]
    highs = [b.high for b in slice_bars if b.high is not None]
    return max(highs) if highs else None


def _bar_time_in_window(bar: Bar, start_et: time, end_et: time) -> bool:
    """True if bar's time (assumed ET) is in [start_et, end_et] inclusive."""
    t = bar.date.time()
    return start_et <= t <= end_et


def _empty_stats(daily_rvol: float | None) -> FashionablyLateStats:
    return FashionablyLateStats(
        triggered=False,
        direction=None,
        cross_price=None,
        cross_bar_time=None,
        low_of_day=None,
        high_of_day=None,
        vwap_at_cross=None,
        measured_move=None,
        stop_price=None,
        target_price=None,
        rvol_at_cross=daily_rvol,
        crosses=[],
    )


def fashionably_late_stats(
    bar_series: BarSeries,
    rvol_min: float = RVOL_MIN,
    rvol_period: int = RVOL_PERIOD,
    require_upsloping_ema: bool = True,
    require_flat_to_down_vwap: bool = True,
    require_downsloping_ema_short: bool = True,
    require_flat_to_up_vwap_short: bool = True,
    time_window_start: time | None = TIME_WINDOW_START,
    time_window_end: time | None = TIME_WINDOW_END,
    adr_move_min: float = ADR_MOVE_MIN,
    adr_days: int = ADR_DAYS,
) -> FashionablyLateStats:
    """Scan for Fashionably Late long or short: 9 EMA cross vs VWAP, daily rel vol > rvol_min.

    Long: 9 EMA crosses above VWAP (upsloping EMA, flat-to-down VWAP). Short: 9 EMA crosses
    below VWAP (downsloping EMA, flat-to-up VWAP). Scans long first, then short. Stop/target
    use measured move from low (long) or high (short) of day. Time window applies to both.
    Uses bar_series.bars_2min (all intraday bars). Move filter: from trailing low (long) or
    trailing high (short) over last 7 2-min bars to VWAP must be at least adr_move_min ADRs.
    """
    bars = bar_series.bars_2min
    daily_rvol = rvol(bar_series, period=rvol_period)
    adr = _adr_from_daily_bars(bar_series.bars_1d, days=adr_days)
    if len(bars) < MIN_BARS_2MIN:
        return _empty_stats(daily_rvol)

    if daily_rvol is None or daily_rvol < rvol_min:
        return _empty_stats(daily_rvol)

    # Scan forward over all bars; collect crosses in [time_window_start, time_window_end].
    # Cross price / entry = 9 EMA at the bar where the cross is detected.
    collected: list[FashionablyLateCross] = []
    for i in range(1, len(bars)):
        in_window = (
            time_window_start is None
            or time_window_end is None
            or _bar_time_in_window(bars[i], time_window_start, time_window_end)
        )
        if not in_window:
            continue

        ema_prev = _ema9_at(bar_series, bars[:i])
        ema_curr = _ema9_at(bar_series, bars[: i + 1])
        vwap_prev = _vwap_at(bar_series, bars[:i])
        vwap_curr = _vwap_at(bar_series, bars[: i + 1])
        if ema_prev is None or ema_curr is None or vwap_prev is None or vwap_curr is None:
            continue

        bars_through_i = bars[: i + 1]
        cross_price = round(ema_curr, 2)

        if ema_prev <= vwap_prev and ema_curr > vwap_curr:
            if require_upsloping_ema and ema_curr <= ema_prev:
                continue
            if require_flat_to_down_vwap and vwap_curr > vwap_prev * 1.001:
                continue
            if adr is not None and adr > 0:
                trailing_low = _trailing_low(bars, i)
                if trailing_low is None or (vwap_curr - trailing_low) < adr_move_min * adr:
                    continue
            low_of_day = min(b.low for b in bars_through_i)
            measured_move = cross_price - low_of_day
            vwap_to_low = vwap_curr - low_of_day
            stop_price = round(vwap_curr - (1.0 / 3.0) * vwap_to_low, 2)
            target_price = round(cross_price + measured_move, 2)
            collected.append(
                FashionablyLateCross(
                    direction='long',
                    cross_price=cross_price,
                    cross_bar_time=bars[i].date,
                    stop_price=stop_price,
                    target_price=target_price,
                    measured_move=round(measured_move, 2),
                    vwap_at_cross=vwap_curr,
                    low_of_day=low_of_day,
                    high_of_day=None,
                )
            )

        if ema_prev >= vwap_prev and ema_curr < vwap_curr:
            if require_downsloping_ema_short and ema_curr >= ema_prev:
                continue
            if require_flat_to_up_vwap_short and vwap_curr < vwap_prev * 0.999:
                continue
            if adr is not None and adr > 0:
                trailing_high = _trailing_high(bars, i)
                if trailing_high is None or (trailing_high - vwap_curr) < adr_move_min * adr:
                    continue
            high_of_day = max(b.high for b in bars_through_i)
            measured_move = high_of_day - cross_price
            high_to_vwap = high_of_day - vwap_curr
            stop_price = round(vwap_curr + (1.0 / 3.0) * high_to_vwap, 2)
            target_price = round(cross_price - measured_move, 2)
            collected.append(
                FashionablyLateCross(
                    direction='short',
                    cross_price=cross_price,
                    cross_bar_time=bars[i].date,
                    stop_price=stop_price,
                    target_price=target_price,
                    measured_move=round(measured_move, 2),
                    vwap_at_cross=vwap_curr,
                    low_of_day=None,
                    high_of_day=high_of_day,
                )
            )

    if not collected:
        return _empty_stats(daily_rvol)

    first = collected[0]
    return FashionablyLateStats(
        triggered=True,
        direction=first.direction,
        cross_price=first.cross_price,
        cross_bar_time=first.cross_bar_time,
        low_of_day=first.low_of_day,
        high_of_day=first.high_of_day,
        vwap_at_cross=first.vwap_at_cross,
        measured_move=first.measured_move,
        stop_price=first.stop_price,
        target_price=first.target_price,
        rvol_at_cross=daily_rvol,
        crosses=collected,
    )


def fashionably_late(
    bar_series: BarSeries,
    rvol_min: float = RVOL_MIN,
    time_window_start: time | None = TIME_WINDOW_START,
    time_window_end: time | None = TIME_WINDOW_END,
) -> bool:
    """True when Fashionably Late long or short setup is present (9 EMA cross vs VWAP, rel vol > rvol_min)."""
    return fashionably_late_stats(
        bar_series,
        rvol_min=rvol_min,
        time_window_start=time_window_start,
        time_window_end=time_window_end,
    ).triggered


@dataclass
class FashionablyLateDiagnostics:
    """Per-factor pass/fail for Fashionably Late scan (to see which conditions are not true)."""

    enough_bars: bool
    bars_count: int
    daily_rvol: float | None
    rvol_ok: bool
    rvol_min: float
    adr: float | None
    long_cross_found: bool
    long_first_cross_bar_idx: int | None
    long_cross_price: float | None
    long_trailing_low: float | None
    long_move_to_vwap: float | None
    long_time_in_window: bool | None
    long_upsloping_ema: bool | None
    long_flat_to_down_vwap: bool | None
    long_move_to_vwap_ok: bool | None
    short_cross_found: bool
    short_first_cross_bar_idx: int | None
    short_cross_price: float | None
    short_trailing_high: float | None
    short_move_to_vwap: float | None
    short_time_in_window: bool | None
    short_downsloping_ema: bool | None
    short_flat_to_up_vwap: bool | None
    short_move_to_vwap_ok: bool | None


def fashionably_late_diagnostics(
    bar_series: BarSeries,
    rvol_min: float = RVOL_MIN,
    rvol_period: int = RVOL_PERIOD,
    time_window_start: time | None = TIME_WINDOW_START,
    time_window_end: time | None = TIME_WINDOW_END,
) -> FashionablyLateDiagnostics:
    """Report which factors are true/false for the Fashionably Late scan (no trigger required).

    Only considers bars in the time window when finding first long/short cross, so diagnostics
    match what the main scan sees. Includes ADR move filter (trailing low/high to VWAP >= 0.3 ADR).
    """
    bars = bar_series.bars_2min
    bars_count = len(bars)
    enough_bars = bars_count >= MIN_BARS_2MIN
    daily_rvol = rvol(bar_series, period=rvol_period)
    rvol_ok = daily_rvol is not None and daily_rvol >= rvol_min
    adr = _adr_from_daily_bars(bar_series.bars_1d, days=ADR_DAYS)

    long_cross_found = False
    long_first_cross_bar_idx: int | None = None
    long_cross_price: float | None = None
    long_trailing_low: float | None = None
    long_move_to_vwap: float | None = None
    long_time_in_window: bool | None = None
    long_upsloping_ema: bool | None = None
    long_flat_to_down_vwap: bool | None = None
    long_move_to_vwap_ok: bool | None = None

    short_cross_found = False
    short_first_cross_bar_idx: int | None = None
    short_cross_price: float | None = None
    short_trailing_high: float | None = None
    short_move_to_vwap: float | None = None
    short_time_in_window: bool | None = None
    short_downsloping_ema: bool | None = None
    short_flat_to_up_vwap: bool | None = None
    short_move_to_vwap_ok: bool | None = None

    for i in range(1, len(bars)):
        in_window = (
            time_window_start is None
            or time_window_end is None
            or _bar_time_in_window(bars[i], time_window_start, time_window_end)
        )
        if not in_window:
            continue
        ema_prev = _ema9_at(bar_series, bars[:i])
        ema_curr = _ema9_at(bar_series, bars[: i + 1])
        vwap_prev = _vwap_at(bar_series, bars[:i])
        vwap_curr = _vwap_at(bar_series, bars[: i + 1])
        if ema_prev is None or ema_curr is None or vwap_prev is None or vwap_curr is None:
            continue
        if not long_cross_found and ema_prev <= vwap_prev and ema_curr > vwap_curr:
            long_cross_found = True
            long_first_cross_bar_idx = i
            long_cross_price = ema_curr
            trailing_low = _trailing_low(bars, i)
            long_trailing_low = trailing_low
            long_move_to_vwap = (vwap_curr - trailing_low) if trailing_low is not None else None
            long_upsloping_ema = ema_curr > ema_prev
            long_flat_to_down_vwap = vwap_curr <= vwap_prev * 1.001
            long_time_in_window = True
            if adr is not None and adr > 0:
                long_move_to_vwap_ok = (
                    trailing_low is not None and (vwap_curr - trailing_low) >= ADR_MOVE_MIN * adr
                )
            else:
                long_move_to_vwap_ok = None
            break

    for i in range(1, len(bars)):
        in_window = (
            time_window_start is None
            or time_window_end is None
            or _bar_time_in_window(bars[i], time_window_start, time_window_end)
        )
        if not in_window:
            continue
        ema_prev = _ema9_at(bar_series, bars[:i])
        ema_curr = _ema9_at(bar_series, bars[: i + 1])
        vwap_prev = _vwap_at(bar_series, bars[:i])
        vwap_curr = _vwap_at(bar_series, bars[: i + 1])
        if ema_prev is None or ema_curr is None or vwap_prev is None or vwap_curr is None:
            continue
        if not short_cross_found and ema_prev >= vwap_prev and ema_curr < vwap_curr:
            short_cross_found = True
            short_first_cross_bar_idx = i
            short_cross_price = ema_curr
            trailing_high = _trailing_high(bars, i)
            short_trailing_high = trailing_high
            short_move_to_vwap = (trailing_high - vwap_curr) if trailing_high is not None else None
            short_downsloping_ema = ema_curr < ema_prev
            short_flat_to_up_vwap = vwap_curr >= vwap_prev * 0.999
            short_time_in_window = True
            if adr is not None and adr > 0:
                short_move_to_vwap_ok = (
                    trailing_high is not None and (trailing_high - vwap_curr) >= ADR_MOVE_MIN * adr
                )
            else:
                short_move_to_vwap_ok = None
            break

    return FashionablyLateDiagnostics(
        enough_bars=enough_bars,
        bars_count=bars_count,
        daily_rvol=daily_rvol,
        rvol_ok=rvol_ok,
        rvol_min=rvol_min,
        adr=adr,
        long_cross_found=long_cross_found,
        long_first_cross_bar_idx=long_first_cross_bar_idx,
        long_cross_price=long_cross_price,
        long_trailing_low=long_trailing_low,
        long_move_to_vwap=long_move_to_vwap,
        long_time_in_window=long_time_in_window,
        long_upsloping_ema=long_upsloping_ema,
        long_flat_to_down_vwap=long_flat_to_down_vwap,
        long_move_to_vwap_ok=long_move_to_vwap_ok,
        short_cross_found=short_cross_found,
        short_first_cross_bar_idx=short_first_cross_bar_idx,
        short_cross_price=short_cross_price,
        short_trailing_high=short_trailing_high,
        short_move_to_vwap=short_move_to_vwap,
        short_time_in_window=short_time_in_window,
        short_downsloping_ema=short_downsloping_ema,
        short_flat_to_up_vwap=short_flat_to_up_vwap,
        short_move_to_vwap_ok=short_move_to_vwap_ok,
    )
