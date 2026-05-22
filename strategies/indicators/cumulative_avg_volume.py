"""Average prior-session cumulative volume at the same ET bar time."""

import numpy as np
import pandas as pd

from strategies.indicators.trading_date import trading_date_series_utc
from trading.market_timezones import exchange_timezone_name
from trading.market_timezones import timestamp_utc_series_to_zone

INDICATOR_DECIMAL_PLACES = 2
DEFAULT_CUMULATIVE_AVG_VOLUME_PERIOD = 10


def _timestamp_et(timestamp_utc: 'pd.Series') -> 'pd.Series':
    return timestamp_utc_series_to_zone(timestamp_utc, exchange_timezone_name())


def bar_time_et_minute(timestamp_utc: 'pd.Series') -> 'pd.Series':
    """ET minute-of-day (0..1439) for aligning the same clock time across sessions."""
    ts_et = _timestamp_et(timestamp_utc)
    return ts_et.dt.hour * 60 + ts_et.dt.minute


def fill_sparse_intraday(
    trading_date: 'pd.Series',
    timestamp_utc: 'pd.Series',
    values: 'pd.Series',
) -> 'pd.Series':
    """Forward-fill within each ``trading_date``; first bar of the day becomes 0."""
    work = pd.DataFrame(
        {
            'trading_date': trading_date,
            'timestamp': pd.to_datetime(timestamp_utc, utc=True),
            'value': values.astype('float64'),
        },
        index=values.index,
    )
    sort_idx = work.sort_values(['trading_date', 'timestamp']).index
    sorted_work = work.loc[sort_idx]
    filled = sorted_work.groupby('trading_date', sort=False).value.transform(
        lambda series: series.ffill().fillna(0.0),
    )
    out = pd.Series(index=values.index, dtype='float64')
    out.loc[sort_idx] = filled.to_numpy()
    return out


def cum_vol_metrics_table(
    trading_date: 'pd.Series',
    volume: 'pd.Series',
    timestamp_utc: 'pd.Series',
) -> pd.DataFrame:
    """Per bar: ``bar_time`` and cumulative volume within ``trading_date``."""
    work = pd.DataFrame(
        {
            'trading_date': trading_date,
            'volume': volume.astype('float64'),
            'timestamp': pd.to_datetime(timestamp_utc, utc=True),
            'bar_time': bar_time_et_minute(timestamp_utc),
        },
    ).sort_values(['trading_date', 'timestamp'])
    work['cum_vol'] = work.groupby('trading_date', sort=False).volume.cumsum()
    return work[['trading_date', 'bar_time', 'cum_vol']]


def _days_by_trading_date(metrics: pd.DataFrame) -> dict[object, pd.DataFrame]:
    return {
        td: grp.sort_values('bar_time')
        for td, grp in metrics.groupby('trading_date', sort=False)
    }


def _metric_as_of(day: pd.DataFrame, bar_time: int, metric_col: str) -> float | None:
    """Last ``metric_col`` on ``day`` where ``bar_time`` <= target minute."""
    if day.empty:
        return None
    bar_times = day.bar_time.to_numpy()
    idx = int(np.searchsorted(bar_times, bar_time, side='right')) - 1
    if idx < 0:
        return None
    value = day.iloc[idx][metric_col]
    if pd.isna(value):
        return None
    return float(value)


def _as_of_prior_metric_avg(
    metrics: pd.DataFrame,
    metric_col: str,
    period: int,
    target_trading_date: 'pd.Series',
    target_bar_time: 'pd.Series',
) -> dict[tuple[object, int], float]:
    """Mean as-of ``metric_col`` for each target ``(trading_date, bar_time)`` pair only."""
    days = _days_by_trading_date(metrics)
    dates_sorted = sorted(metrics.trading_date.unique())
    date_pos = {d: i for i, d in enumerate(dates_sorted)}

    targets = pd.DataFrame(
        {
            'trading_date': target_trading_date,
            'bar_time': target_bar_time,
        },
    ).dropna(subset=['bar_time'])
    targets['bar_time'] = targets.bar_time.astype('int64')
    targets = targets.drop_duplicates()

    out: dict[tuple[object, int], float] = {}
    for td in targets.trading_date.unique():
        pos = date_pos.get(td)
        if pos is None or pos < period:
            continue
        prior_dates = dates_sorted[pos - period: pos]
        bar_times = targets.loc[targets.trading_date == td, 'bar_time']
        for bar_time in bar_times:
            values: list[float] = []
            for prior_date in prior_dates:
                day = days.get(prior_date)
                if day is None:
                    continue
                as_of = _metric_as_of(day, int(bar_time), metric_col)
                if as_of is not None:
                    values.append(as_of)
            if not values:
                continue
            out[(td, int(bar_time))] = float(sum(values) / len(values))
    return out


def _lookup_as_of(
    lookup: dict[tuple[object, int], float],
    trading_date: object,
    bar_time: int,
) -> float | None:
    """Exact ``bar_time`` match, else the latest prior minute already in ``lookup``."""
    exact = lookup.get((trading_date, bar_time))
    if exact is not None:
        return exact
    prior_times = [bt for (td, bt) in lookup if td == trading_date and bt <= bar_time]
    if not prior_times:
        return None
    return lookup[(trading_date, max(prior_times))]


def cumulative_avg_volume_series(
    trading_date: 'pd.Series',
    volume: 'pd.Series',
    timestamp_utc: 'pd.Series',
    history_bars: 'pd.DataFrame',
    period: int = DEFAULT_CUMULATIVE_AVG_VOLUME_PERIOD,
) -> 'pd.Series':
    """Mean as-of cumulative volume at this ET minute over the prior ``period`` session days.

    Sparse PM/AH minutes use the prior bar's value within the session (0 on the first bar).
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

    bar_time = bar_time_et_minute(timestamp_utc)
    denom_lookup = _as_of_prior_metric_avg(
        metrics,
        'cum_vol',
        period,
        trading_date,
        bar_time,
    )

    values: list[float] = []
    for td, bt in zip(trading_date, bar_time, strict=False):
        if pd.isna(bt):
            values.append(float('nan'))
            continue
        denom = _lookup_as_of(denom_lookup, td, int(bt))
        if denom is None or denom <= 0:
            values.append(float('nan'))
            continue
        values.append(round(denom, INDICATOR_DECIMAL_PLACES))

    raw = pd.Series(values, index=trading_date.index, dtype='float64')
    return fill_sparse_intraday(trading_date, timestamp_utc, raw)
