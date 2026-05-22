"""SMA volume series."""

import pandas as pd
import pytest

from strategies.indicators.sma_volume import DEFAULT_SMA_VOLUME_PERIOD
from strategies.indicators.sma_volume import sma_volume_series


def test_sma_volume_series_default_period() -> None:
    volume = pd.Series([10, 20, 30, 40, 50] * 5, dtype='float64')
    out = sma_volume_series(volume, period=DEFAULT_SMA_VOLUME_PERIOD)
    first_valid = float(out.iloc[DEFAULT_SMA_VOLUME_PERIOD - 1])
    expected = float(volume.iloc[:DEFAULT_SMA_VOLUME_PERIOD].mean())

    print(
        '**summary for sma_volume_series:**\n'
        f'first_valid = {first_valid} | expected = {expected}'
    )

    assert first_valid == pytest.approx(expected)


def test_sma_volume_series_rounds_to_indicator_decimals() -> None:
    volume = pd.Series([1, 2, 3, 7], dtype='float64')
    out = sma_volume_series(volume, period=2)
    val = float(out.iloc[-1])
    assert val == round(val, 2)
