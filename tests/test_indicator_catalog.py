"""Indicator catalog load and generic adapter."""

from datetime import date
from datetime import datetime
from datetime import timedelta

import pandas as pd
import pytest

from backtesting.indicators.indicator_catalog_load import catalog_entry_by_id
from backtesting.indicators.indicator_catalog_load import default_indicator_ids
from backtesting.indicators.indicator_catalog_load import topological_indicator_order
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY
from strategies.indicators.cumulative_avg_volume import DEFAULT_CUMULATIVE_AVG_VOLUME_PERIOD
from strategies.indicators.cumulative_avg_volume import cumulative_avg_volume_series
from strategies.indicators.cumulative_avg_volume import fill_sparse_intraday
from strategies.indicators.rvol import rvol_series
from strategies.indicators.rvol_time import DEFAULT_RVOL_TIME_PERIOD
from strategies.indicators.rvol_time import rvol_time_series
from strategies.indicators.sma_volume import DEFAULT_SMA_VOLUME_PERIOD
from strategies.utils import RTH_START
from trading.market_timezones import exchange_timezone_name

ADR_CATALOG_DAYS = 14


def _bars_for_day(session: date, start_et: tuple[int, int], volumes: list[int]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    hour, minute = start_et
    tz_exchange = exchange_timezone_name()
    for i, vol in enumerate(volumes):
        et = datetime(session.year, session.month, session.day, hour, minute) + timedelta(minutes=i)
        ts_utc = pd.Timestamp(et, tz=tz_exchange).tz_convert('UTC')
        rows.append({'timestamp': ts_utc, 'volume': vol})
    return pd.DataFrame(rows)


def _daily_bars(rows: list[tuple[date, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        {'trading_date': [r[0] for r in rows], 'volume': [r[1] for r in rows]},
    )


def test_catalog_has_rvol_and_rvol_time() -> None:
    by_id = catalog_entry_by_id()
    assert 'rvol' in by_id
    assert 'rvol_time' in by_id
    assert 'cumulative_avg_volume' in by_id
    assert catalog_entry_by_id()['rvol'].requires_history_bars


def test_default_pipeline_ids_include_rvol_and_rvol_time() -> None:
    defaults = default_indicator_ids()
    assert 'rvol' in defaults
    assert 'rvol_time' in defaults


def test_topological_order_places_cumulative_avg_volume_before_rvol() -> None:
    ordered = topological_indicator_order(('rvol', 'trading_date'))
    assert ordered.index('cumulative_avg_volume') < ordered.index('rvol')


def test_topological_order_places_vwap_after_trading_date() -> None:
    ordered = topological_indicator_order(('vwap', 'trading_date', 'ema9'))
    assert ordered.index('vwap') > ordered.index('trading_date')


def test_registry_compute_fn_names_match_catalog() -> None:
    for iid in ('ema9', 'rvol', 'rvol_time', 'cumulative_avg_volume'):
        spec = INDICATOR_REGISTRY.spec(iid)
        assert getattr(spec.compute_fn, '__name__', '') == f'_compute_{iid}'


def test_rvol_series_cum_over_cumulative_avg_volume() -> None:
    """Cum vol today / cumulative_avg_volume at same ET minute."""
    d1, d2, d3 = date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)
    history = pd.concat(
        [
            _bars_for_day(d1, (9, 30), [10, 10, 10]),
            _bars_for_day(d2, (9, 30), [20, 20, 20]),
            _bars_for_day(d3, (9, 30), [30, 30, 30]),
        ],
        ignore_index=True,
    )
    session = d3
    ts = _bars_for_day(session, (9, 30), [30, 30, 30]).timestamp
    trading_date = pd.Series([session] * len(ts))
    volume = pd.Series([30.0] * len(ts))
    denom = cumulative_avg_volume_series(trading_date, volume, ts, history, period=2)
    out = rvol_series(trading_date, volume, ts, history, denom)
    assert float(out.iloc[1]) == pytest.approx(2.0, rel=1e-3)


def test_rvol_catalog_requires_cumulative_avg_volume() -> None:
    entry = catalog_entry_by_id()['rvol']
    assert 'cumulative_avg_volume' in entry.requires
    assert not entry.requires_daily_bars


def test_cumulative_avg_volume_catalog_period() -> None:
    assert catalog_entry_by_id()['cumulative_avg_volume'].params['period'] == (
        DEFAULT_CUMULATIVE_AVG_VOLUME_PERIOD
    )


def test_fill_sparse_intraday_first_bar_zero() -> None:
    td = pd.Series([date(2026, 5, 15)] * 2)
    ts = _bars_for_day(date(2026, 5, 15), (9, 30), [1, 1]).timestamp
    values = pd.Series([float('nan'), 2.0])
    filled = fill_sparse_intraday(td, ts, values)
    assert float(filled.iloc[0]) == 0.0
    assert float(filled.iloc[1]) == 2.0


def test_rvol_time_bar_volume_over_mean_prior_sma() -> None:
    d1, d2, d3 = date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)
    vols = list(range(1, 26))
    history = pd.concat(
        [
            _bars_for_day(d1, (RTH_START.hour, RTH_START.minute), vols),
            _bars_for_day(d2, (RTH_START.hour, RTH_START.minute), [v * 2 for v in vols]),
            _bars_for_day(d3, (RTH_START.hour, RTH_START.minute), vols),
        ],
        ignore_index=True,
    )
    ts = _bars_for_day(d3, (RTH_START.hour, RTH_START.minute), vols).timestamp
    trading_date = pd.Series([d3] * len(ts))
    volume = pd.Series([float(v) for v in vols])
    out = rvol_time_series(trading_date, volume, ts, history, period=2, sma_period=20)
    assert float(out.iloc[24]) > 0


def test_rvol_time_catalog_sma_period() -> None:
    entry = catalog_entry_by_id()['rvol_time']
    assert entry.params['period'] == DEFAULT_RVOL_TIME_PERIOD
    assert entry.params['sma_period'] == DEFAULT_SMA_VOLUME_PERIOD


def test_adr_catalog_days_param() -> None:
    assert catalog_entry_by_id()['adr'].params['days'] == ADR_CATALOG_DAYS
