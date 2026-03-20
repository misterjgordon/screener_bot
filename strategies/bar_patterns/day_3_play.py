"""Day 3 play bar pattern: big day 1 up, day 2 inside range, trigger above day 2 high on day 3.

Day 1 (daily): up only, >= 3x relative volume, range >= 1.5x ADR.
Day 2 (daily): consolidation — high and low inside day 1 range.
Day 3: trigger when any 2-min bar on day 3 has high > day 2 high. All 2-min bars on day 3
are considered (all times: PM, RTH, AH). When ib/symbol are provided, a synthetic bar
(last 2-min bar merged with current realtime) is appended so the trigger can be recognized
on the latest price without waiting for the next 2-min close.

Uses only bar_series.bars_1d and bar_series.bars_2min (no extra IB bar requests). Expects
bars_1d = 20 days of daily bars, bars_2min = current day 2-min bars. Day 1/2/3 are the
last three daily bars; day 2 high/low come from that bar in the 20-day range.
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from strategies.bar_patterns.breakout import _realtime_bar_ohlc
from strategies.bar_patterns.breakout import _synthetic_bar
from strategies.indicators.adr import calculate_adr
from strategies.indicators.rvol import rvol
from strategies.utils import bar_date
from strategies.utils import is_session_bar
from trading.market_data import get_realtime_bar
from trading.models import Bar
from trading.models import BarSeries

if TYPE_CHECKING:
    from ib_async import IB

MIN_RVOL = 2.5
MIN_ATR_MULTIPLIER = 1.5
RVOL_PERIOD = 10
ADR_DAYS = 20
MIN_DAILY_BARS = 3  # day_1, day_2, day_3 (last three of the 20 daily bars)


def _day_1_qualifies(bars_1d: list[Bar], day_1_index: int) -> bool:
    """Day 1: up only, >= MIN_RVOL relative volume, range >= 1.5 * ADR. Uses only bars_1d."""
    d1 = bars_1d[day_1_index]
    if d1.close <= d1.open:
        return False
    series_for_day_1 = BarSeries(bars_1d=bars_1d[: day_1_index + 1], bars_2min=[])
    rvol_val = rvol(series_for_day_1, period=RVOL_PERIOD)
    if rvol_val is None or rvol_val < MIN_RVOL:
        return False
    adr_slice = bars_1d[max(0, day_1_index - ADR_DAYS): day_1_index]
    if len(adr_slice) < 1:
        return False
    series_for_adr = BarSeries(bars_1d=adr_slice, bars_2min=[])
    adr_val = calculate_adr(ib=None, symbol='', days=len(adr_slice), bundle=series_for_adr)
    if adr_val is None or adr_val <= 0:
        return False
    day_1_range = d1.high - d1.low
    return day_1_range >= MIN_ATR_MULTIPLIER * adr_val


def _day_2_consolidates(day_1_bar: Bar, day_2_bar: Bar) -> bool:
    """Day 2 high and low strictly inside day 1 range (day_2 is one of last bars in bars_1d)."""
    return day_1_bar.low < day_2_bar.low and day_2_bar.high < day_1_bar.high


def _day_3_bars_and_synthetic(
    bar_series: BarSeries,
    day_3_date: date,
    ib: 'IB | None',
    symbol: str | None,
) -> list[Bar]:
    """All 2-min bars on day_3_date (PM, RTH, AH), plus synthetic bar when ib/symbol provided.

    Synthetic bar = last 2-min bar merged with current realtime so a trigger above day 2
    high is detected on latest price without waiting for the next 2-min close.
    """
    day_3_bars = [b for b in bar_series.bars_2min if is_session_bar(b.date, day_3_date)]
    if not day_3_bars or ib is None or symbol is None:
        return day_3_bars
    rt_bar = get_realtime_bar(ib, symbol)
    if rt_bar is None:
        return day_3_bars
    o, h_rt, l_rt, c, dt = _realtime_bar_ohlc(rt_bar)
    if dt is None or dt.date() != day_3_date or h_rt < l_rt:
        return day_3_bars
    synthetic = _synthetic_bar(day_3_bars[-1], rt_bar)
    return day_3_bars + [synthetic]


def _triggered_above(day_2_high: float, bars: list[Bar]) -> bool:
    """True if any bar (including synthetic) has high > day_2_high; setup is valid in that case."""
    return any(b.high > day_2_high for b in bars)


def _day_1_rvol_adr(bars_1d: list[Bar], day_1_index: int) -> tuple[float | None, float | None]:
    """Rvol and ADR used for day 1 qualification; (None, None) if not computable."""
    series_for_day_1 = BarSeries(bars_1d=bars_1d[: day_1_index + 1], bars_2min=[])
    rvol_val = rvol(series_for_day_1, period=RVOL_PERIOD)
    adr_slice = bars_1d[max(0, day_1_index - ADR_DAYS): day_1_index]
    if len(adr_slice) < 1:
        return (rvol_val, None)
    series_for_adr = BarSeries(bars_1d=adr_slice, bars_2min=[])
    adr_val = calculate_adr(ib=None, symbol='', days=len(adr_slice), bundle=series_for_adr)
    return (rvol_val, adr_val)


@dataclass
class Day3PlayStats:
    """Result of day_3_play pattern check."""

    triggered: bool
    day_2_high: float | None
    day_3_high: float | None  # max high of 2-min bars on day 3 (incl. synthetic); else day 3 daily high
    day_1_qualified: bool
    day_2_consolidated: bool
    rvol: float | None  # day 1 relative volume (vs MIN_RVOL)
    adr: float | None  # ADR used for day 1 range check (vs 1.5*ADR)
    day_1_up_only: bool
    day_1_rvol_ok: bool
    day_1_range_ok: bool


def day_3_play_stats(
    bar_series: BarSeries,
    ib: 'IB | None' = None,
    symbol: str | None = None,
) -> Day3PlayStats:
    """Evaluate day-3-play using only bar_series (no extra IB bar requests).

    bars_1d: last three bars are day_1, day_2, day_3. Day 2 high/low from bars_1d[-2].
    bars_2min: current day bars for trigger. Optional ib/symbol: merge realtime into synthetic bar.
    """
    bars_1d = bar_series.bars_1d
    if len(bars_1d) < MIN_DAILY_BARS:
        return Day3PlayStats(
            triggered=False,
            day_2_high=None,
            day_3_high=None,
            day_1_qualified=False,
            day_2_consolidated=False,
            rvol=None,
            adr=None,
            day_1_up_only=False,
            day_1_rvol_ok=False,
            day_1_range_ok=False,
        )
    day_1_idx = -3
    day_2_idx = -2
    day_1_bar = bars_1d[day_1_idx]
    day_2_bar = bars_1d[day_2_idx]
    day_2_high = day_2_bar.high
    day_3_date = bar_date(bars_1d[-1].date)
    if day_3_date is None:
        return Day3PlayStats(
            triggered=False,
            day_2_high=None,
            day_3_high=None,
            day_1_qualified=False,
            day_2_consolidated=False,
            rvol=None,
            adr=None,
            day_1_up_only=False,
            day_1_rvol_ok=False,
            day_1_range_ok=False,
        )

    rvol_val, adr_val = _day_1_rvol_adr(bars_1d, day_1_idx)
    day_1_up_only = day_1_bar.close > day_1_bar.open
    day_1_rvol_ok = rvol_val is not None and rvol_val >= MIN_RVOL
    day_1_range_ok = (
        adr_val is not None
        and adr_val > 0
        and (day_1_bar.high - day_1_bar.low) >= MIN_ATR_MULTIPLIER * adr_val
    )
    day_1_ok = _day_1_qualifies(bars_1d, day_1_idx)
    day_2_ok = _day_2_consolidates(day_1_bar, day_2_bar)
    if not day_1_ok or not day_2_ok:
        day_3_bars_early = _day_3_bars_and_synthetic(bar_series, day_3_date, ib, symbol)
        if day_3_bars_early:
            day_3_high_early = max(b.high for b in day_3_bars_early)
        else:
            day_3_high_early = bars_1d[-1].high
        return Day3PlayStats(
            triggered=False,
            day_2_high=day_2_high,
            day_3_high=day_3_high_early,
            day_1_qualified=day_1_ok,
            day_2_consolidated=day_2_ok,
            rvol=rvol_val,
            adr=adr_val,
            day_1_up_only=day_1_up_only,
            day_1_rvol_ok=day_1_rvol_ok,
            day_1_range_ok=day_1_range_ok,
        )

    day_3_bars = _day_3_bars_and_synthetic(bar_series, day_3_date, ib, symbol)
    triggered = _triggered_above(day_2_high, day_3_bars)
    if day_3_bars:
        day_3_high = max(b.high for b in day_3_bars)
    else:
        day_3_high = bars_1d[-1].high
    return Day3PlayStats(
        triggered=triggered,
        day_2_high=day_2_high,
        day_3_high=day_3_high,
        day_1_qualified=True,
        day_2_consolidated=True,
        rvol=rvol_val,
        adr=adr_val,
        day_1_up_only=day_1_up_only,
        day_1_rvol_ok=day_1_rvol_ok,
        day_1_range_ok=day_1_range_ok,
    )


def day_3_play(
    bar_series: BarSeries,
    ib: 'IB | None' = None,
    symbol: str | None = None,
) -> bool:
    """True when day-3-play pattern is set up (day 1 + day 2) and day 3 has traded above day 2 high."""
    return day_3_play_stats(bar_series, ib=ib, symbol=symbol).triggered
