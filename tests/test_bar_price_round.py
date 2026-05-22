"""Cent rounding and OHLC repair for loaded bar and indicator prices."""

import pandas as pd
import pytest

from backtesting.frames.bar_price_round import PRICE_DECIMALS
from backtesting.frames.bar_price_round import format_inspect_bar_table
from backtesting.frames.bar_price_round import inspect_bar_table_string
from backtesting.frames.bar_price_round import order_inspect_display_columns
from backtesting.frames.bar_price_round import round_loaded_bar_prices
from backtesting.frames.bar_price_round import round_ohlc_columns


def _prices_cent_aligned(series: pd.Series) -> bool:
    finite = series.dropna().astype('float64')
    if finite.empty:
        return True
    return bool((finite == finite.round(PRICE_DECIMALS)).all())


def test_round_ohlc_repairs_high_low_after_round() -> None:
    df = pd.DataFrame(
        {
            'open': [10.004],
            'high': [10.001],
            'low': [10.006],
            'close': [10.003],
        },
    ).astype('float32')
    df_out = round_ohlc_columns(df)
    hi = float(df_out.high.iloc[0])
    lo = float(df_out.low.iloc[0])
    o = float(df_out.open.iloc[0])
    c = float(df_out.close.iloc[0])
    high_ok = hi >= max(o, c, lo)
    low_ok = lo <= min(o, c, hi)

    print(
        '**summary for round_ohlc repair:**\n'
        f'open = {o} | high = {hi} | low = {lo} | close = {c}\n'
        f'high_ok = {high_ok} | low_ok = {low_ok}'
    )

    assert high_ok
    assert low_ok
    assert _prices_cent_aligned(df_out.open)
    assert _prices_cent_aligned(df_out.close)


def test_round_loaded_bar_prices_includes_vwap() -> None:
    df = pd.DataFrame(
        {
            'symbol': ['AAPL'],
            'timestamp': [pd.Timestamp('2026-05-15 14:00:00', tz='UTC')],
            'open': [100.001],
            'high': [101.006],
            'low': [99.004],
            'close': [100.559],
            'volume': [100],
            'vwap': [100.5555],
        },
    )
    df_out = round_loaded_bar_prices(df)
    vwap_val = float(df_out.vwap.iloc[0])
    close_val = float(df_out.close.iloc[0])
    vwap_cent = _prices_cent_aligned(df_out.vwap)
    close_cent = _prices_cent_aligned(df_out.close)

    print(
        '**summary for round_loaded_bar_prices:**\n'
        f'close = {close_val} | vwap = {vwap_val}\n'
        f'close_cent = {close_cent} | vwap_cent = {vwap_cent}'
    )

    assert close_val == pytest.approx(100.56)
    assert vwap_val == pytest.approx(100.56)
    assert df_out.open.dtype == 'float64'
    assert close_cent
    assert vwap_cent


def test_order_inspect_display_columns_prefers_ohlcv_first() -> None:
    cols = ('rvol', 'close', 'symbol', 'open', 'time')
    ordered = order_inspect_display_columns(cols)
    assert ordered[:2] == ('time', 'open')
    assert 'rvol' in ordered


def test_format_inspect_bar_table_aligns_large_volume_column() -> None:
    df = pd.DataFrame(
        {
            'symbol': ['AAPL'],
            'datetime_pt': ['2026-05-15 07:31'],
            'close': [299.73],
            'cumulative_avg_volume': [12_598_935.9],
            'signal_eligible': [True],
        },
    )
    df_fmt = format_inspect_bar_table(df)
    assert 'cum_avg_vol' in df_fmt.columns
    assert df_fmt.cum_avg_vol.iloc[0] == '12,598,936'
    assert df_fmt.sig_elig.iloc[0] == 'True'
    table = inspect_bar_table_string(df)
    assert 'cum_avg_vol' in table
    assert '12,598,936' in table


def test_order_inspect_display_columns_no_duplicate_session_cols() -> None:
    cols = (
        'close',
        'symbol',
        'all_filters_ok',
        'signal_eligible',
        'filter_rvol',
        'trigger_ema9_cross_above_ema21',
    )
    ordered = order_inspect_display_columns(cols)
    assert len(ordered) == len(set(ordered))
    assert ordered.count('all_filters_ok') == 1


def test_inspect_table_right_aligns_numeric_headers_with_values() -> None:
    df = pd.DataFrame({'time': ['09:00'], 'open': [301.18], 'volume': [47_940]})
    header, row = inspect_bar_table_string(df).splitlines()
    open_header_start = header.index('open')
    open_row_end = row.rindex('301.18') + len('301.18')
    assert open_header_start < open_row_end
    assert header[open_header_start: open_header_start + len('open')] == 'open'


def test_inspect_header_shortens_long_trigger_column() -> None:
    df = pd.DataFrame(
        {
            'symbol': ['AAPL'],
            'trigger_ema9_cross_above_ema21': [False],
        },
    )
    df_fmt = format_inspect_bar_table(df)
    assert 'trg_9x21' in df_fmt.columns
    table = inspect_bar_table_string(df)
    assert 'trigger_ema9_cross_above_ema21' not in table.splitlines()[0]
