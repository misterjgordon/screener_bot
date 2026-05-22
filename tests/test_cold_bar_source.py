"""ColdBarSource: read ingested cold Parquet (no fabricated OHLCV).

Requires ``OHLCV_COLD_ROOT`` populated by ``scripts/ingest_ohlcv_cold.py``.
Optional ``OHLCV_TEST_SYMBOL`` env selects the symbol file.

uv run --frozen pytest tests/test_cold_bar_source.py -v
"""

import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backtesting.indicators.indicator_catalog_load import daily_bar_lookback_calendar_days
from backtesting.indicators.indicator_catalog_load import history_bar_lookback_calendar_days
from backtesting.indicators.indicator_catalog_load import min_history_sessions_for_indicators
from backtesting.indicators.indicator_pipeline import IndicatorPipeline
from backtesting.io.cold_bar_source import ColdBarSource
from strategies.indicators.trading_date import trading_date_series_utc
from trading import config as cf
from trading.market_timezones import exchange_timezone_name
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_START_DATE
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_paths import symbol_path
from trading.storage.ohlcv.ohlcv_schema import OHLCV_COLD_PARQUET_COLUMNS

OHLCV_TEST_SYMBOL_ENV = 'OHLCV_TEST_SYMBOL'
MISSING_SYMBOL = 'NOTACOLDTICKER999'
WARMUP_BAR_COUNT = 50

# Inclusive ET calendar analysis window (same defaults as cold ingest / verify tests).
OHLCV_COLD_VERIFY_START_DATE = OHLCV_DEFAULT_INGEST_START_DATE
OHLCV_COLD_VERIFY_END_DATE = OHLCV_DEFAULT_INGEST_END_DATE


def _require_cold_symbol_parquet() -> tuple[Path, str]:
    """Resolve a symbol Parquet written by ingest; skip when cold store is not populated."""
    if not cf.OHLCV_COLD_ROOT.strip():
        pytest.skip(
            'Set OHLCV_COLD_ROOT to the directory used by scripts/ingest_ohlcv_cold.py '
            'so tests can read ingested Parquet',
        )
    sym_env = os.environ.get(OHLCV_TEST_SYMBOL_ENV, '').strip().upper()
    if sym_env:
        p_parquet = symbol_path(sym_env)
        if not p_parquet.is_file():
            pytest.skip(
                f'Missing {p_parquet}; ingest data for {sym_env} or unset {OHLCV_TEST_SYMBOL_ENV}',
            )
        return p_parquet, sym_env
    p_list = get_p_ohlcv_symbol_list_path()
    tickers = load_tickers_from_symbol_list_file(p_list)
    for sym in tickers:
        p_parquet = symbol_path(sym)
        if p_parquet.is_file():
            return p_parquet, sym
    pytest.skip(
        'No .parquet files under OHLCV_COLD_ROOT for symbols in the ticker list; '
        'run scripts/ingest_ohlcv_cold.py first',
    )


def _session_date_with_rows(p_parquet: Path) -> date | None:
    """Pick one ET session date that has at least one bar in the verify window."""
    df_raw = pd.read_parquet(p_parquet, columns=['timestamp'])
    if df_raw.empty:
        return None
    ts = pd.to_datetime(df_raw.timestamp, utc=True)
    mask = (ts.dt.date >= OHLCV_COLD_VERIFY_START_DATE) & (ts.dt.date <= OHLCV_COLD_VERIFY_END_DATE)
    df_in = df_raw.loc[mask]
    if df_in.empty:
        return None
    session_dates = trading_date_series_utc(df_in.timestamp)
    return session_dates.iloc[0]


def test_trading_date_series_matches_cold_bar_timestamp() -> None:
    p_parquet, sym = _require_cold_symbol_parquet()
    df_raw = pd.read_parquet(p_parquet, columns=['timestamp'])
    if df_raw.empty:
        pytest.skip(f'Parquet empty: {p_parquet}')

    sample_ts = pd.to_datetime(df_raw.timestamp.iloc[0], utc=True)
    session_date = trading_date_series_utc(pd.Series([sample_ts])).iloc[0]
    expected_date = sample_ts.tz_convert(exchange_timezone_name()).date()

    print(
        '**summary for trading_date_series_utc on cold timestamp:**\n'
        f'{sym} | sample_utc = {sample_ts.isoformat()} | session_date_ok = '
        f'{session_date == expected_date}\n'
        f'session_date = {session_date.isoformat()}\n'
        f'expected_et_date = {expected_date.isoformat()}'
    )

    assert session_date == expected_date


