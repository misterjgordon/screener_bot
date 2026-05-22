"""INDICATOR_REGISTRY metadata and catalog export."""

import json

from backtesting.indicators.indicator_catalog_load import default_indicator_ids
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY


def test_default_indicator_ids_registered() -> None:
    missing = [iid for iid in default_indicator_ids() if iid not in INDICATOR_REGISTRY.ids()]
    all_present = len(missing) == 0

    print(
        '**summary for default indicator registration:**\n'
        f'default_count = {len(default_indicator_ids())} | all_present = {all_present}\n'
        f'missing = {missing}'
    )

    assert all_present


def test_indicator_catalog_json_round_trip() -> None:
    raw = INDICATOR_REGISTRY.catalog_json()
    rows = json.loads(raw)
    ids = [row['id'] for row in rows]
    has_vwap = 'vwap' in ids
    has_ema9 = 'ema9' in ids
    has_condition_col = 'close_above_vwap' in ids

    print(
        '**summary for indicator catalog JSON:**\n'
        f'row_count = {len(rows)} | has_vwap = {has_vwap}\n'
        f'has_ema9 = {has_ema9} | has_close_above_vwap = {has_condition_col}'
    )

    assert has_vwap
    assert has_ema9
    assert not has_condition_col
    for row in rows:
        assert 'compute_fn' not in row
