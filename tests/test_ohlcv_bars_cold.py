"""
Cold OHLCV: Massive request shape, live fetch schema, Parquet round-trip, and on-disk checks.
Massive ingest leaves ``vwap`` unset (NaN); OHLCV is still compared for cold vs refetch.

- ``scripts/ingest_ohlcv_cold.py`` caps **symbol-days** (UTC inclusive calendar span ×
  symbol count) unless ``--allow-high-volume-ingest``; use ``--max-symbols`` for smoke runs.
- On-disk tests only use rows in ``OHLCV_COLD_VERIFY_START_DATE``..``OHLCV_COLD_VERIFY_END_DATE``
  (same calendar bounds as ``OHLCV_DEFAULT_INGEST_*`` in ``ohlcv_ingest_limits`` / ingest script defaults).
- ``test_etl_writes_parquet_to_cold_root_for_verify_window`` performs real ETL into
  ``OHLCV_COLD_ROOT`` (needs ``MASSIVE_API_KEY``); run it before expecting on-disk tests to pass.

uv run --frozen pytest tests/test_ohlcv_bars_cold.py -v
"""

import csv
import os
from datetime import UTC
from datetime import datetime
from datetime import time
from datetime import timedelta
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from trading import config as cf
from trading.integrations.massive_bars import fetch_stock_minute_bars_dataframe
from trading.integrations.massive_bars import massive_minute_aggs_first_page_url
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_START_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import symbol_day_ingest_cost
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_paths import p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import symbol_path
from trading.storage.ohlcv.ohlcv_prepare import validate_and_prepare
from trading.storage.ohlcv.ohlcv_schema import BAR_FRAME_COLUMNS
from trading.storage.ohlcv.ohlcv_schema import OHLCV_COLD_PARQUET_COLUMNS
from trading.storage.ohlcv.ohlcv_schema import empty_bars_dataframe
from trading.storage.ohlcv.ohlcv_symbol_store import write_bars

OHLCV_TEST_SYMBOL_ENV = 'OHLCV_TEST_SYMBOL'
SAMPLE_INDEX_COUNT = 3
# First N symbols from the ticker list to ETL into OHLCV_COLD_ROOT in ``test_etl_writes_...``.
OHLCV_ETL_TEST_SYMBOL_COUNT = 5

# Inclusive calendar days for cold-store integration checks (UTC day bounds).
# Same as ``OHLCV_DEFAULT_INGEST_*`` in trading.storage.ohlcv.ohlcv_ingest_limits (ingest defaults).
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


def _verify_window_utc_bounds() -> tuple[datetime, datetime]:
    """Return [start, end) UTC bounds for rows that fall on VERIFY_* calendar dates."""
    start = datetime.combine(OHLCV_COLD_VERIFY_START_DATE, time.min, tzinfo=UTC)
    end_exclusive = datetime.combine(
        OHLCV_COLD_VERIFY_END_DATE + timedelta(days=1),
        time.min,
        tzinfo=UTC,
    )
    return start, end_exclusive


def _massive_fetch_bounds_for_verify_dates() -> tuple[datetime, datetime]:
    """UTC inclusive window for Massive: start of first verify day through end of last verify day."""
    fetch_start = datetime.combine(OHLCV_COLD_VERIFY_START_DATE, time.min, tzinfo=UTC)
    fetch_end = datetime.combine(OHLCV_COLD_VERIFY_END_DATE, time.max, tzinfo=UTC)
    return fetch_start, fetch_end


def _df_rows_in_verify_window(df_bars: pd.DataFrame) -> pd.DataFrame:
    start, end_exclusive = _verify_window_utc_bounds()
    ts = pd.to_datetime(df_bars.timestamp, utc=True)
    return df_bars.loc[(ts >= start) & (ts < end_exclusive)].copy()


def _sample_row_indices(n_rows: int) -> list[int]:
    if n_rows <= 0:
        return []
    if n_rows == 1:
        return [0]
    mid = n_rows // 2
    raw = [0, mid, n_rows - 1]
    out: list[int] = []
    for i in raw:
        if i not in out:
            out.append(i)
    return out[: min(SAMPLE_INDEX_COUNT, n_rows)]


