"""Simple moving average of bar volume on an intraday series."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

INDICATOR_DECIMAL_PLACES = 2
DEFAULT_SMA_VOLUME_PERIOD = 20


def sma_volume_series(volume: 'pd.Series', period: int = DEFAULT_SMA_VOLUME_PERIOD) -> 'pd.Series':
    """SMA of ``volume`` over the prior ``period`` bars (current bar included in the window)."""
    if period < 1:
        msg = f'period must be >= 1, got {period}'
        raise ValueError(msg)
    return (
        volume.astype('float64')
        .rolling(window=period, min_periods=period)
        .mean()
        .round(INDICATOR_DECIMAL_PLACES)
    )
