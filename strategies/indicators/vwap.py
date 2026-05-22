"""Volume-weighted average price indicator."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

    from trading.models import BarSeries

INDICATOR_DECIMAL_PLACES = 2


def vwap_series(
    high: 'pd.Series',
    low: 'pd.Series',
    close: 'pd.Series',
    volume: 'pd.Series',
    trading_date: 'pd.Series',
) -> 'pd.Series':
    """Cumulative VWAP per ``trading_date``, anchored at each day's first bar in the frame.

    Typical price ``(H + L + C) / 3``. Rows with ``volume <= 0`` get NaN; they do not
    advance the cumulative sums.
    """
    tp = (high.astype('float64') + low.astype('float64') + close.astype('float64')) / 3.0
    vol = volume.astype('float64')
    pv = tp * vol.where(vol > 0, 0.0)
    v = vol.where(vol > 0, 0.0)

    session_key = trading_date.astype('object')
    cum_pv = pv.groupby(session_key, sort=False).cumsum()
    cum_v = v.groupby(session_key, sort=False).cumsum()

    result = cum_pv / cum_v
    result = result.where(cum_v > 0)
    result = result.where(vol > 0)
    return result.round(INDICATOR_DECIMAL_PLACES)


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
    return round(sum_pv / sum_v, INDICATOR_DECIMAL_PLACES)