def _ohlc_stack_consistent(df_bars: pd.DataFrame) -> bool:
    stack = df_bars.loc[:, ['open', 'high', 'low', 'close']].to_numpy(dtype='float64')
    highs = stack.max(axis=1)
    lows = stack.min(axis=1)
    return bool((df_bars.high.to_numpy() == highs).all() and (df_bars.low.to_numpy() == lows).all())


def _float_or_both_na(a: object, b: object) -> bool:
    a_na = pd.isna(a)
    b_na = pd.isna(b)
    if a_na and b_na:
        return True
    if a_na or b_na:
        return False
    af = float(cast('int | float', a))
    bf = float(cast('int | float', b))
    return af == pytest.approx(bf)


def _cold_volume_matches_api_volume(cold_vol: object, api_vol: object) -> bool:
    """Cold store rounds ingest volume to int32; API returns float shares."""
    a_na = pd.isna(cold_vol)
    b_na = pd.isna(api_vol)
    if a_na and b_na:
        return True
    if a_na or b_na:
        return False
    api_rounded = int(round(float(cast('int | float', api_vol))))
    return int(cast('int | float', cold_vol)) == api_rounded


def test_bar_frame_columns_order() -> None:
    assert BAR_FRAME_COLUMNS[0] == 'symbol'
    assert BAR_FRAME_COLUMNS[-1] == 'vwap'


def test_empty_bars_dataframe_columns() -> None:
    df_empty = empty_bars_dataframe()
    assert list(df_empty.columns) == list(BAR_FRAME_COLUMNS)


def test_load_tickers_from_default_shortlist_resolves() -> None:
    p_list = p_ohlcv_symbol_list_path.resolve()
    assert p_list.name == 'shortlist_stocks.csv'
    tickers = load_tickers_from_symbol_list_file(p_list)
    with p_list.open(encoding='utf-8-sig', newline='') as csv_file:
        rows = list(csv.reader(csv_file))
    if len(rows) < 3:
        pytest.skip(f'{p_list} must have a header row plus at least two tickers')
    sym_from_csv_a = rows[1][0].strip().upper()
    sym_from_csv_b = rows[2][0].strip().upper()
    order_ok = tickers[0] == sym_from_csv_a and tickers[1] == sym_from_csv_b
    has_a = sym_from_csv_a in tickers
    has_b = sym_from_csv_b in tickers
    print(
        '**summary for cold OHLCV symbol list load:**\n'
        f'{sym_from_csv_a} | {sym_from_csv_b} | shortlist_resolves = '
        f'{order_ok and has_a and has_b}\n'
        f'list_path = {p_list}\n'
        f'ticker_count = {len(tickers)}\n'
        f'first_two_match_csv_order = {order_ok}\n'
        f'{sym_from_csv_a}_present = {has_a}\n'
        f'{sym_from_csv_b}_present = {has_b}'
    )
    assert order_ok
    assert has_a
    assert has_b


