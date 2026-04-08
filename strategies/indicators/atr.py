"""Average True Range (RMA / Wilder-style smoothing) on a series of OHLC bars (typically daily)."""


def atr(bars_1d: list, period: int = 14) -> float | None:
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
    if len(trs) < period:
        return None
    atr_val = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return round(atr_val, 4)