def test_cold_bar_source_loads_verify_window() -> None:
    p_parquet, sym = _require_cold_symbol_parquet()

    source = ColdBarSource(
        OHLCV_COLD_VERIFY_START_DATE,
        OHLCV_COLD_VERIFY_END_DATE,
        warmup_bars=WARMUP_BAR_COUNT,
    )
    frame = source.load(sym)
    df_warmup = source.warmup_bars(sym)

    if frame.bars.empty:
        pytest.skip(
            f'No analysis rows for {sym} in '
            f'{OHLCV_COLD_VERIFY_START_DATE.isoformat()}..{OHLCV_COLD_VERIFY_END_DATE.isoformat()}',
        )

    session_dates = trading_date_series_utc(frame.bars.timestamp)
    in_range = (session_dates >= OHLCV_COLD_VERIFY_START_DATE) & (
        session_dates <= OHLCV_COLD_VERIFY_END_DATE
    )
    analysis_rows = len(frame.bars)
    warmup_rows = len(df_warmup)
    first_analysis_ts = frame.bars.timestamp.min()
    warmup_before_analysis = (
        df_warmup.timestamp.max() < first_analysis_ts if warmup_rows else True
    )
    warmup_within_cap = warmup_rows <= WARMUP_BAR_COUNT
    cols_ok = list(frame.bars.columns) == list(OHLCV_COLD_PARQUET_COLUMNS)
    tz_ok = frame.bars.timestamp.dt.tz is not None
    sorted_ok = frame.bars.timestamp.is_monotonic_increasing
    symbol_ok = frame.symbol == sym.upper()
    window_label = (
        f'{OHLCV_COLD_VERIFY_START_DATE.isoformat()}..{OHLCV_COLD_VERIFY_END_DATE.isoformat()}'
    )

    print(
        '**summary for ColdBarSource verify-window load:**\n'
        f'{sym} | {window_label} | load_ok = {analysis_rows > 0}\n'
        f'parquet_path = {p_parquet}\n'
        f'analysis_row_count = {analysis_rows}\n'
        f'warmup_row_count = {warmup_rows}\n'
        f'all_session_dates_in_range = {bool(in_range.all())}\n'
        f'warmup_strictly_before_analysis = {warmup_before_analysis}\n'
        f'warmup_within_cap = {warmup_within_cap}\n'
        f'schema_columns_match = {cols_ok}\n'
        f'timestamp_tz_present = {tz_ok}\n'
        f'timestamps_sorted = {sorted_ok}\n'
        f'symbol_ok = {symbol_ok}'
    )

    assert in_range.all()
    assert warmup_before_analysis
    assert warmup_within_cap
    assert cols_ok
    assert tz_ok
    assert sorted_ok
    assert symbol_ok


def test_cold_bar_source_single_session_day() -> None:
    p_parquet, sym = _require_cold_symbol_parquet()
    maybe_day = _session_date_with_rows(p_parquet)
    if maybe_day is None:
        pytest.skip(f'No bars in verify window for {p_parquet}')
    assert maybe_day is not None
    session_day = maybe_day

    source = ColdBarSource(session_day, session_day, warmup_bars=0)
    frame = source.load(sym)
    if frame.bars.empty:
        pytest.skip(f'No rows for {sym} on session date {session_day.isoformat()}')

    session_dates = trading_date_series_utc(frame.bars.timestamp)
    unique_days = sorted(session_dates.unique())
    day_count = len(unique_days)

    print(
        '**summary for single-session-day ColdBarSource load:**\n'
        f'{sym} | {session_day.isoformat()} | day_count = {day_count}\n'
        f'analysis_row_count = {len(frame.bars)}\n'
        f'unique_session_dates = {[d.isoformat() for d in unique_days]}'
    )

    assert day_count == 1
    assert unique_days[0] == session_day


def _latest_session_date_with_rows(p_parquet: Path) -> date | None:
    """Latest ET session date with bars in the verify window (recent history for ADR/ATR)."""
    df_raw = pd.read_parquet(p_parquet, columns=['timestamp'])
    if df_raw.empty:
        return None
    ts = pd.to_datetime(df_raw.timestamp, utc=True)
    mask = (ts.dt.date >= OHLCV_COLD_VERIFY_START_DATE) & (ts.dt.date <= OHLCV_COLD_VERIFY_END_DATE)
    df_in = df_raw.loc[mask]
    if df_in.empty:
        return None
    session_dates = trading_date_series_utc(df_in.timestamp)
    return session_dates.iloc[-1]