def test_etl_writes_parquet_to_cold_root_for_verify_window() -> None:
    """Fetch Massive minute bars for VERIFY dates and write Parquet under OHLCV_COLD_ROOT."""
    if not cf.MASSIVE_API_KEY.strip():
        pytest.skip('Set MASSIVE_API_KEY to run cold-root ETL from this test')
    if not cf.OHLCV_COLD_ROOT.strip():
        pytest.skip('Set OHLCV_COLD_ROOT to the directory where Parquet should be written')

    p_list = get_p_ohlcv_symbol_list_path()
    tickers = load_tickers_from_symbol_list_file(p_list)[:OHLCV_ETL_TEST_SYMBOL_COUNT]
    fetch_start, fetch_end = _massive_fetch_bounds_for_verify_dates()

    written_syms: list[str] = []
    total_rows = 0
    for sym in tickers:
        df_bars = fetch_stock_minute_bars_dataframe(sym, fetch_start, fetch_end, interval_minutes=1)
        if df_bars.empty:
            continue
        write_bars(df_bars, interval_minutes=1)
        written_syms.append(sym)
        total_rows += len(df_bars)

    if not written_syms:
        pytest.fail(
            f'Massive returned no rows for any of {tickers} in [{fetch_start}, {fetch_end}]; '
            'adjust OHLCV_COLD_VERIFY_*_DATE to liquid US equity session days',
        )

    paths_ok = all(symbol_path(s, interval_minutes=1).is_file() for s in written_syms)
    window_label = (
        f'{OHLCV_COLD_VERIFY_START_DATE.isoformat()}..{OHLCV_COLD_VERIFY_END_DATE.isoformat()}'
    )
    syms_joined = ','.join(written_syms)
    print(
        '**summary for cold-root ETL (Massive → Parquet):**\n'
        f'{syms_joined} | {window_label} | etl_ok = {paths_ok}\n'
        f'cold_root = {cf.OHLCV_COLD_ROOT}\n'
        f'symbols_attempted = {len(tickers)}\n'
        f'symbols_written = {len(written_syms)}\n'
        f'total_rows_written = {total_rows}\n'
        f'fetch_start_utc = {fetch_start}\n'
        f'fetch_end_utc = {fetch_end}'
    )

    assert paths_ok


def test_ingested_cold_parquet_schema_and_invariants() -> None:
    p_parquet, sym = _require_cold_symbol_parquet()
    df_raw = pd.read_parquet(p_parquet)
    if df_raw.empty:
        pytest.skip(f'Parquet is empty: {p_parquet}')

    df_raw = _df_rows_in_verify_window(df_raw)
    if df_raw.empty:
        pytest.skip(
            f'No rows in [{OHLCV_COLD_VERIFY_START_DATE.isoformat()}..'
            f'{OHLCV_COLD_VERIFY_END_DATE.isoformat()}] UTC in {p_parquet}; '
            'ingest that window or widen OHLCV_COLD_VERIFY_*_DATE',
        )

    df_norm = validate_and_prepare(df_raw)
    cols_ok = list(df_norm.columns) == list(OHLCV_COLD_PARQUET_COLUMNS)
    tz_ok = df_norm.timestamp.dt.tz is not None
    no_dupes = not df_norm.duplicated(subset=['symbol', 'timestamp']).any()
    sorted_ok = df_norm.timestamp.is_monotonic_increasing
    sym_ok = (df_norm.symbol.astype(str).str.upper() == sym.upper()).all()
    no_interval_col = 'interval' not in df_norm.columns
    ohlc_f32_ok = all(df_norm[c].dtype == 'float32' for c in ('open', 'high', 'low', 'close', 'vwap'))
    volume_i32_ok = df_norm.volume.dtype == 'int32'
    ohlc_ok = _ohlc_stack_consistent(df_norm)
    vol_ok = (df_norm.volume >= 0).all()
    prep_rows_ok = len(df_norm) == len(df_raw)

    ts_min = df_norm.timestamp.min()
    ts_max = df_norm.timestamp.max()
    expected_window = (
        f'{OHLCV_COLD_VERIFY_START_DATE.isoformat()}..'
        f'{OHLCV_COLD_VERIFY_END_DATE.isoformat()}'
    )
    actual_span = f'{ts_min.date().isoformat()}..{ts_max.date().isoformat()}'

    all_ok = all(
        (
            cols_ok,
            tz_ok,
            no_dupes,
            sorted_ok,
            sym_ok,
            no_interval_col,
            ohlc_f32_ok,
            volume_i32_ok,
            ohlc_ok,
            vol_ok,
            prep_rows_ok,
        ),
    )
    print(
        '**summary for ingested cold OHLCV Parquet:**\n'
        f'{sym} | {expected_window} | cold_invariants_ok = {all_ok}\n'
        f'parquet_path = {p_parquet}\n'
        f'timestamp_span_in_filtered_rows = {actual_span}\n'
        f'row_count = {len(df_norm)}\n'
        f'schema_columns_match = {cols_ok}\n'
        f'timestamp_tz_present = {tz_ok}\n'
        f'no_duplicate_symbol_timestamp = {no_dupes}\n'
        f'timestamps_sorted = {sorted_ok}\n'
        f'symbol_column_matches_file = {sym_ok}\n'
        f'no_interval_column_on_disk = {no_interval_col}\n'
        f'ohlc_vwap_float32 = {ohlc_f32_ok}\n'
        f'volume_int32 = {volume_i32_ok}\n'
        f'ohlc_high_low_consistent = {ohlc_ok}\n'
        f'volume_non_negative = {vol_ok}\n'
        f'validate_and_prepare_row_count_preserved = {prep_rows_ok}'
    )

    assert cols_ok
    assert tz_ok
    assert no_dupes
    assert sorted_ok
    assert sym_ok
    assert no_interval_col
    assert ohlc_f32_ok
    assert volume_i32_ok
    assert ohlc_ok
    assert vol_ok
    assert prep_rows_ok


