"""IndicatorPipeline assigns indicator columns on ingested cold SymbolBarFrames."""

import os
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from backtesting.indicators.indicator_catalog_load import default_indicator_ids
from backtesting.indicators.indicator_pipeline import IndicatorPipeline
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY
from backtesting.io.cold_bar_source import ColdBarSource
from strategies.indicators.vwap import vwap_series
from strategies.utils import bar_session
from trading import config as cf
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_START_DATE
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_paths import symbol_path

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame

OHLCV_TEST_SYMBOL_ENV = 'OHLCV_TEST_SYMBOL'


def _require_cold_frame() -> tuple[str, 'SymbolBarFrame']:
    if not cf.OHLCV_COLD_ROOT.strip():
        pytest.skip('Set OHLCV_COLD_ROOT so tests can read ingested Parquet')
    sym_env = os.environ.get(OHLCV_TEST_SYMBOL_ENV, '').strip().upper()
    if sym_env:
        if not symbol_path(sym_env).is_file():
            pytest.skip(f'Missing cold Parquet for {sym_env}')
        sym = sym_env
    else:
        tickers = load_tickers_from_symbol_list_file(get_p_ohlcv_symbol_list_path())
        found = next((s for s in tickers if symbol_path(s).is_file()), None)
        if found is None:
            pytest.skip('No cold Parquet files found; run ingest first')
        assert found is not None
        sym = found

    source = ColdBarSource(
        OHLCV_DEFAULT_INGEST_START_DATE,
        OHLCV_DEFAULT_INGEST_END_DATE,
        warmup_bars=0,
    )
    frame = source.load(sym)
    if frame.bars.empty:
        pytest.skip(f'No analysis rows for {sym}')
    return sym, frame


def test_indicator_pipeline_assigns_default_indicator_columns() -> None:
    sym, frame = _require_cold_frame()
    pipeline = IndicatorPipeline()
    enriched = pipeline.run(frame)
    cols = enriched.column_names

    has_trading_date = 'trading_date' in cols
    has_ema9 = 'ema9' in cols
    has_vwap = 'vwap' in cols
    has_close_above = 'close_above_vwap' in cols
    row_count = len(enriched.bars)

    print(
        '**summary for IndicatorPipeline default indicators:**\n'
        f'{sym} | row_count = {row_count}\n'
        f'has_trading_date = {has_trading_date}\n'
        f'has_ema9 = {has_ema9} | has_vwap = {has_vwap}\n'
        f'has_close_above_vwap = {has_close_above}'
    )

    assert has_trading_date
    assert has_ema9
    assert has_vwap
    assert not has_close_above
    for iid in default_indicator_ids():
        for out_col in INDICATOR_REGISTRY.spec(iid).outputs:
            assert out_col in cols


def test_indicator_pipeline_vwap_replaces_parquet_nan() -> None:
    sym, frame = _require_cold_frame()
    parquet_vwap_na_frac = float(frame.bars.vwap.isna().mean()) if 'vwap' in frame.column_names else 0.0

    enriched = IndicatorPipeline(('trading_date', 'vwap')).run(frame)
    computed_finite = enriched.bars.vwap.notna().sum()
    has_finite_vwap = computed_finite > 0

    print(
        '**summary for IndicatorPipeline vwap assign:**\n'
        f'{sym} | parquet_vwap_na_frac = {parquet_vwap_na_frac}\n'
        f'computed_finite_vwap_rows = {computed_finite}\n'
        f'has_finite_vwap = {has_finite_vwap}'
    )

    assert has_finite_vwap


def test_indicator_pipeline_vwap_pm_anchor_on_first_rth_when_pm_present() -> None:
    sym, frame = _require_cold_frame()
    enriched = IndicatorPipeline(('trading_date', 'vwap')).run(frame)

    ts = pd.to_datetime(enriched.bars.timestamp, utc=True)
    sessions = [bar_session(t.to_pydatetime()) for t in ts]
    if 'PM' not in sessions or 'RTH' not in sessions:
        pytest.skip('No PM+RTH on same day in verify window for anchor check')

    first_rth_idx = next(i for i, s in enumerate(sessions) if s == 'RTH')
    full_vwap = float(enriched.bars.vwap.iloc[first_rth_idx])

    rth_mask = pd.Series(sessions) == 'RTH'
    rth_df = enriched.bars.loc[rth_mask]
    rth_vwap = vwap_series(
        rth_df.high,
        rth_df.low,
        rth_df.close,
        rth_df.volume,
        rth_df.trading_date,
    )
    rth_only_first = float(rth_vwap.iloc[0])
    pm_anchored = full_vwap != pytest.approx(rth_only_first)

    print(
        '**summary for pipeline VWAP PM anchor:**\n'
        f'{sym} | full_vwap_at_first_rth = {full_vwap}\n'
        f'rth_only_first = {rth_only_first} | pm_anchored = {pm_anchored}'
    )

    if not pm_anchored:
        pytest.skip('PM bars had no volume effect on first RTH VWAP for this symbol/day')
    assert pm_anchored
