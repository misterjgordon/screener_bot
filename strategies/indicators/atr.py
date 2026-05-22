"""Average True Range (RMA / Wilder-style smoothing) on a series of OHLC bars (typically daily)."""

import pandas as pd

INDICATOR_DECIMAL_PLACES = 2
DEFAULT_ATR_PERIOD = 14


def _atr_scalar_from_true_ranges(trs: list[float], period: int) -> float | None:
    if len(trs) < period:
        return None
    atr_val = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return round(atr_val, INDICATOR_DECIMAL_PLACES)


def atr(bars_1d: list, period: int = DEFAULT_ATR_PERIOD) -> float | None:
    """ATR with RMA smoothing: first value is SMA of the first ``period`` true ranges; then RMA.

    Each true range uses the prior bar's close. Requires at least ``period + 1`` bars.
    """
    if len(bars_1d) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars_1d)):
        h = float(bars_1d[i].high)
        low_px = float(bars_1d[i].low)
        c_prev = float(bars_1d[i - 1].close)
        tr = max(h - low_px, abs(h - c_prev), abs(low_px - c_prev))
        trs.append(tr)
    return _atr_scalar_from_true_ranges(trs, period)


def atr_series(
    trading_date: pd.Series,
    daily_bars: pd.DataFrame,
    period: int = DEFAULT_ATR_PERIOD,
) -> pd.Series:
    """Wilder ATR on RTH ``daily_bars``; map the as-of value to each minute by ``trading_date``."""
    if daily_bars.empty:
        return pd.Series([float('nan')] * len(trading_date), index=trading_date.index)

    daily = daily_bars.sort_values('trading_date').reset_index(drop=True)
    prev_close = daily.close.shift(1)
    tr = pd.concat(
        [
            daily.high - daily.low,
            (daily.high - prev_close).abs(),
            (daily.low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_by_date: dict[object, float] = {}
    tr_list: list[float] = []
    for i in range(len(daily)):
        if i > 0 and pd.notna(tr.iloc[i]):
            tr_list.append(float(tr.iloc[i]))
        sess = daily.trading_date.iloc[i]
        atr_val = _atr_scalar_from_true_ranges(tr_list, period)
        atr_by_date[sess] = float('nan') if atr_val is None else atr_val
    mapped = trading_date.map(atr_by_date)
    return mapped.astype('float64')
