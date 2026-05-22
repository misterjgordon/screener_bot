"""VWAP vector series vs scalar vwap on ingested cold bars."""

import os

import pandas as pd
import pytest

from strategies.indicators.trading_date import trading_date_series_utc
from strategies.indicators.vwap import vwap
from strategies.indicators.vwap import vwap_series
from strategies.utils import bar_session
from trading import config as cf
from trading.models import Bar
from trading.models import BarSeries
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_START_DATE
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_paths import symbol_path

OHLCV_TEST_SYMBOL_ENV = 'OHLCV_TEST_SYMBOL'
MIN_SESSION_BARS = 10


def _require_cold_symbol() -> str:
    if not cf.OHLCV_COLD_ROOT.strip():
        pytest.skip('Set OHLCV_COLD_ROOT so tests can read ingested Parquet')
    sym_env = os.environ.get(OHLCV_TEST_SYMBOL_ENV, '').strip().upper()
    if sym_env:
        if not symbol_path(sym_env).is_file():
            pytest.skip(f'Missing cold Parquet for {sym_env}')
        return sym_env
    tickers = load_tickers_from_symbol_list_file(get_p_ohlcv_symbol_list_path())
    for sym in tickers:
        if symbol_path(sym).is_file():
            return sym
    pytest.skip('No cold Parquet files found; run ingest first')


def _load_cold_bars(sym: str) -> pd.DataFrame:
    p_parquet = symbol_path(sym)
    df = pd.read_parquet(p_parquet)
    if df.empty:
        pytest.skip(f'Parquet empty: {p_parquet}')
    ts = pd.to_datetime(df.timestamp, utc=True)
    start = pd.Timestamp(OHLCV_DEFAULT_INGEST_START_DATE, tz='UTC')
    end = pd.Timestamp(OHLCV_DEFAULT_INGEST_END_DATE, tz='UTC') + pd.Timedelta(days=1)
    return df.loc[(ts >= start) & (ts < end)].sort_values('timestamp').reset_index(drop=True)


def _bar_series_from_df(df: pd.DataFrame) -> BarSeries:
    bars: list[Bar] = []
    for ts, open_px, high_px, low_px, close_px, vol in zip(
        df.timestamp,
        df.open,
        df.high,
        df.low,
        df.close,
        df.volume,
        strict=True,
    ):
        bar_dt = ts.to_pydatetime()
        bars.append(
            Bar(
                date=bar_dt,
                open=float(open_px),
                high=float(high_px),
                low=float(low_px),
                close=float(close_px),
                volume=float(vol),
            ),
        )
    return BarSeries(bars_1d=[], bars_2min=bars)


def _one_session_day_slice(df: pd.DataFrame) -> pd.DataFrame | None:
    session_dates = trading_date_series_utc(df.timestamp)
    for session_day in sorted(session_dates.unique()):
        mask = session_dates == session_day
        day_df = df.loc[mask]
        if len(day_df) >= MIN_SESSION_BARS:
            return day_df.reset_index(drop=True)
    return None


def test_vwap_series_last_bar_matches_scalar_vwap() -> None:
    sym = _require_cold_symbol()
    df = _load_cold_bars(sym)
    maybe_day = _one_session_day_slice(df)
    if maybe_day is None:
        pytest.skip('No session day with enough bars for VWAP parity')
    assert maybe_day is not None
    day_df = maybe_day

    trading_date = trading_date_series_utc(day_df.timestamp)
    series = vwap_series(day_df.high, day_df.low, day_df.close, day_df.volume, trading_date)
    scalar = vwap(_bar_series_from_df(day_df))
    vector_last = series.iloc[-1]
    parity = scalar == pytest.approx(vector_last) if scalar is not None else pd.isna(vector_last)

    print(
        '**summary for vwap_series vs scalar vwap:**\n'
        f'{sym} | session_bars = {len(day_df)} | parity = {bool(parity)}\n'
        f'scalar_vwap = {scalar}\n'
        f'vector_last = {vector_last}'
    )

    assert scalar is not None
    assert parity


def test_vwap_pm_anchored_on_first_rth_bar() -> None:
    sym = _require_cold_symbol()
    df = _load_cold_bars(sym)
    maybe_day = _one_session_day_slice(df)
    if maybe_day is None:
        pytest.skip('No session day with enough bars for PM anchor test')
    assert maybe_day is not None
    day_df = maybe_day

    ts = pd.to_datetime(day_df.timestamp, utc=True)
    sessions = [bar_session(t.to_pydatetime()) for t in ts]
    has_pm = 'PM' in sessions
    has_rth = 'RTH' in sessions
    if not (has_pm and has_rth):
        pytest.skip('Session day has no PM and RTH bars for anchor comparison')

    trading_date = trading_date_series_utc(day_df.timestamp)
    full_vwap = vwap_series(day_df.high, day_df.low, day_df.close, day_df.volume, trading_date)

    rth_mask = pd.Series(sessions) == 'RTH'
    rth_df = day_df.loc[rth_mask].reset_index(drop=True)
    rth_date = trading_date_series_utc(rth_df.timestamp)
    rth_only_vwap = vwap_series(rth_df.high, rth_df.low, rth_df.close, rth_df.volume, rth_date)

    first_rth_idx = next(i for i, s in enumerate(sessions) if s == 'RTH')
    full_at_first_rth = float(full_vwap.iloc[first_rth_idx])
    rth_only_first = float(rth_only_vwap.iloc[0])
    pm_anchored = full_at_first_rth != pytest.approx(rth_only_first)

    print(
        '**summary for VWAP PM anchor on first RTH bar:**\n'
        f'{sym} | has_pm = {has_pm} | has_rth = {has_rth}\n'
        f'full_vwap_at_first_rth = {full_at_first_rth}\n'
        f'rth_only_vwap_first = {rth_only_first}\n'
        f'pm_anchored_differs = {pm_anchored}'
    )

    assert pm_anchored
