"""9- and 21-period EMA indicators."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.models import BarSeries


def ema(bar_series: 'BarSeries', period: int) -> float | None:
    """Exponential Moving Average of close prices. Uses bar_series.bars_2min."""
    bars = bar_series.bars_2min
    if len(bars) < period:
        return None
    closes = [b.close for b in bars]
    if len(closes) < period:
        return None
    closes = closes[-period * 2:]
    if len(closes) < period:
        return None
    ema_val = sum(closes[:period]) / period
    k = 2.0 / (1.0 + period)
    for c in closes[period:]:
        ema_val = (c - ema_val) * k + ema_val
    return round(ema_val, 2)


def ema9(bar_series: 'BarSeries') -> float | None:
    """9-period EMA of close prices."""
    return ema(bar_series, 9)


def ema21(bar_series: 'BarSeries') -> float | None:
    """21-period EMA of close prices."""
    return ema(bar_series, 21)