def test_cold_bar_source_daily_bars_support_adr_atr_on_single_day() -> None:
    """Daily history must extend beyond 1m warmup so ADR/ATR are not all NaN."""
    p_parquet, sym = _require_cold_symbol_parquet()
    maybe_day = _latest_session_date_with_rows(p_parquet)
    if maybe_day is None:
        pytest.skip(f'No bars in verify window for {p_parquet}')
    assert maybe_day is not None
    session_day = maybe_day

    source = ColdBarSource(session_day, session_day, warmup_bars=100)
    frame = source.load(sym)
    if frame.bars.empty:
        pytest.skip(f'No rows for {sym} on session date {session_day.isoformat()}')

    daily_rows = 0 if frame.daily_bars is None else len(frame.daily_bars)
    lookback_days = daily_bar_lookback_calendar_days()
    enriched = IndicatorPipeline(('trading_date', 'adr', 'atr')).run(frame)
    adr_non_null = int(enriched.bars.adr.notna().sum())
    atr_non_null = int(enriched.bars.atr.notna().sum())
    bar_count = len(enriched.bars)
    adr_populated = adr_non_null == bar_count and bar_count > 0
    atr_populated = atr_non_null == bar_count and bar_count > 0

    print(
        '**summary for daily_bars ADR/ATR on single session day:**\n'
        f'{sym} | {session_day.isoformat()} | lookback_calendar_days = {lookback_days}\n'
        f'daily_row_count = {daily_rows} | analysis_bars = {bar_count}\n'
        f'adr_non_null = {adr_non_null} | atr_non_null = {atr_non_null}\n'
        f'adr_all_populated = {adr_populated} | atr_all_populated = {atr_populated}'
    )

    assert daily_rows >= 16
    assert adr_populated
    assert atr_populated


def test_cold_bar_source_history_bars_support_rvol_on_single_day() -> None:
    """1m history must extend beyond warmup cushion so RVOL is not all NaN."""
    p_parquet, sym = _require_cold_symbol_parquet()
    maybe_day = _latest_session_date_with_rows(p_parquet)
    if maybe_day is None:
        pytest.skip(f'No bars in verify window for {p_parquet}')
    assert maybe_day is not None
    session_day = maybe_day

    source = ColdBarSource(session_day, session_day, warmup_bars=100)
    frame = source.load(sym)
    if frame.bars.empty:
        pytest.skip(f'No rows for {sym} on session date {session_day.isoformat()}')

    hist = frame.history_bars
    assert hist is not None
    hist_dates = trading_date_series_utc(hist.timestamp).nunique()
    min_sessions = min_history_sessions_for_indicators()
    lookback_days = history_bar_lookback_calendar_days()

    enriched = IndicatorPipeline(('trading_date', 'rvol', 'rvol_time')).run(frame)
    ts_et = pd.to_datetime(enriched.bars.timestamp, utc=True).dt.tz_convert(exchange_timezone_name())
    rth_mask = (ts_et.dt.hour == 9) & (ts_et.dt.minute >= 30) & (ts_et.dt.minute < 40)
    rth = enriched.bars.loc[rth_mask]
    rvol_non_null = int(rth.rvol.notna().sum())
    rvol_time_non_null = int(rth.rvol_time.notna().sum())
    rth_count = len(rth)

    print(
        '**summary for history_bars RVOL on single session day:**\n'
        f'{sym} | {session_day.isoformat()} | lookback_calendar_days = {lookback_days}\n'
        f'hist_session_dates = {hist_dates} | min_sessions = {min_sessions}\n'
        f'rth_open_10m | rvol_non_null = {rvol_non_null} | rvol_time_non_null = {rvol_time_non_null} '
        f'| bars = {rth_count}'
    )

    assert hist_dates >= min_sessions
    assert rth_count > 0
    assert rvol_non_null > 0
    assert rvol_time_non_null > 0


def test_cold_bar_source_missing_parquet_raises() -> None:
    _require_cold_symbol_parquet()
    source = ColdBarSource(
        OHLCV_COLD_VERIFY_START_DATE,
        OHLCV_COLD_VERIFY_END_DATE,
    )
    with pytest.raises(FileNotFoundError):
        source.load(MISSING_SYMBOL)
