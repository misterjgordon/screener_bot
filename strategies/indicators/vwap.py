"""Volume-weighted average price indicator."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.models import BarSeries


def vwap(bar_series: 'BarSeries') -> float | None:
    """Volume-weighted average price: cumulative(typical_price * volume) / cumulative(volume).

    Typical price per bar = (H + L + C) / 3.
    Example: bar with H=20, L=15, C=18 → typical = 17.67; typical * V = 353.33;
    over multiple bars, VWAP = sum(typical * volume) / sum(volume).
    Uses bar_series.bars_2min.
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
