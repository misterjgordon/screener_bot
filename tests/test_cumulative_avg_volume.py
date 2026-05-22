"""Tests for cumulative_avg_volume indicator."""

from datetime import date
from datetime import datetime
from datetime import timedelta

import pandas as pd
import pytest

from strategies.indicators.cumulative_avg_volume import cumulative_avg_volume_series
from trading.market_timezones import exchange_timezone_name


def _bars_for_day(session: date, start_et: tuple[int, int], volumes: list[int]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    hour, minute = start_et
    tz_exchange = exchange_timezone_name()
    for i, vol in enumerate(volumes):
        et = datetime(session.year, session.month, session.day, hour, minute) + timedelta(minutes=i)
        ts_utc = pd.Timestamp(et, tz=tz_exchange).tz_convert('UTC')
        rows.append({'timestamp': ts_utc, 'volume': vol})
    return pd.DataFrame(rows)


def test_cumulative_avg_volume_mean_prior_cum_at_bar_time() -> None:
    """Bar 2: mean prior cum (10+20, 20+40) / 2 = 45 on session day 3."""
    d1, d2, d3 = date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)
    history = pd.concat(
        [
            _bars_for_day(d1, (9, 30), [10, 10, 10]),
            _bars_for_day(d2, (9, 30), [20, 20, 20]),
            _bars_for_day(d3, (9, 30), [30, 30, 30]),
        ],
        ignore_index=True,
    )
    ts = _bars_for_day(d3, (9, 30), [30, 30, 30]).timestamp
    trading_date = pd.Series([d3] * len(ts))
    volume = pd.Series([30.0] * len(ts))
    out = cumulative_avg_volume_series(trading_date, volume, ts, history, period=2)
    assert float(out.iloc[0]) == pytest.approx(15.0)
    assert float(out.iloc[1]) == pytest.approx(30.0)
    assert float(out.iloc[2]) == pytest.approx(45.0)


def test_cumulative_avg_volume_as_of_when_prior_days_share_start_time() -> None:
    """As-of uses each prior day's cum vol at the same clock time (mean across period)."""
    d1, d2, d3 = date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)
    history = pd.concat(
        [
            _bars_for_day(d1, (4, 0), [100, 100]),
            _bars_for_day(d2, (4, 0), [200, 200]),
            _bars_for_day(d3, (4, 0), [300, 300, 300]),
        ],
        ignore_index=True,
    )
    ts = _bars_for_day(d3, (4, 0), [300, 300, 300]).timestamp
    trading_date = pd.Series([d3] * len(ts))
    volume = pd.Series([300.0] * len(ts))
    out = cumulative_avg_volume_series(trading_date, volume, ts, history, period=2)
    assert float(out.iloc[0]) == pytest.approx(150.0)
    assert float(out.iloc[1]) == pytest.approx(300.0)
    assert float(out.iloc[2]) == pytest.approx(300.0)