def test_ingested_rows_match_massive_refetch_for_samples() -> None:
    if not cf.MASSIVE_API_KEY.strip():
        pytest.skip('Set MASSIVE_API_KEY to refetch Massive bars and compare to cold store')

    p_parquet, sym = _require_cold_symbol_parquet()
    df_cold = pd.read_parquet(p_parquet)
    if df_cold.empty:
        pytest.skip(f'Parquet is empty: {p_parquet}')

    df_cold = _df_rows_in_verify_window(df_cold)
    if df_cold.empty:
        pytest.skip(
            f'No rows in [{OHLCV_COLD_VERIFY_START_DATE.isoformat()}..'
            f'{OHLCV_COLD_VERIFY_END_DATE.isoformat()}] UTC in {p_parquet}; '
            'ingest that window or widen OHLCV_COLD_VERIFY_*_DATE',
        )

    df_cold = validate_and_prepare(df_cold)
    indices = _sample_row_indices(len(df_cold))
    matches_ok: list[bool] = []

    for idx in indices:
        row = df_cold.iloc[idx]
        ts = pd.Timestamp(row.timestamp)
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        else:
            ts = ts.tz_convert('UTC')
        start = ts.to_pydatetime()
        end = (ts + timedelta(minutes=2)).to_pydatetime()
        df_api = fetch_stock_minute_bars_dataframe(sym, start, end, interval_minutes=1)
        api_ts = pd.to_datetime(df_api.timestamp, utc=True)
        hit = df_api.loc[api_ts == ts]
        if hit.empty:
            pytest.skip(f'Massive returned no bar at {ts} for {sym}; cannot verify sample row')
        api_row = hit.iloc[0]
        vwap_refetch_unset = bool(pd.isna(api_row.vwap))
        row_ok = (
            _float_or_both_na(row.open, api_row.open)
            and _float_or_both_na(row.high, api_row.high)
            and _float_or_both_na(row.low, api_row.low)
            and _float_or_both_na(row.close, api_row.close)
            and _cold_volume_matches_api_volume(row.volume, api_row.volume)
            and vwap_refetch_unset
        )
        matches_ok.append(row_ok)
        print(
            '**summary for cold row vs Massive refetch:**\n'
            f'{sym} | {OHLCV_COLD_VERIFY_START_DATE.isoformat()}..'
            f'{OHLCV_COLD_VERIFY_END_DATE.isoformat()} | sample_row_matches_api = {row_ok}\n'
            f'parquet_path = {p_parquet}\n'
            f'sample_index = {idx}\n'
            f'cold_timestamp = {ts}\n'
            f'api_rows_in_window = {len(df_api)}\n'
            f'vwap_refetch_unset = {vwap_refetch_unset}'
        )

    assert all(matches_ok)


def test_symbol_day_ingest_cost_inclusive_utc_calendar() -> None:
    """Two UTC calendar days inclusive × 10 symbols = 20 symbol-days."""
    assert symbol_day_ingest_cost(
        datetime(2024, 6, 1, tzinfo=UTC),
        datetime(2024, 6, 2, tzinfo=UTC),
        10,
    ) == 20


