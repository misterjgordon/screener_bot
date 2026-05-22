"""Massive REST ingest: OHLCV only; ``vw`` must not populate ``vwap``."""

from datetime import UTC
from datetime import datetime
from unittest.mock import patch

import pytest

from trading.integrations.massive_bars import MASSIVE_AGG_BAR_FIELDS
from trading.integrations.massive_bars import fetch_stock_minute_bars_dataframe
from trading.storage.ohlcv.ohlcv_schema import BAR_FRAME_COLUMNS

SYMBOL = 'TEST'
FETCH_START = datetime(2024, 6, 3, 14, 30, tzinfo=UTC)
FETCH_END = datetime(2024, 6, 3, 14, 31, tzinfo=UTC)
MASSIVE_VW = 999.99


def _mock_aggs_payload() -> dict[str, object]:
    """One bar with a distinct ``vw`` so accidental mapping would be obvious."""
    return {
        'status': 'OK',
        'results': [
            {
                't': 1_717_411_800_000,
                'o': 100.0,
                'h': 101.0,
                'l': 99.5,
                'c': 100.5,
                'v': 10_000,
                'vw': MASSIVE_VW,
                'n': 42,
            },
        ],
    }


def test_massive_fetch_ignores_vw_field() -> None:
    with patch(
        'trading.integrations.massive_bars._fetch_aggs_session',
        return_value=_mock_aggs_payload(),
    ), patch('trading.integrations.massive_bars._require_massive_api_key', return_value='test-key'):
        df = fetch_stock_minute_bars_dataframe(SYMBOL, FETCH_START, FETCH_END)

    vwap_all_nan = bool(df.vwap.isna().all())
    row_count = len(df)
    close_val = float(df.close.iloc[0])
    fields_read = MASSIVE_AGG_BAR_FIELDS

    print(
        '**summary for Massive fetch ignores vw:**\n'
        f'{SYMBOL} | row_count = {row_count}\n'
        f'fields_read = {fields_read}\n'
        f'massive_vw_in_fixture = {MASSIVE_VW}\n'
        f'close = {close_val}\n'
        f'vwap_all_nan = {vwap_all_nan}'
    )

    assert row_count == 1
    assert list(df.columns) == list(BAR_FRAME_COLUMNS)
    assert close_val == pytest.approx(100.5)
    assert vwap_all_nan
