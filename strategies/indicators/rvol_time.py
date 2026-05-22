"""Relative volume at time: bar volume vs mean SMA(volume) at the same ET minute on prior days."""

import pandas as pd

from strategies.indicators.cumulative_avg_volume import _as_of_prior_metric_avg
from strategies.indicators.cumulative_avg_volume import _lookup_as_of
from strategies.indicators.cumulative_avg_volume import bar_time_et_minute
from strategies.indicators.cumulative_avg_volume import fill_sparse_intraday
from strategies.indicators.sma_volume import DEFAULT_SMA_VOLUME_PERIOD
from strategies.indicators.sma_volume import sma_volume_series
from strategies.indicators.trading_date import trading_date_series_utc

INDICATOR_DECIMAL_PLACES = 2
DEFAULT_RVOL_TIME_PERIOD = 10


def _sma_vol_metrics_table(
    trading_date: 'pd.Series',
    volume: 'pd.Series',
    timestamp_utc: 'pd.Series',
    sma_period: int,
) -> pd.DataFrame:
    """Per bar: ``bar_time`` and rolling SMA(volume) within ``trading_date``."""
    work = pd.DataFrame(
        {
            'trading_date': trading_date,
            'volume': volume.astype('float64'),
            'timestamp': pd.to_datetime(timestamp_utc, utc=True),
            'bar_time': bar_time_et_minute(timestamp_utc),
        },
    ).sort_values(['trading_date', 'timestamp'])
    work['sma_vol'] = work.groupby('trading_date', sort=False).volume.transform(
        lambda series: sma_volume_series(series, period=sma_period),
    )
    return work[['trading_date', 'bar_time', 'sma_vol']]


def rvol_time_series(
    trading_date: 'pd.Series',
    volume: 'pd.Series',
    timestamp_utc: 'pd.Series',
    history_bars: 'pd.DataFrame',
    period: int = DEFAULT_RVOL_TIME_PERIOD,
    sma_period: int = DEFAULT_SMA_VOLUME_PERIOD,
) -> 'pd.Series':
    """Bar volume / mean as-of SMA(volume) at this ET minute on prior session days.

    Zero-volume bars and sparse PM/AH minutes forward-fill within the session (0 on first bar).
    """
    if history_bars.empty:
        return pd.Series([0.0] * len(trading_date), index=trading_date.index)

    hist_td = trading_date_series_utc(history_bars.timestamp)
    metrics = _sma_vol_metrics_table(
        hist_td,
        history_bars.volume,
        history_bars.timestamp,
        sma_period=sma_period,
    )
    if metrics.empty:
        return pd.Series([0.0] * len(trading_date), index=trading_date.index)

    bar_time = bar_time_et_minute(timestamp_utc)
    denom_lookup = _as_of_prior_metric_avg(
        metrics,
        'sma_vol',
        period,
        trading_date,
        bar_time,
    )
    bar_vol = volume.astype('float64')

    ratios: list[float] = []
    for td, vol, bt in zip(trading_date, bar_vol, bar_time, strict=False):
        if pd.isna(bt):
            ratios.append(float('nan'))
            continue
        denom = _lookup_as_of(denom_lookup, td, int(bt))
        if denom is None or denom <= 0:
            ratios.append(float('nan'))
            continue
        ratios.append(round(float(vol) / denom, INDICATOR_DECIMAL_PLACES))

    raw = pd.Series(ratios, index=trading_date.index, dtype='float64')
    return fill_sparse_intraday(trading_date, timestamp_utc, raw)
