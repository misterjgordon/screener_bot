"""9- and 21-period EMA indicators."""

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from trading.models import BarSeries

INDICATOR_DECIMAL_PLACES = 2


def ema_series(close: pd.Series, period: int) -> pd.Series:
    """EMA of ``close`` for every bar, matching ``ema()`` on the same trailing window per row.

    For index *i*, uses the last ``period * 2`` closes through *i* (same as scalar ``ema``).
    Earlier rows (before ``period - 1``) are NaN.
    """
    if period < 1:
        msg = f'period must be >= 1, got {period}'
        raise ValueError(msg)

    closes = close.astype('float64')
    k = 2.0 / (1.0 + period)
    out: list[float] = []
    for i in range(len(closes)):
        if i < period - 1:
            out.append(float('nan'))
            continue
        window = closes.iloc[max(0, i + 1 - period * 2): i + 1].tolist()
        ema_val = sum(window[:period]) / period
        for price in window[period:]:
            ema_val = (price - ema_val) * k + ema_val
        out.append(round(ema_val, INDICATOR_DECIMAL_PLACES))
    return pd.Series(out, index=close.index, dtype='float64')


def ema(bar_series: 'BarSeries', period: int) -> float | None:
    """Exponential Moving Average of close prices over bar_series.bars_2min; one value per call.

    To get EMA at any point in the day: pass a BarSeries whose bars_2min is the slice from
    session start (PM) through that bar; calculation starts from the first bar (PM).
    """
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
    return round(ema_val, INDICATOR_DECIMAL_PLACES)


def ema9(bar_series: 'BarSeries') -> float | None:
    """9-period EMA of close prices."""
    return ema(bar_series, 9)


def ema21(bar_series: 'BarSeries') -> float | None:
    """21-period EMA of close prices."""
    return ema(bar_series, 21)
