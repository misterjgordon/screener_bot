"""Relative Volume: current bar volume / SMA(volume, period) over prior bars.

Per TradingView: Average Volume is SMA of the past N periods, not including the
current volume bar. Relative Volume = volume / average volume.
See: https://www.tradingview.com/support/solutions/43000635874-how-do-we-calculate-relative-volume-and-relative-volume-at-time/
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.models import BarSeries


def rvol(bar_series: 'BarSeries', period: int = 10) -> float | None:
    """Relative Volume = current bar volume / SMA(volume, period) over prior bars.

    Uses bar_series.bars_1d. Current bar is the last bar. Average volume is the
    mean of the prior `period` bars (excluding current), matching TradingView's
    "not taking into account the current volume bar".
    """
    bars = bar_series.bars_1d
    if len(bars) < period + 1:
        return None
    prior_volumes = [float(b.volume) for b in bars[-period - 1: -1]]
    avg_vol = sum(prior_volumes) / period
    if avg_vol <= 0:
        return None
    current_vol = float(bars[-1].volume)
    return round(current_vol / avg_vol, 4)
