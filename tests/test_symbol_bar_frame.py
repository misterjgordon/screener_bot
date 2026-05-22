"""SymbolBarFrame: column contract, with_columns, and to_parquet on ingested cold data.

Requires ``OHLCV_COLD_ROOT`` populated by ``scripts/ingest_ohlcv_cold.py``.

uv run --frozen pytest tests/test_symbol_bar_frame.py -v
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from backtesting.frames.column_contract import ColumnContractError
from backtesting.frames.column_contract import check_raw_contract
from backtesting.io.cold_bar_source import ColdBarSource
from trading import config as cf
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_START_DATE
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_paths import symbol_path
from trading.storage.ohlcv.ohlcv_schema import OHLCV_COLD_PARQUET_COLUMNS

OHLCV_TEST_SYMBOL_ENV = 'OHLCV_TEST_SYMBOL'
OHLCV_COLD_VERIFY_START_DATE = OHLCV_DEFAULT_INGEST_START_DATE
OHLCV_COLD_VERIFY_END_DATE = OHLCV_DEFAULT_INGEST_END_DATE


def _require_cold_symbol(monkeypatch: pytest.MonkeyPatch | None = None) -> tuple[str, 'ColdBarSource']:
    if not cf.OHLCV_COLD_ROOT.strip():
        pytest.skip('Set OHLCV_COLD_ROOT so tests can read ingested Parquet')
    sym_env = os.environ.get(OHLCV_TEST_SYMBOL_ENV, '').strip().upper()
    if sym_env:
        if not symbol_path(sym_env).is_file():
            pytest.skip(f'Missing cold Parquet for {sym_env}')
        sym: str = sym_env
    else:
        tickers = load_tickers_from_symbol_list_file(get_p_ohlcv_symbol_list_path())
        found = next((s for s in tickers if symbol_path(s).is_file()), None)
        if found is None:
            pytest.skip('No cold Parquet files found; run ingest first')
        assert found is not None
        sym = found
    source = ColdBarSource(
        OHLCV_COLD_VERIFY_START_DATE,
        OHLCV_COLD_VERIFY_END_DATE,
        warmup_bars=0,
    )
    return sym, source


def test_symbol_bar_frame_raw_contract_passes() -> None:
    sym, source = _require_cold_symbol()
    frame = source.load(sym)
    if frame.bars.empty:
        pytest.skip(f'No rows for {sym} in verify window')

    try:
        check_raw_contract(frame.bars)
        contract_ok = True
        error_msg = ''
    except ColumnContractError as exc:
        contract_ok = False
        error_msg = str(exc)

    print(
        '**summary for SymbolBarFrame raw contract:**\n'
        f'{sym} | contract_ok = {contract_ok}\n'
        f'columns = {frame.column_names}\n'
        f'error = {error_msg or "none"}'
    )

    assert contract_ok, error_msg


def test_symbol_bar_frame_column_names_match_bars() -> None:
    sym, source = _require_cold_symbol()
    frame = source.load(sym)
    if frame.bars.empty:
        pytest.skip(f'No rows for {sym} in verify window')

    names_match = frame.column_names == list(frame.bars.columns)
    raw_cols_present = all(c in frame.column_names for c in OHLCV_COLD_PARQUET_COLUMNS)

    print(
        '**summary for SymbolBarFrame column_names:**\n'
        f'{sym} | names_match = {names_match} | raw_cols_present = {raw_cols_present}\n'
        f'column_names = {frame.column_names}'
    )

    assert names_match
    assert raw_cols_present


def test_symbol_bar_frame_with_columns_adds_derived_column() -> None:
    sym, source = _require_cold_symbol()
    frame = source.load(sym)
    if frame.bars.empty:
        pytest.skip(f'No rows for {sym} in verify window')

    mid = (frame.bars.high + frame.bars.low) / 2
    enriched = frame.with_columns(mid_price=mid)

    added = 'mid_price' in enriched.column_names
    original_unchanged = 'mid_price' not in frame.column_names
    symbol_preserved = enriched.symbol == frame.symbol
    interval_preserved = enriched.interval_minutes == frame.interval_minutes
    rows_preserved = len(enriched.bars) == len(frame.bars)

    print(
        '**summary for SymbolBarFrame.with_columns:**\n'
        f'{sym} | added = {added} | original_unchanged = {original_unchanged}\n'
        f'symbol_preserved = {symbol_preserved}\n'
        f'interval_preserved = {interval_preserved}\n'
        f'rows_preserved = {rows_preserved}'
    )

    assert added
    assert original_unchanged
    assert symbol_preserved
    assert interval_preserved
    assert rows_preserved


def test_symbol_bar_frame_to_parquet_round_trip(tmp_path: Path) -> None:
    sym, source = _require_cold_symbol()
    frame = source.load(sym)
    if frame.bars.empty:
        pytest.skip(f'No rows for {sym} in verify window')

    p_out = tmp_path / f'{sym}.parquet'
    frame.to_parquet(p_out)
    df_read = pd.read_parquet(p_out)

    rows_match = len(df_read) == len(frame.bars)
    cols_match = list(df_read.columns) == list(frame.bars.columns)

    print(
        '**summary for SymbolBarFrame.to_parquet:**\n'
        f'{sym} | rows_match = {rows_match} | cols_match = {cols_match}\n'
        f'row_count = {len(frame.bars)}'
    )

    assert rows_match
    assert cols_match


def test_check_raw_contract_raises_on_missing_column() -> None:
    sym, source = _require_cold_symbol()
    frame = source.load(sym)
    if frame.bars.empty:
        pytest.skip(f'No rows for {sym} in verify window')

    df_stripped = frame.bars.drop(columns=['close'])

    raised = False
    try:
        check_raw_contract(df_stripped)
    except ColumnContractError:
        raised = True

    print(
        '**summary for check_raw_contract missing column:**\n'
        f'{sym} | raised_on_missing_close = {raised}'
    )

    assert raised
