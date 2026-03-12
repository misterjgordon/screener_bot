"""Volume-weighted average price indicator."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.models import BarSeries


def vwap(bar_series: 'BarSeries') -> float | None:
    """Volume-weighted average price: one price per call over bar_series.bars_2min.

    Cumulative(typical_price * volume) / cumulative(volume). Typical price = (H + L + C) / 3.
    To get VWAP at any point in the day: pass a BarSeries whose bars_2min is the slice from
    session start (PM) through that bar; calculation starts from the first bar (PM).
    """
    bars = bar_series.bars_2min
    if not bars:
        return None
    sum_pv = 0.0
    sum_v = 0.0
    for b in bars:
        typical = (b.high + b.low + b.close) / 3.0
        vol = float(b.volume)
        sum_pv += typical * vol
        sum_v += vol
    if sum_v <= 0:
        return None
    return round(sum_pv / sum_v, 2)
