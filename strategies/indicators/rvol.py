"""Relative volume: cumulative session volume vs average prior cumulative volume at same time."""

from typing import TYPE_CHECKING

import pandas as pd

from strategies.indicators.cumulative_avg_volume import _days_by_trading_date
from strategies.indicators.cumulative_avg_volume import _metric_as_of
from strategies.indicators.cumulative_avg_volume import bar_time_et_minute
from strategies.indicators.cumulative_avg_volume import cum_vol_metrics_table
from strategies.indicators.cumulative_avg_volume import fill_sparse_intraday
from strategies.indicators.trading_date import trading_date_series_utc

if TYPE_CHECKING:
    from trading.models import BarSeries

INDICATOR_DECIMAL_PLACES = 2
DEFAULT_RVOL_PERIOD = 10


def rvol_series(
    trading_date: 'pd.Series',
    volume: 'pd.Series',
    timestamp_utc: 'pd.Series',
    history_bars: 'pd.DataFrame',
    cumulative_avg_volume: 'pd.Series',
) -> 'pd.Series':
    """RVOL: cumulative volume so far today / ``cumulative_avg_volume`` at this bar.

    PM, RTH, and AH count toward today's cumulative volume. Sparse minutes forward-fill
    within the session (0 on the first bar).
    """
    if history_bars.empty:
        return pd.Series([0.0] * len(trading_date), index=trading_date.index)

    hist_td = trading_date_series_utc(history_bars.timestamp)
    metrics = cum_vol_metrics_table(
        hist_td,
        history_bars.volume,
        history_bars.timestamp,
    )
    if metrics.empty:
        return pd.Series([0.0] * len(trading_date), index=trading_date.index)

    days = _days_by_trading_date(metrics)
    bar_time = bar_time_et_minute(timestamp_utc)

    ratios: list[float] = []
    for td, bt, denom in zip(trading_date, bar_time, cumulative_avg_volume, strict=False):
        if pd.isna(bt):
            ratios.append(float('nan'))
            continue
        day = days.get(td)
        if day is None:
            ratios.append(float('nan'))
            continue
        today_cum = _metric_as_of(day, int(bt), 'cum_vol')
        if today_cum is None:
            ratios.append(float('nan'))
            continue
        if pd.isna(denom) or float(denom) <= 0:
            ratios.append(float('nan'))
            continue
        ratios.append(round(today_cum / float(denom), INDICATOR_DECIMAL_PLACES))

    raw = pd.Series(ratios, index=trading_date.index, dtype='float64')
    return fill_sparse_intraday(trading_date, timestamp_utc, raw)


def rvol_daily_series(
    trading_date: 'pd.Series',
    volume: 'pd.Series',
    timestamp_utc: 'pd.Series',
    history_bars: 'pd.DataFrame',
    cumulative_avg_volume: 'pd.Series',
) -> 'pd.Series':
    """Catalog alias for :func:`rvol_series`."""
    return rvol_series(
        trading_date,
        volume,
        timestamp_utc,
        history_bars,
        cumulative_avg_volume,
    )


def rvol(bar_series: 'BarSeries', period: int = DEFAULT_RVOL_PERIOD) -> float | None:
    """Full-session RVOL on the last daily bar (desk ``bars_1d``; unchanged)."""
    bars = bar_series.bars_1d
    if len(bars) < period + 1:
        return None
    prior_volumes = [float(b.volume) for b in bars[-period - 1: -1]]
    avg_vol = sum(prior_volumes) / period
    if avg_vol <= 0:
        return None
    current_vol = float(bars[-1].volume)
    return round(current_vol / avg_vol, INDICATOR_DECIMAL_PLACES)
