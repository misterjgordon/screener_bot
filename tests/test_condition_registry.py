"""CONDITION_REGISTRY metadata."""

import json

from backtesting.conditions.condition_registry import CONDITION_REGISTRY


def test_condition_registry_has_vwap_conditions() -> None:
    ids = CONDITION_REGISTRY.ids()
    has_cross_up = 'trigger_vwap_cross_up' in ids
    has_close_above = 'close_above_vwap' in ids
    has_vwap_indicator = 'vwap' in ids

    print(
        '**summary for condition registry:**\n'
        f'condition_count = {len(ids)}\n'
        f'has_trigger_vwap_cross_up = {has_cross_up}\n'
        f'has_close_above_vwap = {has_close_above}\n'
        f'has_vwap_indicator_id = {has_vwap_indicator}'
    )

    assert has_cross_up
    assert has_close_above
    assert not has_vwap_indicator


def test_condition_catalog_json_round_trip() -> None:
    raw = CONDITION_REGISTRY.catalog_json()
    rows = json.loads(raw)
    kinds = {row['kind'] for row in rows}
    has_filter = 'filter' in kinds
    has_trigger = 'trigger' in kinds

    print(
        '**summary for condition catalog JSON:**\n'
        f'row_count = {len(rows)} | has_filter = {has_filter} | has_trigger = {has_trigger}'
    )

    assert has_filter
    assert has_trigger
