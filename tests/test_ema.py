"""EMA vector series vs scalar ema on ingested cold bars."""

import os

import pandas as pd
import pytest

from strategies.indicators.ema import ema9
from strategies.indicators.ema import ema21
from strategies.indicators.ema import ema_series
from trading import config as cf
from trading.models import Bar
from trading.models import BarSeries
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_START_DATE
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_paths import symbol_path

OHLCV_TEST_SYMBOL_ENV = 'OHLCV_TEST_SYMBOL'
EMA_PERIOD = 9
MIN_BARS_FOR_EMA = 50


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
    df = df.loc[(ts >= start) & (ts < end)].sort_values('timestamp').reset_index(drop=True)
    if len(df) < MIN_BARS_FOR_EMA:
        pytest.skip(f'Need at least {MIN_BARS_FOR_EMA} bars for EMA parity test')
    return df


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


def test_ema_series_last_bar_matches_scalar_ema9() -> None:
    sym = _require_cold_symbol()
    df = _load_cold_bars(sym)
    tail = df.tail(MIN_BARS_FOR_EMA)
    series = ema_series(tail.close, EMA_PERIOD)
    scalar = ema9(_bar_series_from_df(tail))
    vector_last = series.iloc[-1]
    parity = scalar == pytest.approx(vector_last) if scalar is not None else pd.isna(vector_last)

    print(
        '**summary for ema_series vs ema9:**\n'
        f'{sym} | tail_bars = {len(tail)} | parity = {bool(parity)}\n'
        f'scalar_ema9 = {scalar}\n'
        f'vector_last = {vector_last}'
    )

    assert scalar is not None
    assert parity


def test_ema_series_last_bar_matches_scalar_ema21() -> None:
    sym = _require_cold_symbol()
    df = _load_cold_bars(sym)
    tail = df.tail(80)
    series = ema_series(tail.close, 21)
    scalar = ema21(_bar_series_from_df(tail))
    vector_last = series.iloc[-1]
    parity = scalar == pytest.approx(vector_last) if scalar is not None else pd.isna(vector_last)

    print(
        '**summary for ema_series vs ema21:**\n'
        f'{sym} | tail_bars = {len(tail)} | parity = {bool(parity)}\n'
        f'scalar_ema21 = {scalar}\n'
        f'vector_last = {vector_last}'
    )

    assert scalar is not None
    assert parity