def test_massive_minute_aggs_first_page_url_shape() -> None:
    p_list = p_ohlcv_symbol_list_path.resolve()
    tickers = load_tickers_from_symbol_list_file(p_list)
    sym = tickers[0]
    fetch_start = datetime.combine(OHLCV_COLD_VERIFY_START_DATE, time(14, 0), tzinfo=UTC)
    fetch_end = datetime.combine(OHLCV_COLD_VERIFY_START_DATE, time(14, 30), tzinfo=UTC)
    url = massive_minute_aggs_first_page_url(
        sym,
        fetch_start,
        fetch_end,
        base_url='https://api.massive.com',
    )
    has_path = f'/v2/aggs/ticker/{sym}/range/1/minute/' in url
    has_limit = 'limit=50000' in url
    has_sort = 'sort=asc' in url
    shape_ok = has_path and has_limit and has_sort
    print(
        '**summary for Massive aggs URL shape:**\n'
        f'{sym} | {OHLCV_COLD_VERIFY_START_DATE.isoformat()} | url_matches_contract = {shape_ok}\n'
        f'url = {url}\n'
        f'path_segment_ok = {has_path}\n'
        f'limit_param_ok = {has_limit}\n'
        f'sort_param_ok = {has_sort}'
    )
    assert has_path
    assert has_limit
    assert has_sort


def test_massive_live_fetch_write_read_parquet_roundtrip(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    if not cf.MASSIVE_API_KEY.strip():
        pytest.skip('Set MASSIVE_API_KEY for live Massive fetch + Parquet round-trip')

    monkeypatch.setattr(cf, 'OHLCV_COLD_ROOT', str(tmp_path))
    p_list = p_ohlcv_symbol_list_path.resolve()
    tickers = load_tickers_from_symbol_list_file(p_list)
    sym = tickers[0]
    fetch_start = datetime.combine(OHLCV_COLD_VERIFY_START_DATE, time(14, 0), tzinfo=UTC)
    fetch_end = datetime.combine(OHLCV_COLD_VERIFY_START_DATE, time(15, 0), tzinfo=UTC)

    df_fetched = fetch_stock_minute_bars_dataframe(sym, fetch_start, fetch_end, interval_minutes=1)
    if df_fetched.empty:
        pytest.skip(
            f'Massive returned no rows for {sym} in [{fetch_start}, {fetch_end}]; '
            'pick OHLCV_COLD_VERIFY_* on a liquid RTH day',
        )

    vwap_unset_ok = bool(df_fetched['vwap'].isna().all())
    cols_ok = list(df_fetched.columns) == list(BAR_FRAME_COLUMNS)
    tz_ok = df_fetched.timestamp.dt.tz is not None
    sym_col_ok = (df_fetched.symbol.astype(str).str.upper() == sym.upper()).all()
    write_bars(df_fetched, interval_minutes=1)
    p_written = symbol_path(sym, interval_minutes=1)
    assert p_written.is_file()
    df_read = pd.read_parquet(p_written)
    read_cols_ok = list(df_read.columns) == list(OHLCV_COLD_PARQUET_COLUMNS)
    df_read_norm = validate_and_prepare(df_read)
    read_rows_ok = len(df_read_norm) == len(df_fetched)
    roundtrip_ok = (
        vwap_unset_ok
        and cols_ok
        and tz_ok
        and sym_col_ok
        and read_cols_ok
        and read_rows_ok
    )

    print(
        '**summary for Massive fetch and Parquet round-trip:**\n'
        f'{sym} | {OHLCV_COLD_VERIFY_START_DATE.isoformat()} | roundtrip_ok = {roundtrip_ok}\n'
        f'fetch_row_count = {len(df_fetched)}\n'
        f'parquet_path = {p_written}\n'
        f'fetch_vwap_all_nan = {vwap_unset_ok}\n'
        f'fetch_columns_match_BAR_FRAME = {cols_ok}\n'
        f'parquet_columns_match_cold_schema = {read_cols_ok}\n'
        f'timestamp_tz_present = {tz_ok}\n'
        f'symbol_column_matches_ticker = {sym_col_ok}\n'
        f'read_row_count_matches_fetch = {read_rows_ok}'
    )

    assert vwap_unset_ok
    assert cols_ok
    assert tz_ok
    assert sym_col_ok
    assert read_cols_ok
    assert read_rows_ok
