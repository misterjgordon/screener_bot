"""UniverseBarFrames: get, symbols, map, multiindex, export on ingested cold data.

Requires ``OHLCV_COLD_ROOT`` populated by ``scripts/ingest_ohlcv_cold.py``.
Uses up to two symbols from the shortlist so tests stay fast.

uv run --frozen pytest tests/test_universe_bar_frames.py -v
"""

import os
from pathlib import Path

import pytest

from backtesting.frames.universe_bar_frames import UniverseBarFrames
from backtesting.io.cold_bar_source import ColdBarSource
from trading import config as cf
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_START_DATE
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_paths import symbol_path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame

OHLCV_TEST_SYMBOL_ENV = 'OHLCV_TEST_SYMBOL'
OHLCV_COLD_VERIFY_START_DATE = OHLCV_DEFAULT_INGEST_START_DATE
OHLCV_COLD_VERIFY_END_DATE = OHLCV_DEFAULT_INGEST_END_DATE
MAX_TEST_SYMBOLS = 2


def _require_cold_universe() -> UniverseBarFrames:
    """Load up to MAX_TEST_SYMBOLS frames from ingested cold store; skip if unavailable."""
    if not cf.OHLCV_COLD_ROOT.strip():
        pytest.skip('Set OHLCV_COLD_ROOT so tests can read ingested Parquet')

    sym_env = os.environ.get(OHLCV_TEST_SYMBOL_ENV, '').strip().upper()
    if sym_env:
        candidates = [sym_env]
    else:
        tickers = load_tickers_from_symbol_list_file(get_p_ohlcv_symbol_list_path())
        candidates = [s for s in tickers if symbol_path(s).is_file()][:MAX_TEST_SYMBOLS]

    if not candidates:
        pytest.skip('No cold Parquet files found; run ingest first')

    source = ColdBarSource(
        OHLCV_COLD_VERIFY_START_DATE,
        OHLCV_COLD_VERIFY_END_DATE,
        warmup_bars=0,
    )
    frames = [source.load(sym) for sym in candidates]
    non_empty = [f for f in frames if not f.bars.empty]
    if not non_empty:
        pytest.skip('All loaded frames are empty for the verify window')

    return UniverseBarFrames.from_list(non_empty)


def test_universe_bar_frames_symbols_sorted() -> None:
    universe = _require_cold_universe()
    syms = universe.symbols
    symbol_count = len(syms)
    sorted_ok = syms == sorted(syms)

    print(
        '**summary for UniverseBarFrames.symbols:**\n'
        f'symbol_count = {symbol_count} | sorted_ok = {sorted_ok}\n'
        f'symbols = {syms}'
    )

    assert symbol_count >= 1
    assert sorted_ok


def test_universe_bar_frames_get_returns_correct_frame() -> None:
    universe = _require_cold_universe()
    sym = universe.symbols[0]
    frame = universe.get(sym)

    symbol_matches = frame.symbol == sym
    has_bars = not frame.bars.empty

    print(
        '**summary for UniverseBarFrames.get:**\n'
        f'{sym} | symbol_matches = {symbol_matches} | has_bars = {has_bars}\n'
        f'row_count = {len(frame.bars)}'
    )

    assert symbol_matches
    assert has_bars


def test_universe_bar_frames_get_raises_on_unknown_symbol() -> None:
    universe = _require_cold_universe()

    raised = False
    try:
        universe.get('NOTASYMBOL999')
    except KeyError:
        raised = True

    print(
        f'**summary for UniverseBarFrames.get unknown symbol:**\n'
        f'raised_key_error = {raised}'
    )

    assert raised


def test_universe_bar_frames_map_applies_fn_to_all_frames() -> None:
    universe = _require_cold_universe()

    def add_mid(frame: 'SymbolBarFrame') -> 'SymbolBarFrame':
        mid = (frame.bars.high + frame.bars.low) / 2
        return frame.with_columns(mid_price=mid)

    enriched = universe.map(add_mid)

    syms_match = enriched.symbols == universe.symbols
    all_have_mid = all('mid_price' in enriched.get(s).column_names for s in enriched.symbols)
    original_unchanged = all('mid_price' not in universe.get(s).column_names for s in universe.symbols)

    print(
        '**summary for UniverseBarFrames.map:**\n'
        f'symbols = {universe.symbols} | syms_match = {syms_match}\n'
        f'all_have_mid = {all_have_mid}\n'
        f'original_unchanged = {original_unchanged}'
    )

    assert syms_match
    assert all_have_mid
    assert original_unchanged


def test_universe_bar_frames_to_dataframe_multiindex_shape() -> None:
    universe = _require_cold_universe()
    df_multi = universe.to_dataframe_multiindex()

    index_names = list(df_multi.index.names)
    symbol_levels = df_multi.index.get_level_values('symbol').unique().tolist()
    symbols_present = sorted(symbol_levels) == universe.symbols
    total_rows = len(df_multi)
    expected_rows = sum(len(universe.get(s).bars) for s in universe.symbols)
    rows_match = total_rows == expected_rows

    print(
        '**summary for UniverseBarFrames.to_dataframe_multiindex:**\n'
        f'symbols = {universe.symbols} | index_names = {index_names}\n'
        f'symbols_present = {symbols_present}\n'
        f'rows_match = {rows_match} (expected={expected_rows}, got={total_rows})'
    )

    assert index_names == ['symbol', 'timestamp']
    assert symbols_present
    assert rows_match


def test_universe_bar_frames_export_dir_writes_parquet_per_symbol(tmp_path: Path) -> None:
    universe = _require_cold_universe()
    written = universe.export_dir(tmp_path)

    expected_names = {f'{sym}.parquet' for sym in universe.symbols}
    written_names = {p.name for p in written}
    files_exist = all(p.is_file() for p in written)
    names_match = written_names == expected_names

    print(
        '**summary for UniverseBarFrames.export_dir:**\n'
        f'symbols = {universe.symbols} | names_match = {names_match}\n'
        f'files_exist = {files_exist}\n'
        f'written = {[p.name for p in written]}'
    )

    assert names_match
    assert files_exist
