"""Bar range: high minus low for each bar."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def bar_range_series(high: 'pd.Series', low: 'pd.Series') -> 'pd.Series':
    """Bar range (high - low) for each bar."""
    return high - low
